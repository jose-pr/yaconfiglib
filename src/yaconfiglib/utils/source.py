"""Source discovery and normalization for :class:`~yaconfiglib.loader.ConfigLoader`.

Turns the heterogeneous inputs a caller can pass to ``load()`` — file
paths, glob patterns, command URIs, open streams, in-memory ``#!``-marked
strings, and arbitrarily nested iterables of these — into a flat stream of
concrete :class:`~pathlib.Path`-like objects ready for a backend to read.
"""

from __future__ import annotations

import atexit as _atexit
import io as _io
import itertools as _itertools
import logging
import os as _os
import pathlib as _stdlib_pathlib
import typing as _ty
import glob as _glob
import tempfile as _tempfile
import re as _re

try:
    from pathlib_next import Path, Pathname
    from pathlib_next.mempath import MemPath

    HAS_PATHLIB_NEXT = True
except ImportError:
    from pathlib import Path

    Pathname = Path  # fallback
    MemPath = None  # fallback
    HAS_PATHLIB_NEXT = False

logger = logging.getLogger(__name__)

SourceLike = _ty.Union[str, _ty.Any, _io.IOBase, bytes]

#: A command source's scheme. The `:\\` and `:/` separators are deliberately
#: absent: they existed only to re-parse text a Path factory had already
#: mangled, and they made `cmd:/usr/bin/env` lose its leading slash.
_CMD_REGEX = _re.compile(r"^(exec|cmd|sh|exec\+\w+|cmd\+\w+)(://|:)", _re.IGNORECASE)


#: Monotonic counter giving every stream / unnamed in-memory source a unique
#: virtual name. Without it every stream materialized to the SAME
#: ``MemPath("stream")``, so callers that resolved sources up front
#: (``list(parse_sources(...))``) saw every path holding the LAST stream's
#: content.
class CommandSource(str):
    """A command URI, carried as the exact text the caller wrote.

    `parse_sources` yields one of these for every ``exec://``/``cmd://``/``sh://``
    source instead of a path object, because a path factory rewrites the text:
    on Windows it turns ``/`` into ``\\``, and on POSIX it collapses ``//``,
    ``/./`` and trailing slashes — which corrupts URLs, division in an inline
    script, and anything else after the scheme.

    It subclasses `str`, so ``str(source)``, logging, cycle keys and the
    `CommandsDisabledError` message are byte-identical to the text. The few
    path-like attributes callbacks use (`name`, `stem`, `suffix`, `as_posix()`)
    resolve to the full text, so a default `key_factory` or a
    ``transform="pathname.name"`` sees the command rather than a fragment.
    """

    # A str subclass may only declare empty slots; this keeps instances
    # dict-free, and every property below is derived from the text.
    __slots__ = ()

    def __new__(cls, text: str) -> "CommandSource":
        if not _CMD_REGEX.match(text):
            raise ValueError(f"not a command source: {text!r}")
        return super().__new__(cls, text)

    @property
    def _match(self):
        return _CMD_REGEX.match(self)

    @property
    def scheme(self) -> str:
        """The scheme, lower-cased: ``"cmd"``, ``"cmd+json"``, ..."""
        return self._match.group(1).lower()

    @property
    def format(self) -> "_ty.Optional[str]":
        """The ``+fmt`` part, lower-cased, or None."""
        scheme = self.scheme
        return scheme.split("+", 1)[1] if "+" in scheme else None

    @property
    def command(self) -> str:
        """Everything after the scheme separator, exactly as written."""
        return self[self._match.end() :]

    @property
    def name(self) -> str:
        return str(self)

    @property
    def stem(self) -> str:
        return str(self)

    @property
    def suffix(self) -> str:
        return ""

    def as_posix(self) -> str:
        return str(self)


_SOURCE_COUNTER = _itertools.count()

# Temp files created by the no-pathlib_next fallback (delete=False so the
# backend can re-open them). Best-effort removal at interpreter exit — they
# previously leaked one file per materialized source.
_TEMP_SOURCES: list[str] = []


def _cleanup_temp_sources() -> None:
    for name in _TEMP_SOURCES:
        try:
            _os.unlink(name)
        except OSError:
            pass


