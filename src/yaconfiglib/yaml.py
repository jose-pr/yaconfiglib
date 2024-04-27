from pathlib_next import Pathname, Path
import typing
import re
import yaml


from .loader import ConfigLoader
from .reader import Reader


__all__ = ["Include", "YamlReader"]


class Include(ConfigLoader):
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


class YamlReader(Reader):
    PATHNAME_REGEX = re.compile(r".*\.((yaml)|(yml))$", re.IGNORECASE)

    def __init__(
        self, path: Path, encoding: str, loader: yaml.Loader, **kwargs
    ) -> None:
        self.master = loader
        super().__init__(path, encoding, **kwargs)

    def __call__(self):
        loader = type(self.master)(self.read_text())
        try:
            loader.anchors = self.master.anchors
            data = loader.get_single_data()
            return data
        finally:
            loader.dispose()
