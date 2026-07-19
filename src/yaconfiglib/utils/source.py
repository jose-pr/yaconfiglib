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

_CMD_REGEX = _re.compile(r"^(exec|cmd|sh|exec\+\w+|cmd\+\w+)(://|:\\|:/|:)", _re.IGNORECASE)

#: Monotonic counter giving every stream / unnamed in-memory source a unique
#: virtual name. Without it every stream materialized to the SAME
#: ``MemPath("stream")``, so callers that resolved sources up front
#: (``list(parse_sources(...))``) saw every path holding the LAST stream's
#: content.
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


def has_glob_pattern(path: Path) -> bool:
    """Check if the given Path contains glob pattern characters."""
    if hasattr(path, "has_glob_pattern"):
        return path.has_glob_pattern()
    # Fallback checking path string representation directly (faster than path.parts)
    return _glob.has_magic(str(path))


def parse_sources(
    sources: _ty.Iterable[SourceLike | _ty.Iterable[SourceLike]],
    base_dir: Path = None,
    encoding: str = None,
    memo: list[str | Path] = None,
    path_factory: type[Path] = None,
    recursive: bool = None,
) -> _ty.Iterator[Path]:
    """Resolve *sources* into a flat stream of loadable :class:`Path`-like objects.

    Each item in *sources* may be:

    * A file path (string or ``Path``) — resolved against *base_dir* if
      relative and not a command URI, and glob-expanded if it contains
      glob magic characters.
    * A command URI (``exec://``, ``cmd://``, ``sh://``, or a ``+fmt``
      variant) — passed through unresolved and unexpanded so
      :class:`~yaconfiglib.backends.command.CommandBackend` can run it.
    * An in-memory document: a string/bytes value starting with the
      ``"#!\\n"`` marker, where the first line (after the marker) is
      treated as a virtual filename and the remainder as its content. The
      content is materialized to a ``MemPath`` (or a real temp file as a
      fallback) so downstream backends can read it like any other file.
    * An open stream (:class:`io.IOBase`) — read fully and materialized
      the same way as an in-memory document.
    * A nested iterable of any of the above — flattened recursively.

    Args:
        sources: The sources to resolve, as passed to ``ConfigLoader.load()``.
        base_dir: Directory relative file paths are joined against.
        encoding: Text encoding used when decoding bytes markers/content.
        memo: Optional list of already-seen path strings, used to detect
            and skip duplicate sources across recursive calls; extended
            in place.
        path_factory: Constructor used to build a ``Path`` from a bare
            string source.
        recursive: Whether glob expansion should recurse into
            subdirectories.

    Yields:
        Resolved :class:`Path`-like objects, one per concrete source
        (glob patterns may yield zero or many).

    Raises:
        ValueError: If an item in *sources* is not a recognized source type.
    """
    path_factory = path_factory or Path
    recursive = False if recursive is None else bool(recursive)
    if memo is None:
        memo = []
    for source in sources:
        if not source:
            continue
        path_marker = "#!"
        newline = "\n"

        if isinstance(source, bytes):
            path_marker = path_marker.encode(encoding or "utf-8")
            newline = newline.encode(encoding or "utf-8")

        # Handle file streams (in-memory or real)
        if isinstance(source, _io.IOBase):
            content = source.read()
            if MemPath is not None:
                path = MemPath(f"stream-{next(_SOURCE_COUNTER)}")
                path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, str):
                    path.write_text(content, encoding=encoding)
                else:
                    path.write_bytes(content)
                yield path
            else:
                # Fallback to temp file if MemPath is not available
                yield _materialize_temp(content, encoding, ".yaml")
            continue

        elif isinstance(source, (str, Path, bytes)):
            if isinstance(source, (str, bytes)) and source.startswith(path_marker):
                filename, source = source.split(newline, maxsplit=1)
                logger.debug("loading config doc from memory ...")
                filename = filename.removeprefix(path_marker)
                if isinstance(filename, bytes):
                    filename = filename.decode(encoding or "utf-8")
                if not filename:
                    # Unnamed in-memory docs each get a unique virtual name so
                    # two of them never share (and overwrite) one MemPath.
                    filename = f"mem-{next(_SOURCE_COUNTER)}"
                if MemPath is not None:
                    path = MemPath(filename)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if isinstance(source, bytes):
                        path.write_bytes(source)
                    else:
                        path.write_text(source, encoding=encoding)
                    yield path
                else:
                    # Fallback to temp file if MemPath is not available
                    yield _materialize_temp(source, encoding, filename)
                continue
            elif isinstance(source, Path):
                is_cmd = bool(_CMD_REGEX.match(str(source)))
                path = source
                if base_dir and not is_cmd:
                    try:
                        path = base_dir / source
                    except TypeError:
                        # base_dir type is incompatible with this source path type — use source as-is.
                        logger.debug(
                            "Cannot join base_dir %r with path %r; using path as-is",
                            base_dir,
                            source,
                        )
            else:
                is_cmd = isinstance(source, str) and bool(_CMD_REGEX.match(source))
                path = path_factory(source)
                if base_dir and not is_cmd:
                    try:
                        path = base_dir / source
                    except (TypeError, ValueError):
                        logger.debug(
                            "Cannot join base_dir %r with %r; using path_factory result",
                            base_dir,
                            source,
                        )
            if not is_cmd:
                memo_key = str(path)
                if memo_key in memo:
                    logger.warning("ignoring duplicated file %s" % path)
                    continue
                memo.append(memo_key)
            if not is_cmd and has_glob_pattern(path):
                # stdlib glob pattern fallback uses glob.glob on string paths
                if hasattr(path, "glob") and HAS_PATHLIB_NEXT:
                    try:
                        yield from path.glob("", recursive=recursive)
                    except TypeError:
                        pattern = path.name
                        parent_dir = path.parent
                        yield from parent_dir.glob(pattern)
                else:
                    # Fallback path traversal
                    # If it's a standard Path, glob is supported: path.glob(pattern)
                    # We need to separate directory from the pattern
                    pattern = path.name
                    parent_dir = path.parent
                    yield from parent_dir.glob(pattern)
            else:
                yield path
        elif isinstance(source, _ty.Iterable):
            yield from parse_sources(
                source,
                memo=memo,
                base_dir=base_dir,
                path_factory=path_factory,
                encoding=encoding,
                recursive=recursive,
            )
        else:
            raise ValueError(
                "unable to handle arg %s of type %s"
                % (
                    source,
                    type(source),
                )
            )
