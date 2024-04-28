import re
import typing

import yaml
from pathlib_next import Path, Pathname

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["YamlConfig"]


class YamlConfig(ConfigBackend):
    PATHNAME_REGEX = re.compile(r".*\.((yaml)|(yml))$", re.IGNORECASE)
    DEFAULT_LOADER_CLS = yaml.SafeLoader
    PATH_FACTORY = Path

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

        return self.load(pathname, *args, **kwargs, loader=loader)

    def load(
        self,
        path: Path | str,
        encoding: str = None,
        master: yaml.Loader = None,
        loader_cls: type[yaml.Loader] = None,
        path_factory: type[Path] = None,
        configloader: type[ConfigBackend] = None,
        **options,
    ) -> object:

        if path_factory is None:
            path_factory = self.PATH_FACTORY
        if isinstance(path, str):
            path = path_factory(path)
        if master and not loader_cls:
            loader_cls = type(master)
        if loader_cls is None:
            loader_cls = self.DEFAULT_LOADER_CLS

        if configloader and not self.can_load_path(path):
            return configloader.load(path, loader_cls=loader_cls, encoding=encoding)

        loader = loader_cls(path.read_text(encoding=encoding))
        try:
            if master:
                loader.anchors = master.anchors
            data = loader.get_single_data()
            return data
        finally:
            loader.dispose()
