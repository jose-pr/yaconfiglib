import os as _os
import re
import typing as _ty

try:
    import tomllib
except ImportError:
    # tomli is the backport of tomllib, same loads()/TOMLDecodeError API. The
    # third-party `toml` package is deliberately NOT a further fallback: it
    # implements TOML 0.5, so a transitively installed copy would silently
    # parse a TOML 1.0 document differently from 3.11+.
    import tomli as tomllib  # type: ignore[no-redef]

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["TomlConfig"]


class TomlConfig(ConfigBackend):
    """Backend for ``*.toml`` files.

    Uses the standard library :mod:`tomllib` on Python 3.11+, and its
    ``tomli`` backport (``yaconfiglib[toml]``) on 3.9/3.10, so a TOML 1.0
    document means the same thing on every supported interpreter. Note that
    ``tomli`` 2.4+ also accepts some TOML 1.1 syntax that 3.11-3.14's
    ``tomllib`` rejects, so a TOML 1.1-only file is not portable.
    """

    PATHNAME_REGEX = re.compile(r".*\.toml$", re.IGNORECASE)

    def load(
        self,
        path: "_ty.Union[str, _os.PathLike]",
        encoding: "_ty.Optional[str]" = None,
        path_factory: "_ty.Optional[_ty.Callable[[str], Path]]" = None,
        **options: _ty.Any,
    ) -> _ty.Any:
        """Parse *path* as TOML and return the resulting dict.

        Args:
            path: File to parse, either a ``Path`` or a string (converted
                via *path_factory*).
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
            path_factory: Path constructor used when *path* is a string.
        """
        path = self._coerce_path(path, path_factory)
        return tomllib.loads(self._read_text(path, encoding))
