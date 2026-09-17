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

    Useful for layering computed or in-memory configuration over files
    without writing one. ``loader=`` selects the backend for *every* source
    in a call, so load the object on its own and merge the results::

        from yaconfiglib import ConfigLoader, ConfigLoaderMergeMethod

        loader = ConfigLoader()
        base = loader.load("base.yaml")
        override = loader.load(
            loader=PythonBackend({"override_key": "override_value"})
        )
        config = ConfigLoaderMergeMethod.Deep(base, override)

    Do not combine ``loader=PythonBackend(...)`` with file sources in one
    call: the files would be read by this backend, which ignores them.
    """

    PATHNAME_REGEX = None
    NAME = "python"

    def __init__(self, data: object = None) -> None:
        self._data = data

    def load(
        self,
        path: _ty.Any = None,
        **_options: _ty.Any,
    ) -> _ty.Any:
        """Return the wrapped object, ignoring any file I/O.

        If constructed with ``data=...``, that object is always returned.
        Otherwise *path* itself is returned as-is, letting this backend
        double as a passthrough for already-parsed data.
        """
        # If called as a YAML tag constructor path will be a string/Path;
        # otherwise callers pass the data object directly via __init__.
        if self._data is not None:
            return self._data
        return path  # fallback: treat path as the data object itself
