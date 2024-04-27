from pathlib_next import Path, Pathname, LocalPath
import typing

import yaml

import logging

from ..reader import Reader

_LOGGER = logging.getLogger("yaconfiglib.yaml.include")


class Include:

    DEFAULT_PATH_GENERATOR = LocalPath
    DEFAULT_ENCODING = "utf-8"

    def __init__(
        self,
        base_dir: str | Path = "",
        encoding: str = None,
        path_factory: typing.Callable[[str], Path] = None,
        recursive: bool = None,
    ) -> None:
        self.path_factory = path_factory or self.DEFAULT_PATH_GENERATOR
        self.base_dir = base_dir or ""
        self.encoding = encoding or self.DEFAULT_ENCODING
        self.recursive = False if recursive is None else recursive

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

        return self._load(loader, pathname, *args, **kwargs)

    def _find_reader_for_path(self, path: Path, **kwargs) -> Reader:
        return Reader.get_class_by_pathname(path)(path, **kwargs)

    def _load(
        self,
        loader: yaml.Loader,
        pathname: Path | typing.Sequence[Path],
        recursive: bool = None,
        encoding: str = None,
        reader: str = None,
        **reader_args,
    ):
        encoding = encoding or self.encoding
        recursive = self.recursive if recursive is None else recursive
        reader_factory = (
            Reader.get_class_by_name(reader) if reader else self._find_reader_for_path
        )

        paths = [pathname] if isinstance(pathname, Path) else pathname

        for pathname in paths:
            for path in pathname.glob("", recursive=self.recursive):
                _LOGGER.debug(f"Loading file: {path}")
                _reader = reader_factory(
                    path, encoding=self.encoding, loader=loader, **reader_args
                )
                return _reader()
