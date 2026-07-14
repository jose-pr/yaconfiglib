import json
import re

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
        path: Path,
        encoding: str = None,
        json_decoder_options: dict = None,
        **options,
    ) -> object:
        """Parse *path* as JSON and return the resulting object.

        Args:
            path: File to parse.
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
            json_decoder_options: Extra keyword arguments forwarded to
                :func:`json.loads` (e.g. ``object_hook``, ``parse_float``).
        """
        encoding = encoding or self.DEFAULT_ENCODING

        return json.loads(
            path.read_text(encoding=encoding), **(json_decoder_options or {})
        )

    def dumps(self, data: str, **options) -> str:
        """Serialize *data* to a JSON string via :func:`json.dumps`."""
        return json.dumps(data, **options)
