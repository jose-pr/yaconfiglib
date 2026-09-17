"""Source discovery and normalization for :class:`~yaconfiglib.loader.ConfigLoader`.

Turns the heterogeneous inputs a caller can pass to ``load()`` — file
paths, glob patterns, command URIs, open streams, in-memory ``#!``-marked
strings, and arbitrarily nested iterables of these — into a flat stream of
concrete :class:`~pathlib.Path`-like objects ready for a backend to read.
"""

from __future__ import annotations

import io as _io
import itertools as _itertools
import logging
import os as _os
import pathlib as _stdlib_pathlib
import typing as _ty
import glob as _glob
import tempfile as _tempfile
import re as _re

# pathlib-next is a REQUIRED dependency (`pyproject.toml`), so this import is
# unconditional: a missing one is an ImportError here, like any other declared
# dependency. It used to fall back to stdlib `pathlib` with a HAS_PATHLIB_NEXT
# flag, which was dead code that also answered differently — a `**` source
# silently expanded to nothing, and the suite measured 33 failures without the
# package installed.
from pathlib_next import LocalPath, Path, Pathname
from pathlib_next.mempath import MemPath

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


def _materialize_script(content: bytes, suffix: str) -> "tuple[str, str]":
    """Write *content* to a private temp directory as ``script<suffix>``.

    Returns ``(directory, file)``. Its own directory, not a shared temp file:
    a script is executed once and the caller removes the whole directory
    afterwards, so it never sits in a shared or globbed location. On POSIX the file is mode 0o700, so a ``#!`` line can
    run it directly.
    """
    directory = _tempfile.mkdtemp(prefix="yaconfiglib-script-")
    target = _os.path.join(directory, f"script{suffix}")
    with open(target, "wb") as handle:
        handle.write(content)
    if _os.name != "nt":
        _os.chmod(target, 0o700)
    return directory, target


def _encode_text(
    content: str, encoding: "_ty.Optional[str]", fallback: bool
) -> "_ty.Tuple[bytes, _ty.Optional[str]]":
    """Encode in-memory text; return the bytes and the codec to read them with.

    A `None` read codec means "whatever the call already uses". The fallback to
    UTF-8 applies only where the requested codec cannot represent the text,
    which is exactly where this used to raise.
    """
    codec = encoding or "utf-8"
    try:
        return content.encode(codec), None
    except UnicodeEncodeError:
        if not fallback:
            raise
        logger.debug(
            "in-memory text cannot be encoded as %s; storing it as utf-8", codec
        )
        return content.encode("utf-8"), "utf-8"


def _is_stream(source: object) -> bool:
    """True for a file object: anything that can `read()` its own content.

    Capability, not class. `codecs.open()`, a `SpooledTemporaryFile` before
    3.11 and a plain custom reader are file objects too, and none of them may
    be iterated as a list of sources — a file object is iterable, so that is
    what used to happen to them, one source per line.

    Text, bytes and path types are excluded first: they are sources in their
    own right, and a path object that grew a `read` method is still a path.
    """
    if isinstance(source, (str, bytes, bytearray, Path, _os.PathLike)):
        return False
    return callable(getattr(source, "read", None))


def _backend_claims(filename: str) -> bool:
    """Whether a registered backend other than `CommandBackend` claims *filename*.

    Imported inside the function: `backends/jinja2.py` imports this module at
    module level. `CommandBackend` is excluded on purpose — a file object's
    content is data, never a program to run, however the file is named.
    """
    from ..backends import CommandBackend, ConfigBackend

    try:
        klass = ConfigBackend.get_class_by_path(_stdlib_pathlib.PurePosixPath(filename))
    except NotImplementedError:
        return False
    return not (isinstance(klass, type) and issubclass(klass, CommandBackend))


