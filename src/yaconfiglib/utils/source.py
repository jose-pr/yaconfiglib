import io as _io
import typing as _ty

from pathlib_next import Path, Pathname
from pathlib_next.mempath import MemPath

from . import log as _log

SourceLike = str | Pathname | _io.IOBase | bytes


def parse_sources(
    sources: _ty.Iterable[SourceLike | _ty.Iterable[SourceLike]],
    base_dir: Path = None,
    logger: _log.Logger = None,
    encoding: str = None,
    memo: list[str | Path] = None,
    path_factory: type[Path] = None,
    recursive: bool = None,
) -> _ty.Iterator[Path]:
    logger = _log.getLogger(None)
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

        if isinstance(source, _io.IOBase):
            content = source.read()
            path = MemPath("stream")
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                path.write_text(content, encoding=encoding)
            else:
                path.write_bytes(content)
            yield path
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
                path = MemPath(filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(source, bytes):
                    path.write_bytes(source)
                else:
                    path.write_text(source, encoding=encoding)
                yield path
                continue
            elif isinstance(source, Path):
                try:
                    if base_dir:
                        path = base_dir / source
                except Exception as _e:
                    ...
            else:
                if base_dir:
                    path = base_dir / source
            path = path_factory(source) if not isinstance(source, Path) else source
            if path.has_glob_pattern():
                yield from path.glob("", recursive=recursive)
            else:
                yield path
        elif isinstance(source, _ty.Iterable):
            yield from parse_sources(
                source,
                memo=memo,
                base_dir=base_dir,
                logger=logger,
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
