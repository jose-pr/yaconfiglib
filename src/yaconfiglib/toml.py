import re

try:
    import tomllib as toml
except ImportError:
    import toml

from pathlib_next import Path

from .reader import Reader

__all__ = ["TomlReader"]


class TomlReader(Reader):
    PATHNAME_REGEX = re.compile(r".*\.toml$", re.IGNORECASE)

    def __init__(self, path: Path, encoding: str, **kwargs) -> None:
        super().__init__(path, encoding, **kwargs)

    def __call__(self):
        return toml.loads(self.read_text())