def _stream_filename(stream: object) -> "_ty.Optional[str]":
    """The file name to select a stream's backend by, or None for the default.

    A stream is named after its file only when a backend recognizes that name,
    so every stream that loads as YAML today keeps doing so: `sys.stdin`
    (``<stdin>``), a gzip file (``x.yaml.gz``) and a file opened on a
    descriptor (whose `name` is an `int`) are all unclaimed.
    """
    name = getattr(stream, "name", None)
    if isinstance(name, (bytes, _os.PathLike)):
        try:
            name = _os.fsdecode(name)
        except (TypeError, ValueError):
            return None
    if not isinstance(name, str):
        return None
    basename = _os.path.basename(name.replace("\\", "/"))
    if not basename or not _backend_claims(basename):
        return None
    return basename


def _stream_origin(stream: object) -> "_ty.Optional[str]":
    """The real file *path* behind a stream, or None when it has none.

    Distinct from `_stream_filename`, which answers "which backend reads
    this" and so returns a basename: a confinement check has to compare the
    whole path, and a basename would resolve against the working directory.

    A pseudo-name (``<stdin>``, ``<string>``), a descriptor `int` and a
    nameless reader all return None. That is deliberate — such a stream has
    no location to confine, and treating ``"<stdin>"`` as a filename would
    resolve it into a cwd-relative path and refuse it.
    """
    name = getattr(stream, "name", None)
    if isinstance(name, (bytes, _os.PathLike)):
        try:
            name = _os.fsdecode(name)
        except (TypeError, ValueError):
            return None
    if not isinstance(name, str) or not name:
        return None
    if name.startswith("<") and name.endswith(">"):
        return None
    return name


def _marker_view(
    source: bytes, encoding: "_ty.Optional[str]"
) -> "_ty.Union[str, bytes]":
    """A view of *source* in which a ``#!`` marker can be recognized.

    Bytes are returned untouched when the codec spells the marker in ASCII, so
    a byte payload stays byte-exact for a backend that reads it raw. Only the
    codecs that cannot — UTF-16/32, and anything with a BOM — are decoded, and
    those are the ones that fail outright today.
    """
    codec = encoding or "utf-8"
    if "#!\n".encode(codec) == b"#!\n":
        return source
    # Every codec that lands here (utf-8-sig, UTF-16/32) already drops the BOM
    # on decode; the strip is for a codec that does not.
    return source.decode(codec).removeprefix("\ufeff")


