"""Backend for parsing .env files (dotenv format)."""

from __future__ import annotations

import re
import typing as _ty

try:
    from pathlib_next import Path as _Path
except ImportError:
    from pathlib import Path as _Path  # type: ignore[no-redef]

from .base import ConfigBackend

__all__ = ["DotenvBackend"]

_COMMENT_RE = re.compile(r"^\s*#")
_EXPORT_RE = re.compile(r"^\s*export\s+")
_PAIR_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_QUOTED_RE = re.compile(r'^(["\'])(.*)\1$', re.DOTALL)


def _parse_value(raw: str) -> str:
    """Strip optional surrounding quotes from a dotenv value."""
    m = _QUOTED_RE.match(raw)
    if m:
        return m.group(2)
    return raw


class DotenvBackend(ConfigBackend):
    """Parses `.env` files into a flat ``{KEY: value}`` mapping.

    Supports:
    * ``KEY=value`` and ``export KEY=value`` syntax
    * Single- and double-quoted values
    * ``#`` comment lines and inline comments (outside quoted values)
    """

    PATHNAME_REGEX = re.compile(r".*\.env(\..+)?$", re.IGNORECASE)
    NAME = "dotenv"

    def __init__(self, lowercase: bool = True) -> None:
        self.lowercase = lowercase

    def load(
        self,
        path: _Path | str,
        encoding: str = None,
        path_factory: _ty.Callable[[str], _Path] = None,
        lowercase: bool | None = None,
        **_options,
    ) -> dict[str, str]:
        encoding = encoding or self.DEFAULT_ENCODING
        lowercase = self.lowercase if lowercase is None else lowercase
        if path_factory and not isinstance(path, _Path):
            path = path_factory(path)
        elif isinstance(path, str):
            path = (path_factory or self.DEFAULT_PATH_FACTORY)(path)

        result: dict[str, str] = {}
        for line in path.read_text(encoding=encoding).splitlines():
            # Skip blanks and comments
            if not line.strip() or _COMMENT_RE.match(line):
                continue
            line = _EXPORT_RE.sub("", line)
            m = _PAIR_RE.match(line)
            if m:
                key = m.group(1)
                if lowercase:
                    key = key.lower()
                result[key] = _parse_value(m.group(2))
        return result