_atexit.register(_cleanup_temp_sources)


def _materialize_temp(content: str | bytes, encoding: str, suffix: str) -> Path:
    """Write *content* to a tracked temp file and return its Path.

    The suffix is reduced to a basename (separators stripped) so a virtual
    filename from an in-memory ``#!`` marker line can never steer the temp
    file outside the temp directory.
    """
    mode = "w" if isinstance(content, str) else "wb"
    kwargs = {"encoding": encoding} if isinstance(content, str) else {}
    safe_suffix = _os.path.basename(str(suffix).replace("\\", "/")) if suffix else ""
    with _tempfile.NamedTemporaryFile(
        mode=mode, delete=False, suffix="-" + (safe_suffix or "source.yaml"), **kwargs
    ) as tmp:
        tmp.write(content)
        name = tmp.name
    _TEMP_SOURCES.append(name)
    return Path(name)


def _is_materialized_source(path: object) -> bool:
    """True for a source that has no meaningful directory of its own.

    In-memory documents (``MemPath``) and the temp files the no-pathlib_next
    fallback writes are not part of a config tree: ``MemPath``'s parent is ``''``
    and a temp file's is the system temp directory. Includes inside them keep
    resolving against ``base_dir``. Module globals are read at call time because
    tests monkeypatch ``MemPath``.
    """
    if MemPath is not None and isinstance(path, MemPath):
        return True
    return str(path) in _TEMP_SOURCES


def _rebase_include_sources(sources: object, origin: Path | None) -> object:
    """Resolve include *sources* against the directory of *origin*, keeping their shape.

    Relative paths inside a configuration file are written relative to that file.
    Returns *sources* unchanged when there is no usable origin, and leaves
    non-paths alone: in-memory ``#!`` documents, command URIs and non-strings.
    """
    if origin is None or _is_materialized_source(origin):
        return sources
    return _rebase(sources, origin)


def _rebase(source: object, origin: Path) -> object:
    if isinstance(source, (list, tuple)):
        return type(source)(_rebase(item, origin) for item in source)
    if not isinstance(source, str) or not source:
        return source
    if source.startswith("#!") or _CMD_REGEX.match(source):
        return source
    target = origin.parent / source  # an absolute source wins by join semantics
    if not target.is_absolute():
        # parse_sources joins base_dir onto any relative source; make the target
        # absolute so that join is a no-op instead of a second, wrong prefix.
        target = target.absolute()
    return str(target)


def _caller_components(path) -> "list[str]":
    r"""The components of *path* that may hold pattern text: the anchor excluded.

    An anchor is path syntax, never a pattern: the ``?`` in an extended-length
    ``\\?\C:\...`` prefix does not make that path a glob.
    """
    segments = getattr(path, "segments", None)
    if segments is None:
        if not isinstance(path, _stdlib_pathlib.PurePath):
            # str, bytes, or any os.PathLike without segments/parts.
            path = _stdlib_pathlib.PurePath(_os.fsdecode(_os.fspath(path)))
        components = [str(part) for part in path.parts]
    else:
        components = [str(segment) for segment in segments]
    anchor = str(getattr(path, "anchor", "") or "")
    if anchor and components and components[0] == anchor:
        del components[0]
    return components


def has_glob_pattern(path: Path) -> bool:
    """Check whether *path* holds glob pattern characters outside its anchor.

    Accepts a ``str``, a pathlib-next path, or any other ``os.PathLike``.

    The check is done here for every path type rather than delegated to
    pathlib-next's own ``has_glob_pattern()``, because that one scans the anchor
    too and so calls every extended-length path a pattern (reported upstream).
    Delegate again once that is fixed.
    """
    # Two regex scans at most, and one for the common literal source: no magic
    # anywhere means no magic outside the anchor either. Splitting into
    # components here cost ~3x on the has_glob_pattern benchmark.
    text = str(path)
    if not _glob.has_magic(text):
        return False
    anchor = getattr(path, "anchor", None)
    if anchor is None:
        # A str, bytes or plain os.PathLike: read the anchor once, only now that
        # magic is known to be present.
        anchor = _stdlib_pathlib.PurePath(_os.fsdecode(_os.fspath(path))).anchor
    anchor = str(anchor or "")
    if anchor and text.startswith(anchor):
        text = text[len(anchor) :]
    return _glob.has_magic(text)


