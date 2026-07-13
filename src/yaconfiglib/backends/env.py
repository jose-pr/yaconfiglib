"""Backend that reads environment variables as configuration."""

from __future__ import annotations

import os
import json

try:
    from pathlib_next import Path as _Path
except ImportError:
    from pathlib import Path as _Path  # type: ignore[no-redef]

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


def _set_nested(result: dict[str, object], parts: list[str], value: object) -> None:
    current = result
    for part in parts[:-1]:
        existing = current.get(part)
        if not isinstance(existing, dict):
            existing = {}
            current[part] = existing
        current = existing
    current[parts[-1]] = value


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
        nested_delimiter: str | None = None,
        coerce: bool = False,
    ) -> None:
        self.prefix = prefix
        self.lowercase = lowercase
        self.nested_delimiter = nested_delimiter
        self.coerce = coerce

    def load(
        self,
        path: _Path | str | None = None,
        prefix: str | None = None,
        lowercase: bool | None = None,
        nested_delimiter: str | None = None,
        coerce: bool | None = None,
        **_options,
    ) -> dict[str, object]:
        prefix = self.prefix if prefix is None else prefix
        lowercase = self.lowercase if lowercase is None else lowercase
        nested_delimiter = (
            self.nested_delimiter
            if nested_delimiter is None
            else nested_delimiter
        )
        coerce = self.coerce if coerce is None else coerce

        result: dict[str, object] = {}
        for key, value in os.environ.items():
            if prefix and not key.startswith(prefix):
                continue
            clean_key = key[len(prefix):]
            if lowercase:
                clean_key = clean_key.lower()
            parsed_value = _coerce_value(value) if coerce else value
            if nested_delimiter and nested_delimiter in clean_key:
                parts = [part for part in clean_key.split(nested_delimiter) if part]
                if parts:
                    _set_nested(result, parts, parsed_value)
                continue
            result[clean_key] = parsed_value
        return result
