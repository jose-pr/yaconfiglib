import re

try:
    import tomllib as toml
except ImportError:
    import toml  # type: ignore

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["TomlConfig"]


class TomlConfig(ConfigBackend):
    """Backend for ``*.toml`` files.

    Uses the standard library :mod:`tomllib` on Python 3.11+, falling back
    to the third-party ``toml`` package on older interpreters.
    """

    PATHNAME_REGEX = re.compile(r".*\.toml$", re.IGNORECASE)

    def load(self, path: Path, encoding: str, **kwargs):
        """Parse *path* as TOML and return the resulting dict."""
        return toml.loads(path.read_text(encoding=encoding or self.DEFAULT_ENCODING))
