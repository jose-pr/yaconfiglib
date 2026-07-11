"""Backend that accepts raw Python dicts/objects directly."""

from __future__ import annotations

import typing as _ty

try:
    from pathlib_next import Path as _Path
except ImportError:
    from pathlib import Path as _Path  # type: ignore[no-redef]

from .base import ConfigBackend

__all__ = ["PythonBackend"]


class PythonBackend(ConfigBackend):
    """Wraps a plain Python object (dict, list, etc.) as a config source.

    Useful for injecting computed or in-memory configuration into a loader
    chain without writing a file::

        loader.load(
            "base.yaml",
            PythonBackend({"override_key": "override_value"}),
        )
    """

    PATHNAME_REGEX = None
    NAME = "python"

    def __init__(self, data: object = None) -> None:
        self._data = data

    def load(
        self,
        path: _Path | str | object = None,
        **_options,
    ) -> object:
        # If called as a YAML tag constructor path will be a string/Path;
        # otherwise callers pass the data object directly via __init__.
        if self._data is not None:
            return self._data
        return path  # fallback: treat path as the data object itself
