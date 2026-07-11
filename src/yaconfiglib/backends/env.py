"""Backend that reads environment variables as configuration."""

from __future__ import annotations

import os
import re
import typing as _ty

try:
    from pathlib_next import Path as _Path
except ImportError:
    from pathlib import Path as _Path  # type: ignore[no-redef]

from .base import ConfigBackend

__all__ = ["EnvVarBackend"]


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
    ) -> None:
        self.prefix = prefix
        self.lowercase = lowercase

    def load(
        self,
        path: _Path | str | None = None,
        prefix: str | None = None,
        lowercase: bool | None = None,
        **_options,
    ) -> dict[str, str]:
        prefix = self.prefix if prefix is None else prefix
        lowercase = self.lowercase if lowercase is None else lowercase

        result: dict[str, str] = {}
        for key, value in os.environ.items():
            if prefix and not key.startswith(prefix):
                continue
            clean_key = key[len(prefix):]
            if lowercase:
                clean_key = clean_key.lower()
            result[clean_key] = value
        return result
