import re
import typing

import yaml
from pathlib_next import Path, Pathname

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["YamlConfig"]


class YamlConfig(ConfigBackend):
    PATHNAME_REGEX = re.compile(r".*\.((yaml)|(yml))$", re.IGNORECASE)
    DEFAULT_LOADER_CLS = yaml.SafeLoader
    DEFAULT_DUMPER_CLS = yaml.Dumper

    def load(
        self,
        path: Path | str,
        encoding: str = None,
        master: yaml.Loader = None,
        loader_cls: type[yaml.Loader] = None,
        path_factory: type[Path] = None,
        **options,
    ) -> object:
        encoding = encoding or self.DEFAULT_ENCODING

        if path_factory is None:
            path_factory = self.DEFAULT_PATH_FACTORY
        if isinstance(path, str):
            path = path_factory(path)
        if master and not loader_cls:
            loader_cls = type(master)
        if loader_cls is None:
            loader_cls = self.DEFAULT_LOADER_CLS

        loader = loader_cls(path.read_text(encoding=encoding))
        try:
            if master:
                loader.anchors = master.anchors
            data = loader.get_single_data()
            return data
        finally:
            loader.dispose()

    def dumps(self, data: str, dumper_cls: yaml.Dumper, **options) -> str:
        options.setdefault("Dumper", dumper_cls or self.DEFAULT_DUMPER_CLS)
        return yaml.dump(data, **options)
