import re
import typing as _ty

try:
    import tomllib as toml
except ImportError:
    import toml  # type: ignore

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["TomlConfig"]


class TomlConfig(ConfigBackend):
    """Backend for ``*.toml`` files.

    Uses the standard library :mod:`tomllib` on Python 3.11+, falling back
    to the third-party ``toml`` package on older interpreters.
    """

    PATHNAME_REGEX = re.compile(r".*\.toml$", re.IGNORECASE)

    def load(
        self,
        path: "_ty.Union[Path, str]",
        encoding: "_ty.Optional[str]" = None,
        path_factory: "_ty.Optional[_ty.Callable[[str], Path]]" = None,
        **options,
    ):
        """Parse *path* as TOML and return the resulting dict.

        Args:
            path: File to parse, either a ``Path`` or a string (converted
                via *path_factory*).
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
            path_factory: Path constructor used when *path* is a string.
        """
        path = self._coerce_path(path, path_factory)
        return toml.loads(self._read_text(path, encoding))