def _is_relative_source(path) -> bool:
    """Can this source be expanded as a pattern relative to a base?"""
    if hasattr(path, "is_absolute"):
        return not path.is_absolute()
    return not str(getattr(path, "anchor", "") or "")


def _component_key(path) -> "tuple[str, ...]":
    """Sort key: the path's own components, compared by code point."""
    return tuple(_caller_components(path))


def _ordered_file_matches(matches) -> "list[Path]":
    """Drop directory matches, then order the rest deterministically.

    Ordering is yaconfiglib's contract, not glob's: ``load()`` merges in the
    order it receives, so the same tree must layer the same way on every
    filesystem. Directories are dropped because no backend can read one, while
    glob is right to return them for a pattern like ``envs/*``.
    """
    files = []
    for match in matches:
        try:
            if match.is_dir():
                logger.debug("skipping directory match %s", match)
                continue
        except OSError:
            # Unreadable or vanished between listing and stat: let the backend
            # report it, the way a named source would.
            pass
        files.append(match)
    files.sort(key=_component_key)
    return files


def _dedup_key(path) -> str:
    """The key that answers "have we already loaded this file?".

    Lexical and I/O-free: it collapses ``./``, ``..``, separator style and, on
    Windows, case — so ``conf/app.json``, ``./conf/app.json`` and
    ``conf/App.json`` are one file. Deliberately NOT ``resolve()``: that would
    stat every source, and it would merge two symlinked names a caller may have
    meant to keep distinct.
    """
    if isinstance(path, _stdlib_pathlib.PurePath):
        return _os.path.normcase(_os.path.abspath(_os.fspath(path)))
    # MemPath, remote paths: no filesystem to normalize against.
    return str(path)


def _flatten_sources(sources, encoding):
    """Yield each source once, recursing into nested iterables.

    Streams and in-memory documents are passed through untouched — they are
    materialized later, when the yield loop reaches them, so ordering and
    single-read semantics are preserved.
    """
    for source in sources:
        if not source:
            continue

        # An os.PathLike that is NOT a pathlib-next path (a pathlib.Path, a
        # PurePath, anything else with __fspath__) becomes the string it spells,
        # so it gets the same command check, path_factory, base_dir join, memo
        # and glob handling a caller would get by passing that string.
        # pathlib-next paths keep their own branch on purpose: MemPath is an
        # os.PathLike whose __fspath__ raises NotImplementedError.
        # os.fsdecode, not encoding=: path bytes use the filesystem encoding,
        # while encoding= describes file *content*.
        if isinstance(source, _os.PathLike) and not isinstance(
            source, (str, bytes, Path)
        ):
            source = _os.fsdecode(_os.fspath(source))

        if isinstance(source, (_io.IOBase, str, bytes, Path)):
            yield source
        elif isinstance(source, _ty.Iterable):
            yield from _flatten_sources(source, encoding)
        else:
            # Raised while classifying, so an unsupported source fails before
            # any source loads rather than after the earlier ones.
            raise ValueError(
                "unable to handle arg %s of type %s"
                % (
                    source,
                    type(source),
                )
            )


