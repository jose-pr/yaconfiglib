"""Backend that reads environment variables as configuration."""

from __future__ import annotations

import os
import json
import typing as _ty

# pathlib-next is a required dependency; see utils/source.py for why this
# import is unconditional.
from pathlib_next import Path as _Path

from ..errors import ConfigValueError
from .base import ConfigBackend

__all__ = ["EnvVarBackend"]

_NULL_VALUES = {"", "none", "null", "~"}
_TRUE_VALUES = {"true", "yes", "on"}
_FALSE_VALUES = {"false", "no", "off"}


def _coerce_value(value: str) -> object:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered in _NULL_VALUES:
        return None
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    if normalized[:1] in {"[", "{"}:
        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            return value
    try:
        return int(normalized, 10)
    except ValueError:
        pass
    try:
        return float(normalized)
    except ValueError:
        return value


#: Windows environment names are case-insensitive and os.environ upper-cases
#: them, so a lowercase prefix would match nothing there. A module flag rather
#: than an inline os.name test, so tests can exercise both modes anywhere.
_ENV_KEYS_CASE_INSENSITIVE = os.name == "nt"


def _has_prefix(key: str, prefix: str) -> bool:
    if _ENV_KEYS_CASE_INSENSITIVE:
        return key.upper().startswith(prefix.upper())
    return key.startswith(prefix)


def _conflict(name: str, other: str, path: "tuple[str, ...]") -> ConfigValueError:
    return ConfigValueError(
        f"environment variables {other!r} and {name!r} both define "
        f"{'.'.join(path)!r}: one is a value and the other nests keys under it. "
        "Rename or remove one of them."
    )


def _set_nested(
    result: "dict[str, object]",
    parts: "list[str]",
    value: object,
    origins: "dict[tuple[str, ...], str]",
    name: str,
) -> None:
    """Write *value* at *parts*, refusing to overwrite another variable's work.

    *origins* maps each produced key path to the variable that produced it, so
    the collision can name both. Whether a variable is a leaf is decided by its
    key path, not by its value: with ``coerce=True`` a JSON object is still a
    leaf.
    """
    current = result
    walked: "tuple[str, ...]" = ()
    for part in parts[:-1]:
        walked += (part,)
        existing = current.get(part)
        if not isinstance(existing, dict):
            if part in current:
                # A leaf from another variable sits where this one needs a parent.
                raise _conflict(name, origins.get(walked, "<unknown>"), walked)
            existing = {}
            current[part] = existing
            # Record the parent too, so the variable that created it can be
            # named when a later leaf collides with it.
            origins[walked] = name
        current = existing
    walked += (parts[-1],)
    if isinstance(current.get(parts[-1]), dict):
        # This variable is a leaf, but another one nested keys under the path.
        raise _conflict(name, origins.get(walked, "<unknown>"), walked)
    current[parts[-1]] = value
    origins[walked] = name


class EnvVarBackend(ConfigBackend):
    """Exposes ``os.environ`` (or a subset) as a configuration document.

    Parameters
    ----------
    prefix:
        When given, only variables whose names start with *prefix* are
        included. The prefix is **stripped** from the key names.
    lowercase:
        If *True* (default) convert key names to lowercase for consistency
        with YAML/TOML conventions.
    """

    PATHNAME_REGEX = None  # Not file-based; registered by name.
    NAME = "env"

    def __init__(
        self,
        prefix: str = "",
        lowercase: bool = True,
        nested_delimiter: _ty.Optional[str] = None,
        coerce: bool = False,
    ) -> None:
        self.prefix = prefix
        self.lowercase = lowercase
        self.nested_delimiter = nested_delimiter
        self.coerce = coerce

    def load(
        self,
        path: _ty.Any = None,
        prefix: _ty.Optional[str] = None,
        lowercase: _ty.Optional[bool] = None,
        nested_delimiter: _ty.Optional[str] = None,
        coerce: _ty.Optional[bool] = None,
        **_options: _ty.Any,
    ) -> dict[str, object]:
        """Snapshot ``os.environ`` (optionally filtered/coerced) into a dict.

        Args:
            path: Ignored — present only so this backend satisfies the
                :meth:`~yaconfiglib.backends.base.ConfigBackend.load`
                signature when invoked generically.
            prefix: Overrides the instance's *prefix* for this call.
            lowercase: Overrides the instance's *lowercase* for this call.
            nested_delimiter: Overrides the instance's *nested_delimiter*.
                When set, keys containing the delimiter (after prefix
                stripping) are split into nested dicts, e.g. with
                delimiter ``"__"``, ``DB__PORT=5432`` becomes
                ``{"db": {"port": 5432}}``.
            coerce: Overrides the instance's *coerce*. When True, string
                values are converted to ``None``/``bool``/``int``/
                ``float``/parsed JSON (for values starting with ``[`` or
                ``{``) where they match, otherwise left as strings.

        Returns:
            A flat or nested dict depending on *nested_delimiter*.
        """
        prefix = self.prefix if prefix is None else prefix
        lowercase = self.lowercase if lowercase is None else lowercase
        nested_delimiter = (
            self.nested_delimiter if nested_delimiter is None else nested_delimiter
        )
        coerce = self.coerce if coerce is None else coerce

        result: dict[str, object] = {}
        origins: "dict[tuple[str, ...], str]" = {}
        for key, value in os.environ.items():
            if prefix and not _has_prefix(key, prefix):
                continue
            clean_key = key[len(prefix) :]
            if lowercase:
                clean_key = clean_key.lower()
            if not clean_key:
                # A variable equal to the prefix would otherwise produce a "" key.
                continue
            parsed_value = _coerce_value(value) if coerce else value
            if nested_delimiter and nested_delimiter in clean_key:
                parts = [part for part in clean_key.split(nested_delimiter) if part]
                if parts:
                    _set_nested(result, parts, parsed_value, origins, key)
                continue
            if not nested_delimiter:
                # Without nesting every variable is a leaf, so no scalar/nested
                # collision is possible and no bookkeeping is needed. This is
                # the default path; tracking origins here measurably slowed it.
                result[clean_key] = parsed_value
                continue
            # Inlined rather than routed through _set_nested for one part.
            if isinstance(result.get(clean_key), dict):
                raise _conflict(
                    key, origins.get((clean_key,), "<unknown>"), (clean_key,)
                )
            result[clean_key] = parsed_value
            origins[(clean_key,)] = key
        return result
