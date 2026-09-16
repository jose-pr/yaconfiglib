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

    # `.env`, `*.env` and staged names like `.env.development.local`, but NOT a
    # name whose final suffix belongs to another format. The lookahead sits
    # before the dot so `a.env.b.env.yaml` is rejected too.
    PATHNAME_REGEX = re.compile(
        r".*\.env((?!.*\.(ya?ml|json|toml|ini|cfg|j2|jinja2)$)\..+)?$", re.IGNORECASE
    )
    NAME = "dotenv"

    def __init__(self, lowercase: bool = True) -> None:
        self.lowercase = lowercase

    @classmethod
    def can_load_path(cls, path) -> bool:
        """Claim *path* only when no other backend also claims it.

        The suffix list in :attr:`PATHNAME_REGEX` cannot know about a custom
        backend, and it over-claims when an optional dependency is missing and
        that format's backend was never registered. Giving way to any
        non-dotenv backend that matches covers both.
        """
        if cls.PATHNAME_REGEX is None or cls.PATHNAME_REGEX.match(path.name) is None:
            return False
        return not any(
            scls.can_load_path(path)
            for scls in ConfigBackend.__subclasses__(recursive=True)
            # `is not cls` would make a user subclass and this class ask each
            # other forever.
            if not issubclass(scls, DotenvBackend)
        )

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
        lowercase = self.lowercase if lowercase is None else lowercase
        path = self._coerce_path(path, path_factory)

        result: dict[str, str] = {}
        for line in self._read_text(path, encoding).splitlines():
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
