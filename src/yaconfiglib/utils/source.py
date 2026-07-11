from __future__ import annotations

import io as _io
import logging
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
            path_marker = path_marker.encode(encoding)
            newline = newline.encode(encoding)

        # Handle file streams (in-memory or real)
        if isinstance(source, _io.IOBase):
            content = source.read()
            if MemPath is not None:
                path = MemPath("stream")
                path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, str):
                    path.write_text(content, encoding=encoding)
                else:
                    path.write_bytes(content)
                yield path
            else:
                # Fallback to temp file if MemPath is not available
                mode = "w" if isinstance(content, str) else "wb"
                temp_kwargs = {"encoding": encoding} if isinstance(content, str) else {}
                with _tempfile.NamedTemporaryFile(mode=mode, delete=False, suffix=".yaml", **temp_kwargs) as tmp:
                    tmp.write(content)
                    tmp_path = Path(tmp.name)
                yield tmp_path
            continue

        elif isinstance(source, (str, Path, bytes)):
            if source in memo:
                logger.warning("ignoring duplicated file %s" % source)
                continue
            if isinstance(source, (str, bytes)) and source.startswith(path_marker):
                filename, source = source.split(newline, maxsplit=1)
                logger.debug("loading config doc from memory ...")
                filename = filename.removeprefix(path_marker)
                if isinstance(filename, bytes):
                    filename = filename.decode(encoding)
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
                    mode = "wb" if isinstance(source, bytes) else "w"
                    temp_kwargs = {"encoding": encoding} if not isinstance(source, bytes) else {}
                    with _tempfile.NamedTemporaryFile(mode=mode, delete=False, suffix=filename, **temp_kwargs) as tmp:
                        tmp.write(source)
                        tmp_path = Path(tmp.name)
                    yield tmp_path
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
            if not is_cmd and has_glob_pattern(path):
                # stdlib glob pattern fallback uses glob.glob on string paths
                if hasattr(path, "glob") and HAS_PATHLIB_NEXT:
                    yield from path.glob("", recursive=recursive)
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
            )
        else:
            raise ValueError(
                "unable to handle arg %s of type %s"
                % (
                    source,
                    type(source),
                )
            )