def _classify_source(source, base_dir, encoding, path_factory, recursive):
    """Decide what a source *is*, without reading or expanding anything.

    Returns ``(kind, payload)`` where kind is:

    * ``"inline"`` — a stream or a ``#!`` document, materialized on yield;
    * ``"command"`` — a command URI, passed through untouched;
    * ``"literal"`` — one concrete path;
    * ``"pattern"`` — a glob, expanded on yield.
    """
    path_marker = "#!"
    if isinstance(source, bytes):
        path_marker = path_marker.encode(encoding or "utf-8")

    if isinstance(source, _io.IOBase):
        return "inline", (source, None)
    if isinstance(source, (str, bytes)) and source.startswith(path_marker):
        return "inline", (source, path_marker)

    # A command is recognized ONLY from string text, and before any path
    # factory can rewrite it. A Path object is always a file, however its text
    # reads: a data file really named "sh:hosts.json" must load by its
    # extension, not run as a program.
    if isinstance(source, str) and _CMD_REGEX.match(source):
        return "command", (CommandSource(source),)

    glob_base = None
    if isinstance(source, Path):
        path = source
        if base_dir:
            was_relative = _is_relative_source(source)
            try:
                path = base_dir / source
                if was_relative:
                    glob_base = base_dir
            except TypeError:
                # base_dir type is incompatible with this source path type — use
                # source as-is.
                logger.debug(
                    "Cannot join base_dir %r with path %r; using path as-is",
                    base_dir,
                    source,
                )
    else:
        path = path_factory(source)
        if base_dir:
            was_relative = _is_relative_source(path)
            try:
                path = base_dir / source
                if was_relative:
                    glob_base = base_dir
            except (TypeError, ValueError):
                logger.debug(
                    "Cannot join base_dir %r with %r; using path_factory result",
                    base_dir,
                    source,
                )

    # Classified on the SOURCE, not the joined path: only what the caller wrote
    # can be pattern text. A base_dir named "proj [v2]" would otherwise turn
    # every source under it into a pattern.
    if has_glob_pattern(source) and not path.exists():
        return "pattern", (path, glob_base, source)

    # Either a plain path, or pattern text naming a real file: a file really
    # named "z[1].json" is what the caller meant, and it wins its own position
    # over any glob match, like every other explicit name.
    return "literal", (path,)


def _expand_pattern(path, glob_base, source, recursive):
    """Ask pathlib-next (or the stdlib fallback) to expand one pattern."""
    if HAS_PATHLIB_NEXT and isinstance(path, Path):
        # The test is isinstance, not hasattr("glob"): a STDLIB path has .glob
        # too, but no `recursive` keyword, and base_dir may be one.
        if glob_base is not None:
            # Expand from the literal base, passing the caller's own pattern
            # text. Multi-segment patterns and `**` are pathlib-next's job.
            # Only a RELATIVE source can go this way: glob() rejects a
            # non-relative pattern, which is what an absolute include source is
            # after rebasing.
            return glob_base.glob(str(source), recursive=recursive)
        # No base to expand from (an absolute pattern, or no base_dir):
        # glob(None) expands the pattern the path itself carries, splitting at
        # the first wildcard. Added in pathlib-next 0.9.6, which is why the
        # floor is >=0.9.6 — 0.9.4 removed the glob("") spelling for pathlib
        # parity, and on 0.9.0-0.9.3 glob(None) returns silently partial
        # matches.
        return path.glob(None, recursive=recursive)
    # Fallback path traversal: stdlib glob takes the pattern as an argument, so
    # separate it from its directory.
    return path.parent.glob(path.name)


def _materialize_inline(source, path_marker, encoding):
    """Turn a stream or a ``#!`` document into a loadable path."""
    if path_marker is None:
        content = source.read()
        if MemPath is not None:
            # Unique name + default .yaml suffix so backend auto-detection
            # works for an anonymous stream (YAML is yaconfiglib's default).
            path = MemPath(f"stream-{next(_SOURCE_COUNTER)}.yaml")
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                path.write_text(content, encoding=encoding)
            else:
                path.write_bytes(content)
            return path
        # Fallback to temp file if MemPath is not available
        return _materialize_temp(content, encoding, ".yaml")

    newline = "\n"
    if isinstance(source, bytes):
        newline = newline.encode(encoding or "utf-8")
    filename, content = source.split(newline, maxsplit=1)
    logger.debug("loading config doc from memory ...")
    filename = filename.removeprefix(path_marker)
    if isinstance(filename, bytes):
        filename = filename.decode(encoding or "utf-8")
    if not filename:
        # Unnamed in-memory docs each get a unique virtual name so two of them
        # never share (and overwrite) one MemPath. The ``.yaml`` suffix keeps
        # backend auto-detection working for a bare ``loads("...")`` (YAML is
        # yaconfiglib's default).
        filename = f"mem-{next(_SOURCE_COUNTER)}.yaml"
    if MemPath is not None:
        path = MemPath(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding=encoding)
        return path
    # Fallback to temp file if MemPath is not available
    return _materialize_temp(content, encoding, filename)


