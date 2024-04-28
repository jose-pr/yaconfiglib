import re

try:
    import tomllib as toml
except ImportError:
    import toml

from pathlib_next import Path

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["TomlConfigLoader"]


class TomlConfigLoader(ConfigBackend):
    PATHNAME_REGEX = re.compile(r".*\.toml$", re.IGNORECASE)

    def load(self, path: Path, encoding: str, **kwargs):
        return toml.loads(path.read_text(encoding=encoding))
