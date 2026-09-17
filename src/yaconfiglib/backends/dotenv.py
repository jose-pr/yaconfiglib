"""Backend for parsing .env files (dotenv format)."""

from __future__ import annotations

import logging
import os as _os
import re
import typing as _ty

try:
    from pathlib_next import Path as _Path
except ImportError:
    from pathlib import Path as _Path  # type: ignore[no-redef]

from ..errors import ConfigValueError
from .base import ConfigBackend

__all__ = ["DotenvBackend"]

logger = logging.getLogger(__name__)

# Whitespace in this grammar is any whitespace character EXCEPT a newline. Only
# a newline ends an entry, so a form feed, U+0085 or U+2028 in the middle of a
# value is data. A bare \s would match those and cut the value short, which is
# also why str.splitlines() cannot be used to split the file.
_SPACE = r"[^\S\n]"
_ASSIGNMENT_RE = re.compile(
    rf"^{_SPACE}*(?:export{_SPACE}+)?([A-Za-z_][A-Za-z0-9_.\-]*){_SPACE}*="
)
#: The only escapes decoded inside a double-quoted value. Single-quoted values
#: are raw, so a Windows path or a literal backslash belongs in single quotes.
_DOUBLE_QUOTE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}


def _scan_quoted(text: str, start: int, quote: str) -> "tuple[str, int, bool]":
    """Read the quoted value opening at *start*.

    Returns the value, the index just past the closing quote, and whether a
    closing quote was found. The value may span newlines.
    """
    out: list[str] = []
    index = start + 1
    end = len(text)
    while index < end:
        char = text[index]
        if char == "\\":
            following = text[index + 1] if index + 1 < end else ""
            if quote == '"' and following in _DOUBLE_QUOTE_ESCAPES:
                out.append(_DOUBLE_QUOTE_ESCAPES[following])
                index += 2
                continue
            if quote == "'" and following == quote:
                # Raw, but an escaped quote still does not close the value.
                out.append(char)
                out.append(following)
                index += 2
                continue
            # Any other backslash pair keeps both characters.
            out.append(char)
            index += 1
            continue
        if char == quote:
            return "".join(out), index + 1, True
        out.append(char)
        index += 1
    return "".join(out), end, False


def _scan_unquoted(raw: str) -> str:
    """Read an unquoted value: to the end of the line, minus a trailing comment.

    Quote characters are ordinary here, so ``it's here # c`` loses only the
    comment.
    """
    for index, char in enumerate(raw):
        if char == "#" and (index == 0 or raw[index - 1].isspace()):
            return raw[:index].rstrip()
    return raw.rstrip()


class DotenvBackend(ConfigBackend):
    """Parses `.env` files into a flat ``{KEY: value}`` mapping.

    Supports:
    * ``KEY=value`` and ``export KEY=value`` syntax; keys may contain ``.``
      and ``-`` after the first character
    * Double-quoted values, which may span newlines and in which ``\\n``,
      ``\\r``, ``\\t``, ``\\"`` and ``\\\\`` are decoded
    * Single-quoted values, which may span newlines and are kept raw — use
      these for a literal backslash, such as a Windows path
    * Unquoted values, in which quote characters are ordinary
    * ``#`` comment lines and inline comments (outside quoted values)

    Only a newline ends an entry, so a form feed, vertical tab, ``\\x1c`` to
    ``\\x1e``, U+0085, U+2028 or U+2029 inside a value is kept.

    A line that cannot be parsed is skipped with a logged warning, or raises
    :class:`ValueError` when *strict* is on. An unterminated quoted value
    always raises, in either mode, because it would otherwise swallow the rest
    of the file.
    """

    # `.env`, `*.env` and staged names like `.env.development.local`, but NOT a
    # name whose final suffix belongs to another format. The lookahead sits
    # before the dot so `a.env.b.env.yaml` is rejected too.
    PATHNAME_REGEX = re.compile(
        r".*\.env((?!.*\.(ya?ml|json|toml|ini|cfg|j2|jinja2)$)\..+)?$", re.IGNORECASE
    )
    NAME = "dotenv"

    def __init__(self, lowercase: bool = True, strict: bool = False) -> None:
        self.lowercase = lowercase
        self.strict = strict

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
        path: "_ty.Union[str, _os.PathLike]",
        encoding: _ty.Optional[str] = None,
        path_factory: "_ty.Optional[_ty.Callable[[str], _os.PathLike]]" = None,
        lowercase: _ty.Optional[bool] = None,
        dotenv_strict: _ty.Optional[bool] = None,
        **_options: _ty.Any,
    ) -> dict[str, str]:
        """Parse *path* as a ``.env`` file into a flat ``{key: value}`` dict.

        Args:
            path: File to parse, either a ``Path`` or a string (converted
                via *path_factory*).
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
            path_factory: Path constructor used when *path* is a string.
            lowercase: Overrides the instance's *lowercase* for this call.
            dotenv_strict: Overrides the instance's *strict* for this call.
                When on, an unparseable line raises :class:`ValueError`
                instead of being skipped with a warning, and a file holding
                no assignment at all raises as well.

        Returns:
            A flat mapping of variable name to string value. Values are
            not coerced to other types — quoting is stripped but the
            result stays all-string, matching dotenv conventions.

        Raises:
            ValueError: On an unterminated quoted value, always; and on an
                unparseable line or an assignment-free file when *strict* is
                in effect.
        """
        lowercase = self.lowercase if lowercase is None else lowercase
        strict = self.strict if dotenv_strict is None else dotenv_strict
        path = self._coerce_path(path, path_factory)

        # read_text translates real line endings already; normalize anything
        # left so the scanner below only has to know about "\n".
        text = self._read_text(path, encoding).replace("\r\n", "\n").replace("\r", "\n")
        label = getattr(path, "name", None) or str(path)

        def reject(line: int, reason: str) -> None:
            message = f"{label}: line {line}: {reason}"
            if strict:
                raise ConfigValueError(message)
            logger.warning("%s", message)

        result: dict[str, str] = {}
        end = len(text)
        position = 0
        line = 1
        while position < end:
            eol = text.find("\n", position)
            if eol < 0:
                eol = end
            raw = text[position:eol]
            statement = raw.strip()
            if not statement or statement.startswith("#"):
                line += 1
                position = eol + 1
                continue

            match = _ASSIGNMENT_RE.match(raw)
            if match is None:
                reject(line, f"not a KEY=value assignment: {statement!r}")
                line += 1
                position = eol + 1
                continue

            key = match.group(1)
            value_at = position + match.end()
            quote = text[value_at] if value_at < end else ""
            if quote in ('"', "'"):
                value, after, closed = _scan_quoted(text, value_at, quote)
                if not closed:
                    raise ConfigValueError(
                        f"{label}: line {line}: unterminated quoted value "
                        f"for {key!r}"
                    )
                tail_eol = text.find("\n", after)
                if tail_eol < 0:
                    tail_eol = end
                tail = text[after:tail_eol].strip()
                consumed = text.count("\n", position, tail_eol)
                if tail and not tail.startswith("#"):
                    reject(line, f"text after the closing quote of {key!r}: {tail!r}")
                    line += consumed + 1
                    position = tail_eol + 1
                    continue
                line += consumed + 1
                position = tail_eol + 1
            else:
                value = _scan_unquoted(raw[match.end() :])
                line += 1
                position = eol + 1

            result[key.lower() if lowercase else key] = value

        if strict and not result:
            raise ConfigValueError(f"{label}: no KEY=value assignment found")
        return result
