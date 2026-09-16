import json
import re
import typing as _ty

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["JsonConfig"]


class JsonConfig(ConfigBackend):
    """Backend for ``*.json`` files, parsed via the standard library :mod:`json` module."""

    PATHNAME_REGEX = re.compile(r".*\.json$", re.IGNORECASE)

    def load(
        self,
        path: "_ty.Union[Path, str]",
        encoding: "_ty.Optional[str]" = None,
        json_decoder_options: dict = None,
        path_factory: "_ty.Optional[_ty.Callable[[str], Path]]" = None,
        **options,
    ) -> object:
        """Parse *path* as JSON and return the resulting object.

        Args:
            path: File to parse, either a ``Path`` or a string (converted
                via *path_factory*).
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
            json_decoder_options: Extra keyword arguments forwarded to
                :func:`json.loads` (e.g. ``object_hook``, ``parse_float``).
            path_factory: Path constructor used when *path* is a string.
        """
        path = self._coerce_path(path, path_factory)

        return json.loads(
            self._read_text(path, encoding), **(json_decoder_options or {})
        )

    def dumps(self, data: object, **options) -> str:
        """Serialize *data* to a JSON string via :func:`json.dumps`."""
        return json.dumps(data, **options)
