import io
import logging
import typing

from pathlib_next import LocalPath, Path, Pathname, PosixPathname, glob
from pathlib_next.mempath import MemPath

try:
    from .jinja2 import jinja2_eval
except ImportError:
    ...

from .reader import Reader

_LOGGER = logging.getLogger("yaconfiglib")

__all__ = ["Include"]


class ConfigBackend(typing.Protocol):

    def load(self, path: Path, **options) -> object:
        raise NotImplementedError()

    def load_all(self, path: Path, **options) -> typing.Iterable[object]:
        yield self.load(path, **options)

    def dumps(self, data: str, **options) -> str:
        raise NotImplementedError


class ConfigLoader:

    DEFAULT_PATH_GENERATOR = LocalPath
    DEFAULT_ENCODING = "utf-8"

    def __init__(
        self,
        base_dir: str | Path = "",
        *,
        encoding: str = None,
        path_factory: typing.Callable[[str], Path] = None,
        reader_factory: type[Reader] = None,
        recursive: bool = None,
        key_factory: typing.Callable[[Path, object], str] = None,
    ) -> None:
        self.path_factory = path_factory or self.DEFAULT_PATH_GENERATOR
        self.base_dir = base_dir or ""
        self.encoding = encoding or self.DEFAULT_ENCODING
        self.recursive = False if recursive is None else recursive
        self.reader_factory = reader_factory or Reader
        self.key_factory = key_factory or (lambda path, value: path.stem)

    def _getpath(self, path: str | Path):
        return path if isinstance(path, Path) else self.path_factory(path)

    @property
    def base_dir(self):
        return self._base_dir

    @base_dir.setter
    def base_dir(self, value: str | Path):
        self._base_dir = self._getpath(value)

    def _load(
        self,
        paths: typing.Iterable[Path],
        *,
        encoding: str,
        recursive: bool = None,
        reader: str = None,
        transform: str = None,
        key_factory: str | typing.Callable[[Path], str] = None,
        **reader_args,
    ) -> typing.Iterator[tuple[str, object]]:

        recursive = self.recursive if recursive is None else recursive
        reader_factory = (
            Reader.get_class_by_name(reader) if reader else self.reader_factory
        )

        key_factory = key_factory or self.key_factory
        if not callable(key_factory):
            if key_factory.startswith("%"):
                _eval = jinja2_eval(key_factory.removeprefix("%"))

                def _key(path: Path, value):
                    return _eval(value=value, pathname=PosixPathname(path.as_posix()))

            else:
                _keyname = key_factory

                def _key(path: Path, value):
                    val = getattr(path, _keyname)
                    if callable(val):
                        val = val()
                    return str(val)

            key_factory = _key
        for _pathname in paths:
            for path in _pathname.glob("", recursive=self.recursive):
                _LOGGER.debug(f"Loading file: {path}")
                _reader = reader_factory(
                    path,
                    encoding=encoding,
                    path_factory=self.path_factory,
                    reader_factory=self.reader_factory,
                    base_dir=self.base_dir,
                    **reader_args,
                )
                value = _reader()
                if transform:
                    value = jinja2_eval(transform)(
                        value=value, pathname=PosixPathname(path.as_posix())
                    )

                yield key_factory(path, value), value

    def load(
        self,
        pathname: Path | typing.Sequence[Path],
        *,
        recursive: bool = None,
        encoding: str = None,
        reader: str = None,
        transform: str = None,
        default: object = None,
        type: str = None,
        key_factory: str | typing.Callable[[Path], str] = None,
        flatten: bool = False,
        **reader_args,
    ):
        encoding = encoding or self.encoding
        paths: list[Path] = []
        is_list = not isinstance(pathname, (str, Pathname)) and isinstance(
            pathname, typing.Iterable
        )
        for path in pathname if is_list else [pathname]:
            if isinstance(path, io.IOBase):
                filename = reader_args.get("filename", "unknown")
                content = path.read()
                path = MemPath(filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, str):
                    path.write_text(content, encoding=encoding)
                else:
                    path.write_bytes(content)
            elif isinstance(path, Path):
                try:
                    path = self.base_dir / path
                except Exception as _e:
                    ...
            else:
                path = self.base_dir / path
            paths.append(path)

        single = (
            not is_list
            or pathname
            and glob.WILCARD_PATTERN.match(paths[0].as_posix()) is None
        )
        if not is_list:
            pathname = paths[0]
        else:
            pathname = paths
        results = list(
            self._load(
                paths,
                recursive=recursive,
                encoding=encoding,
                reader=reader,
                transform=transform,
                key_factory=key_factory,
                **reader_args,
            )
        )
        if not type:
            type = "single" if single else "list"
        type = type.lower()
        match type:
            case "single" | "scalar":
                return results[-1][1] if results else default
            case "list" | "array":
                results: list[dict[str]] = [result[1] for result in results]
            case "map" | "dict" | "hash":
                results: dict[str, dict[str]] = {
                    path: result for path, result in results
                }
            case _:
                raise ValueError(type)

        if flatten:
            if type in ["list", "array"]:
                result = [r for result in results for r in result]
            else:
                result = {
                    prop: value
                    for path, result in results.items()
                    for prop, value in result.items()
                }
        else:
            result = results

        return result

    def load_all(
        self,
        pathname: Path | typing.Sequence[Path],
        **reader_args,
    ):
        return self.load(pathname, type="list", flatten=False, **reader_args)
        ...


DEFAULT_LOADER = ConfigLoader()