def _materialize(filename: str, data: bytes) -> Path:
    """Store *data* under *filename* as an in-memory source."""
    path = MemPath(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _is_materialized_source(path: object) -> bool:
    """True for a source that has no meaningful directory of its own.

    An in-memory document is a ``MemPath``, whose parent is ``''`` — it is not
    part of a config tree, so includes inside it keep resolving against
    ``base_dir`` rather than against "next to itself".
    """
    return isinstance(path, MemPath)


#: Windows extended-length prefixes. `os.path.commonpath` compares components
#: literally, so `\\?\C:\srv\conf\a.yaml` against the root `C:\srv\conf` raises
#: `ValueError` ("Paths don't have the same drive") even though both name the
#: same tree. Stripping the prefix first is what keeps a legitimately-spelled
#: path acceptable (measured).
_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"
_EXTENDED_PREFIX = "\\\\?\\"


def _confinement_key(path: object) -> str:
    r"""*path* as the string the containment test compares.

    Absolute (so ``..`` is removed **before** the comparison rather than
    treated as a component), ``normcase``-folded (so a case-differing spelling
    of the same file matches on Windows and does not on POSIX, matching each
    platform's own rules), and with any ``\\?\`` prefix stripped.

    Deliberately **not** ``realpath``/``resolve()``: a symlink inside an
    allowed root was put there by whoever administers that root, so following
    the link is the intended behaviour. The trust boundary is write access to
    a root, not the filesystem.
    """
    text = str(path)
    if text.startswith(_EXTENDED_UNC_PREFIX):
        text = "\\\\" + text[len(_EXTENDED_UNC_PREFIX) :]
    elif text.startswith(_EXTENDED_PREFIX):
        text = text[len(_EXTENDED_PREFIX) :]
    return _os.path.normcase(_os.path.abspath(text))


def _confinement_kind(path: object) -> str:
    """How confinement treats *path*: ``"check"``, ``"exempt"`` or ``"remote"``.

    Decided by **type**, never by reading the text:

    * ``"exempt"`` — a `CommandSource` (governed by ``allow_commands``) or a
      materialized source (an in-memory ``#!`` document or a rendered
      template): neither has a location on disk to confine.
    * ``"check"`` — a local filesystem path, which is what every ordinary file
      source resolves to.
    * ``"remote"`` — any other path type, such as a pathlib-next ``sftp://``
      URI path. It is inside no local root by definition, so confinement
      refuses it rather than leaving a category the caller has to reason
      about. Reached only when a *path object* of that kind arrives: with the
      default ``path_factory`` (``LocalPath``) a ``scheme://…`` **string**
      becomes an ordinary relative local path and is ``"check"``-ed, because
      pathlib-next's URI paths need extras this package does not require.
    """
    if isinstance(path, CommandSource):
        return "exempt"
    if _is_materialized_source(path):
        return "exempt"
    if isinstance(path, LocalPath):
        return "check"
    if isinstance(path, Path):
        # A pathlib-next path that is not local: a URI scheme. Tested before
        # the os.PathLike fallback below, because a remote path may implement
        # __fspath__ and would otherwise be checked as if its URI text were a
        # filename.
        return "remote"
    # A str or a plain os.PathLike: a local file named the same way
    # path_factory would have named it.
    if isinstance(path, (str, _os.PathLike)):
        return "check"
    return "remote"


def _is_anchored_root(text: str) -> bool:
    r"""Whether *text* names a place rather than a place *relative to* one.

    A confinement root has to be anchored, because `os.path.abspath` silently
    resolves anything else against the working directory — turning a typo, a
    stray leading space or an empty entry into a root under the cwd, which is
    a directory an attacker may be able to write to.

    Not simply `os.path.isabs`: that is `False` for a **bare UNC share**
    (``\\host\share``) before Python 3.13, and a whole share is a perfectly
    ordinary configuration root. Its own drive test is the reliable one.
    """
    if _os.path.isabs(text):
        return True
    drive = _os.path.splitdrive(text)[0]
    return drive.startswith("\\\\") or drive.startswith("//")


def _confinement_root_key(text: str) -> str:
    r"""`_confinement_key`, plus the fix a **root** needs.

    `os.path.commonpath` treats a bare UNC share as *relative* — it is a drive
    with no root component — so a ``\\host\share`` root raised `ValueError`
    for every path inside it, which `_within_roots` then read as "not
    contained". Appending the separator makes it a rooted path, and leaves
    every other spelling untouched (measured).
    """
    key = _confinement_key(text)
    if not _os.path.splitdrive(key)[1]:
        key += _os.sep
    return key


def _within_roots(path: object, roots: "_ty.Sequence[str]") -> bool:
    """True when *path* resolves inside any one of *roots*.

    *roots* are keys from `_confinement_key`, so both sides are normalised the
    same way. An empty *roots* allows nothing: an empty allowlist means exactly
    that.

    Containment is decided with `os.path.commonpath`, never a string prefix
    test: ``/srv/confidential/x`` *starts with* ``/srv/conf`` while lying
    outside it. A `ValueError` — a different drive, or a drive/UNC mix — means
    the two paths share no common root at all, which is simply "not
    contained".
    """
    target = _confinement_key(path)
    for root in roots:
        try:
            if _os.path.commonpath([target, root]) == root:
                return True
        except ValueError:
            continue
    return False


def _rebase_include_sources(sources: _ty.Any, origin: "_ty.Optional[Path]") -> _ty.Any:
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


def _pattern_literal_base(path, glob_base, source) -> str:
    r"""The deepest directory a pattern names before its first wildcard.

    Confinement checks this **before** expanding, so a pattern pointing
    outside the allowed roots is refused without listing a single directory —
    otherwise expansion discovers real filenames outside them, and the
    refusal that follows reports one back.

    Computed from the components of the *source* — the caller's own pattern
    text — for the same reason `has_glob_pattern` is: magic characters in
    *base_dir* are literal (a directory really named ``proj [v2]``), and
    treating one as a wildcard would truncate this prefix to the parent and
    refuse a legitimate pattern.
    """
    literal = []
    for component in _caller_components(source):
        if _glob.has_magic(component):
            break
        literal.append(component)
    if glob_base is not None:
        base = str(glob_base)
    else:
        # An absolute pattern carries its own anchor, and `path` is that
        # pattern; its components are all in `literal` above.
        base = str(getattr(path, "anchor", "") or "") or _os.sep
    return _os.path.join(base, *literal) if literal else base


def has_glob_pattern(path: "_ty.Union[str, _os.PathLike]") -> bool:
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
            # DEBUG, not a warning: passing None for an absent optional layer
            # is the documented pattern, so this fires on correct code.
            logger.debug("skipping empty source %r", source)
            continue

        # Before every other branch: a file object is iterable, so without
        # this it fell through to the iterable branch and each of its LINES
        # was loaded as a path, a glob or a command.
        if _is_stream(source):
            yield source
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

        if isinstance(source, (str, bytes, Path)):
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
    if _is_stream(source):
        return "inline", (source, None)
    if isinstance(source, str):
        if source.startswith("#!"):
            return "inline", (source, "#!")
    elif isinstance(source, bytes):
        # A codec that spells "#!" in ASCII leaves the payload untouched;
        # UTF-16/32 and BOM codecs are decoded so their marker is findable.
        view = _marker_view(source, encoding)
        marker = "#!" if isinstance(view, str) else b"#!"
        if view.startswith(marker):
            return "inline", (view, marker)
        raise TypeError(
            "a bytes source must be an in-memory document starting with "
            f"{marker!r} in the loader's encoding ({encoding or 'utf-8'}); "
            "pass a path as str or Path instead"
        )

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


def _glob_error_hook(on_error, path_factory):
    """Adapt *on_error* to the hook `Path.glob(on_error=)` expects.

    Upstream calls ``hook(error)`` with ``error.filename`` naming the directory;
    returning treats that directory as empty and raising propagates. This
    package's own callback is ``(error, directory) -> bool``, because a caller
    should not have to know which attribute holds the directory, and it gets a
    path object rather than the string upstream fills in.

    The directory is remembered: pathlib-next reports the same unreadable
    directory **twice** for a ``**`` pattern (measured on 0.9.7), and a
    predicate that counts or prompts must be asked once per directory.
    """
    asked = set()

    def hook(error: OSError) -> None:
        # A parent that does not exist, or is not a directory, is not a failure
        # to report: the pattern simply matches nothing, which is what pathlib
        # does and what this package already documented.
        if isinstance(error, (FileNotFoundError, NotADirectoryError)):
            return
        filename = getattr(error, "filename", None)
        key = str(filename)
        if key in asked:
            return
        asked.add(key)
        directory = path_factory(filename) if filename else None
        if on_error(error, directory):
            logger.debug(
                "skipping directory %s during glob expansion: %s", directory, error
            )
            return
        raise error

    return hook


def _expand_pattern(
    path, glob_base, source, recursive, on_error=None, bound_loops=False
):
    """Ask pathlib-next (or the stdlib fallback) to expand one pattern.

    *on_error* is the already-adapted hook (see `_glob_error_hook`), or None to
    keep pathlib's silent skip. *bound_loops* is passed straight through.
    """
    if isinstance(path, Path):
        # The test is isinstance, not hasattr("glob"): a STDLIB path has .glob
        # too, but no `recursive` keyword, and base_dir may be one.
        if glob_base is not None:
            # Expand from the literal base, passing the caller's own pattern
            # text. Multi-segment patterns and `**` are pathlib-next's job.
            # Only a RELATIVE source can go this way: glob() rejects a
            # non-relative pattern, which is what an absolute include source is
            # after rebasing.
            return glob_base.glob(
                str(source),
                recursive=recursive,
                on_error=on_error,
                bound_loops=bound_loops,
            )
        # No base to expand from (an absolute pattern, or no base_dir):
        # glob(None) expands the pattern the path itself carries, splitting at
        # the first wildcard. Added in pathlib-next 0.9.6, which is why the
        # floor is >=0.9.6 — 0.9.4 removed the glob("") spelling for pathlib
        # parity, and on 0.9.0-0.9.3 glob(None) returns silently partial
        # matches.
        return path.glob(
            None, recursive=recursive, on_error=on_error, bound_loops=bound_loops
        )
    # A stdlib path, which a caller gets by passing `path_factory=pathlib.Path`.
    # NOT a no-pathlib-next fallback: pathlib-next is a required dependency and
    # its import is unconditional. stdlib glob takes the pattern as an argument,
    # so separate it from its directory. It has no error hook and swallows a
    # listing failure itself, so *on_error* cannot be honoured here — and
    # neither can *bound_loops*, nor `recursive`: `parent.glob(name)` cannot
    # expand ``**`` at all, so such a source expands to nothing (measured).
    return path.parent.glob(path.name)


def _materialize_inline(
    source, path_marker, encoding, *, text_fallback: bool = False
) -> "_ty.Tuple[Path, _ty.Optional[str]]":
    """Turn a stream or a ``#!`` document into a path, plus its read codec.

    The read codec is ``None`` when the bytes are in the call's own codec,
    which is the usual case; it is ``"utf-8"`` only when the requested codec
    could not represent the text and *text_fallback* allowed storing it as
    UTF-8 instead (where this used to raise `UnicodeEncodeError`).
    """
    if path_marker is None:
        content = source.read()
        if isinstance(content, (bytes, bytearray)):
            content = bytes(content)
        elif not isinstance(content, str):
            raise TypeError(
                f"{type(source).__name__}.read() returned "
                f"{type(content).__name__}; a stream source must read as str "
                "or bytes"
            )
        # A claimed file name goes in a unique directory, so .name and .stem
        # match a load by path (a merge="hash" key, a transform's
        # pathname.name). An unclaimed one keeps the .yaml default, since YAML
        # is yaconfiglib's default and every such stream parses that way today.
        counter = next(_SOURCE_COUNTER)
        basename = _stream_filename(source)
        filename = (
            f"stream-{counter}/{basename}" if basename else f"stream-{counter}.yaml"
        )
    else:
        newline = "\n" if isinstance(source, str) else b"\n"
        name, content = source.split(newline, maxsplit=1)
        logger.debug("loading config doc from memory ...")
        name = name.removeprefix(path_marker)
        if isinstance(name, bytes):
            name = name.decode(encoding or "utf-8")
        # Stripped, so "#!x.json\r" and a bare "#!\r" behave like their LF forms.
        filename = name.strip()
        if not filename:
            # Unnamed in-memory docs each get a unique virtual name so two of
            # them never share (and overwrite) one MemPath. The ``.yaml`` suffix
            # keeps backend auto-detection working for a bare ``loads("...")``.
            filename = f"mem-{next(_SOURCE_COUNTER)}.yaml"

    if isinstance(content, str):
        data, read_encoding = _encode_text(content, encoding, text_fallback)
    else:
        data, read_encoding = content, None
    return _materialize(filename, data), read_encoding


def parse_sources(
    sources: "_ty.Iterable[_ty.Union[SourceLike, _ty.Iterable[SourceLike]]]",
    base_dir: "_ty.Optional[_ty.Union[str, _os.PathLike]]" = None,
    encoding: _ty.Optional[str] = None,
    memo: "_ty.Optional[_ty.Iterable[_ty.Union[str, Path]]]" = None,
    path_factory: "_ty.Optional[_ty.Callable[[str], _os.PathLike]]" = None,
    recursive: _ty.Optional[bool] = None,
    on_error: "_ty.Optional[_ty.Callable[[OSError, _ty.Any], bool]]" = None,
    bound_loops: bool = True,
) -> _ty.Iterator[Path]:
    """Resolve *sources* into a flat stream of loadable :class:`Path`-like objects.

    See :func:`_iter_sources` for the full contract; this is that generator with
    the read-codec channel dropped, which is all a direct caller can use. In
    particular it is **strict** about in-memory text: text the requested
    *encoding* cannot represent raises `UnicodeEncodeError` rather than being
    silently stored as UTF-8, because a caller here has no way to learn that the
    codec changed.
    """
    for item, _read_encoding in _iter_sources(
        sources,
        base_dir=base_dir,
        encoding=encoding,
        memo=memo,
        path_factory=path_factory,
        recursive=recursive,
        on_error=on_error,
        bound_loops=bound_loops,
    ):
        yield item


def _iter_sources(
    sources: "_ty.Iterable[_ty.Union[SourceLike, _ty.Iterable[SourceLike]]]",
    base_dir: "_ty.Optional[_ty.Union[str, _os.PathLike]]" = None,
    encoding: _ty.Optional[str] = None,
    memo: "_ty.Optional[_ty.Iterable[_ty.Union[str, Path]]]" = None,
    path_factory: "_ty.Optional[_ty.Callable[[str], _os.PathLike]]" = None,
    recursive: _ty.Optional[bool] = None,
    on_error: "_ty.Optional[_ty.Callable[[OSError, _ty.Any], bool]]" = None,
    bound_loops: bool = True,
    *,
    text_fallback: bool = False,
    confine: "_ty.Optional[_ty.Callable[[object], bool]]" = None,
) -> "_ty.Iterator[_ty.Tuple[_ty.Any, _ty.Optional[str]]]":
    """Resolve *sources* into loadable paths, each with the codec to read it with.

    This is the full contract; :func:`parse_sources` is this generator with the
    codec channel dropped.

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
      (``"#!app.yaml\nkey: value"``), whitespace-stripped, so a CRLF marker
      line names the same file as an LF one; ``"#!\n..."`` gets a unique
      ``mem-N.yaml`` name. Backend selection uses that name. The document's
      own line endings are kept exactly as given, so a ``\r\n`` block scalar
      loads as it would from a file.
    * An open file object — anything with a callable ``read``, not just an
      `io` class, so `codecs.open()`, a `tempfile.SpooledTemporaryFile` and a
      custom reader all count. It is read once and materialized. Its backend
      follows the basename of its ``name`` when a backend other than
      :class:`~yaconfiglib.backends.command.CommandBackend` recognizes that
      name, and is YAML otherwise (``<stdin>``, ``x.yaml.gz``, a descriptor,
      an unnamed stream). The content is data: it is never read as a list of
      source paths, never run as a script however the file is named, and a
      leading ``#!`` line is content rather than a marker. Being
      materialized, an ``!include`` inside it resolves against *base_dir* —
      pass the path instead for file-relative includes.
    * A nested iterable of any of the above, flattened.

    Falsy items such as ``None`` and ``""`` are skipped (logged at DEBUG), so
    optional layers can be passed unconditionally:
    ``load("base.yaml", os.environ.get("OVERRIDE"))``.

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
        encoding: The codec bytes documents are read with, and the one
            in-memory text is stored in; UTF-8 by default, on every platform.
            Text this codec cannot represent raises `UnicodeEncodeError`
            unless *text_fallback* is set. A **bytes** document in a codec
            that does not spell ``#!`` in ASCII (UTF-16/32, ``utf-8-sig``) is
            decoded so its marker can be found; any other bytes document is
            stored byte-for-byte.
        memo: Keys already seen; updated in place. Accepts a set or any
            iterable.
        path_factory: Callable building a path from a string source.
        recursive: Whether glob expansion should recurse into
            subdirectories (``**``).
        on_error: Called as ``on_error(error, directory)`` when glob expansion
            cannot list a directory. Return `True` to skip that directory —
            the rest of the pattern still expands — or anything falsy to let
            the `OSError` propagate. Without it the directory is skipped
            silently, as pathlib does. Asked once per directory, and only on
            the pathlib-next path: the stdlib fallback has no hook.
        bound_loops: Whether each ``**`` bounds a directory **loop** — a
            Windows junction pointing at one of its own ancestors, which
            otherwise walks until the filesystem refuses the path. `True` by
            default: the unbounded walk raises `OSError` rather than
            returning, so it is not a useful default. Pass `False` for
            pathlib's own behaviour.

            A directory deliberately reachable under two names is still
            reached under both — the bound is the current **descent path**,
            not every directory seen, which is what pathlib-next 0.9.9
            changed and why the floor is ``>=0.9.9``. A POSIX directory
            **symlink** is never descended by ``**`` in the first place, so
            there is nothing to bound there. Forwarded to
            ``Path.glob(bound_loops=)``; the stdlib fallback ignores it,
            having no ``**`` to bound.
        text_fallback: Store in-memory text as UTF-8 when *encoding* cannot
            represent it, reporting that codec back, instead of raising
            `UnicodeEncodeError`.

    Yields:
        ``(item, read_encoding)`` per resolved source, in order (glob patterns
        may yield zero or many). ``read_encoding`` is ``None`` for everything
        the call's own codec already reads — files, glob matches, command
        sources, and in-memory text that encoded cleanly; only a *text_fallback*
        document names a codec of its own.

    Raises:
        ValueError: If a source is of an unsupported type. Raised while
            classifying, so it fires before any source loads.
        TypeError: If a `bytes` source is not an in-memory document — it must
            start with ``#!`` in *encoding*. A path belongs in a `str` or a
            path object.
        UnicodeEncodeError: If in-memory text cannot be represented in
            *encoding* and *text_fallback* is not set.
    """
    path_factory = path_factory or Path
    glob_error = _glob_error_hook(on_error, path_factory) if on_error else None
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
            # A stream is materialized into a MemPath, which has no location,
            # so the check downstream would see nothing to confine. Ask about
            # the file the stream is *on*, before it is read — a caller who
            # confines reads means this file too. A stream with no real name
            # (``<stdin>``, a descriptor) stays exempt.
            if confine is not None and payload[1] is None:
                origin = _stream_origin(payload[0])
                if origin is not None and confine(origin):
                    continue
            yield _materialize_inline(
                payload[0], payload[1], encoding, text_fallback=text_fallback
            )
            continue

        if kind == "command":
            # Passed through unresolved: CommandBackend runs the URI itself, and
            # a command is never deduplicated.
            yield payload[0], None
            continue

        if kind == "literal":
            path = payload[0]
            key = _dedup_key(path)
            if key in memo:
                logger.warning("ignoring duplicated file %s", path)
                continue
            memo.add(key)
            yield path, None
            continue

        path, glob_base, source = payload
        # Before expanding: expansion stats and lists real directories, so a
        # pattern aimed outside the roots would discover names there and the
        # refusal would hand one back — an oracle for whatever the caller of
        # the document can guess a prefix of.
        if confine is not None and confine(
            _pattern_literal_base(path, glob_base, source)
        ):
            continue
        matches = _expand_pattern(
            path, glob_base, source, recursive, glob_error, bound_loops
        )
        for match in _ordered_file_matches(matches):
            key = _dedup_key(match)
            if key in literal_keys:
                logger.debug("%s is also named explicitly; skipping the match", match)
                continue
            if key in memo:
                logger.debug("skipping duplicate glob match %s", match)
                continue
            # A wildcard can still leave the roots *after* a literal prefix
            # inside them (`*/../../elsewhere/*`), so each match is checked
            # here as well — with the pattern, so the refusal names what the
            # document wrote rather than what expansion found.
            if confine is not None and confine(match, source):
                continue
            memo.add(key)
            yield match, None
