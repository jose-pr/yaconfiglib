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


def _strip_inline_comment(raw: str) -> str:
    quote = None
    escaped = False
    for idx, char in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#" and (idx == 0 or raw[idx - 1].isspace()):
            return raw[:idx].rstrip()
    return raw


def _parse_value(raw: str) -> str:
    """Strip optional surrounding quotes from a dotenv value."""
    raw = _strip_inline_comment(raw)
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
        """Parse *path* as a ``.env`` file into a flat ``{key: value}`` dict.

        Args:
            path: File to parse, either a ``Path`` or a string (converted
                via *path_factory*).
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
            path_factory: Path constructor used when *path* is a string.
            lowercase: Overrides the instance's *lowercase* for this call.

        Returns:
            A flat mapping of variable name to string value. Values are
            not coerced to other types — quoting is stripped but the
            result stays all-string, matching dotenv conventions.
        """
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
