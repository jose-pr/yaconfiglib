from pathlib_next import Path, Pathname, LocalPath, PosixPathname, glob
import typing

import yaml

import logging

try:
    from jinja2 import Template, Environment

    JINJA_ENV = Environment(extensions=["jinja2.ext.do"])
except ImportError:
    ...

from ..reader import Reader

_LOGGER = logging.getLogger("yaconfiglib.yaml.include")

__all__ = ["Include"]


class Include:

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
    ) -> None:
        self.path_factory = path_factory or self.DEFAULT_PATH_GENERATOR
        self.base_dir = base_dir or ""
        self.encoding = encoding or self.DEFAULT_ENCODING
        self.recursive = False if recursive is None else recursive
        self.reader_factory = reader_factory or (
            lambda path, **kwargs: Reader.get_class_by_path(path)(path, **kwargs)
        )

    def _getpath(self, path: str | Path):
        return path if isinstance(path, Path) else self.path_factory(path)

    @property
    def base_dir(self):
        return self._base_dir

    @base_dir.setter
    def base_dir(self, value: str | Path):
        self._base_dir = self._getpath(value)

    def __call__(self, loader: yaml.Loader, node: yaml.Node):
        args = ()
        kwargs = {}
        pathname: str | Pathname | typing.Sequence[str | Pathname]
        if isinstance(node, yaml.nodes.ScalarNode):
            pathname = loader.construct_scalar(node)
        elif isinstance(node, yaml.nodes.SequenceNode):
            pathname, *args = loader.construct_sequence(node, deep=True)
        elif isinstance(node, yaml.nodes.MappingNode):
            kwargs = loader.construct_mapping(node, deep=True)
            pathname = kwargs.pop("pathname")
        else:
            raise TypeError(f"Un-supported YAML node {node!r}")

        pathname = (
            [self.base_dir / path for path in pathname]
            if not isinstance(pathname, (str, Pathname))
            and isinstance(pathname, typing.Sequence)
            else self.base_dir / pathname
        )

        return self.load(loader, pathname, *args, **kwargs)

    def _load(
        self,
        loader: yaml.Loader,
        pathname: Path | typing.Sequence[Path],
        recursive: bool = None,
        encoding: str = None,
        reader: str = None,
        transform: str = None,
        **reader_args,
    ):
        encoding = encoding or self.encoding
        recursive = self.recursive if recursive is None else recursive
        reader_factory = (
            Reader.get_class_by_name(reader) if reader else self.reader_factory
        )

        paths = [pathname] if isinstance(pathname, Path) else pathname
        results = []
        for _pathname in paths:
            for path in _pathname.glob("", recursive=self.recursive):
                _LOGGER.debug(f"Loading file: {path}")

                _reader = reader_factory(
                    path,
                    encoding=self.encoding,
                    loader=loader,
                    path_factory=self.path_factory,
                    reader_factory=self.reader_factory,
                    base_dir=self.base_dir,
                    **reader_args,
                )
                value = _reader()
                if transform:
                    _globals = {}
                    code = JINJA_ENV.compile(
                        "{% do _globals.__setitem__('result', " + transform + ") %}"
                    )
                    Template.from_code(
                        JINJA_ENV, code, JINJA_ENV.make_globals(None)
                    ).render(
                        value=value,
                        _globals=_globals,
                        pathname=PosixPathname(path.as_posix()),
                    )
                    value = _globals["result"]

                results.append((path, value))

        return (
            results,
            isinstance(pathname, Pathname)
            and glob.WILCARD_PATTERN.match(pathname.as_posix()) is None,
        )

    def load(
        self,
        loader: yaml.Loader,
        pathname: Path | typing.Sequence[Path],
        recursive: bool = None,
        encoding: str = None,
        reader: str = None,
        transform: str = None,
        default: object = None,
        **reader_args,
    ):
        results, single = self._load(
            loader, pathname, recursive, encoding, reader, transform, **reader_args
        )
        if single:
            return results[0][1] if results else default
        else:
            return [result[1] for result in results]