def parse_sources(
    sources: _ty.Iterable[SourceLike | _ty.Iterable[SourceLike]],
    base_dir: Path = None,
    encoding: str = None,
    memo: _ty.Iterable[str | Path] = None,
    path_factory: type[Path] = None,
    recursive: bool = None,
) -> _ty.Iterator[Path]:
    """Resolve *sources* into a flat stream of loadable :class:`Path`-like objects.

    Each item in *sources* may be:

    * A file path: a ``str``, a pathlib-next path, or any other
      ``os.PathLike`` such as :class:`pathlib.Path` — resolved against
      *base_dir* if relative and not a command URI, and glob-expanded if it
      contains glob magic characters.
    * A command URI (``exec://``, ``cmd://``, ``sh://``, or a ``+fmt``
      variant) — yielded as a :class:`CommandSource`, the text exactly as
      written, so :class:`~yaconfiglib.backends.command.CommandBackend` can run
      it. Only a **string** source can be a command: a path object is always a
      file, whatever its text looks like.
    * An in-memory document: a string/bytes value whose first line starts
      with ``#!``. The rest of that line is a virtual filename
      (``"#!app.yaml\nkey: value"``); ``"#!\n..."`` gets a unique
      ``mem-N.yaml`` name. Backend selection uses that name.
    * An open stream — read once and materialized under a unique
      ``stream-N.yaml`` name.
    * A nested iterable of any of the above, flattened.

    Glob expansion belongs to the path type (pathlib-next, or the stdlib
    fallback). What this function adds: a pattern is recognized by the
    *caller's own* components, so glob characters in *base_dir* or in a
    Windows extended-length anchor are literal; a pattern that names an
    existing path is loaded as that path; directory matches are dropped, since
    no backend reads a directory; and matches are sorted by path component, so
    a layered set of files merges in the same order everywhere.

    Duplicates are dropped by a lexical key (absolute, normalized, case-folded
    on Windows), so the same file named two ways loads once. A file named
    explicitly anywhere in the call wins over a glob match for it, whichever
    comes first, and keeps its own position.

    Args:
        sources: Items to resolve, in order.
        base_dir: Directory relative sources resolve against.
        encoding: Text encoding for in-memory documents and streams.
        memo: Keys already seen; updated in place. Accepts a set or any
            iterable.
        path_factory: Callable building a path from a string source.
        recursive: Whether glob expansion should recurse into
            subdirectories (``**``).

    Yields:
        One path per resolved source, in order
        (glob patterns may yield zero or many).

    Raises:
        ValueError: If a source is of an unsupported type. Raised while
            classifying, so it fires before any source loads.
    """
    path_factory = path_factory or Path
    if memo is None:
        memo = set()
    elif not isinstance(memo, set):
        memo = set(memo)

    # Classify everything first: a glob match must lose to a file named
    # explicitly ANYWHERE in the call, including later on, so that both layering
    # idioms work -- load("base.yaml", "*.yaml") and load("*.yaml", "local.yaml").
    items = [
        _classify_source(source, base_dir, encoding, path_factory, recursive)
        for source in _flatten_sources(sources, encoding)
    ]
    literal_keys = {
        _dedup_key(payload[0]) for kind, payload in items if kind == "literal"
    }

    for kind, payload in items:
        if kind == "inline":
            yield _materialize_inline(payload[0], payload[1], encoding)
            continue

        if kind == "command":
            # Passed through unresolved: CommandBackend runs the URI itself, and
            # a command is never deduplicated.
            yield payload[0]
            continue

        if kind == "literal":
            path = payload[0]
            key = _dedup_key(path)
            if key in memo:
                logger.warning("ignoring duplicated file %s", path)
                continue
            memo.add(key)
            yield path
            continue

        path, glob_base, source = payload
        matches = _expand_pattern(path, glob_base, source, recursive)
        for match in _ordered_file_matches(matches):
            key = _dedup_key(match)
            if key in literal_keys:
                logger.debug("%s is also named explicitly; skipping the match", match)
                continue
            if key in memo:
                logger.debug("skipping duplicate glob match %s", match)
                continue
            memo.add(key)
            yield match
