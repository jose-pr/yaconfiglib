from pathlib_next import Path
import yaml
import re

from ..reader import Reader

__all__ = ["YamlReader"]


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
