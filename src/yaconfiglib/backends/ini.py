import re
import typing as _ty
from configparser import ConfigParser

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["IniConfig"]


class IniConfig(ConfigBackend):
    """Backend for ``*.ini`` files, parsed via the standard library's :class:`configparser.ConfigParser`.

    Returns a ``{section_name: {key: value}}`` mapping. All values are
    strings, matching :mod:`configparser` semantics — use interpolation
    (:func:`yaconfiglib.utils.jinja2.interpolate`) or manual coercion if
    typed values are needed.
    """

    PATHNAME_REGEX = re.compile(r".*\.ini$", re.IGNORECASE)
    DEFAULT_SECTION = "DEFAULT"

    def load(
        self,
        path: "_ty.Union[Path, str]",
        encoding: "_ty.Optional[str]" = None,
        path_factory: "_ty.Optional[_ty.Callable[[str], Path]]" = None,
        **options: object,
    ) -> object:
        """Parse *path* as INI and return a ``{section: {key: value}}`` dict.

        Args:
            path: File to parse, either a ``Path`` or a string (converted
                via *path_factory*).
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
            path_factory: Path constructor used when *path* is a string.
            **options: Accepts ``ini_default_section`` — the section name
                used for :class:`~configparser.ConfigParser`'s
                ``default_section``, defaults to :attr:`DEFAULT_SECTION`.
        """
        path = self._coerce_path(path, path_factory)

        parser_args = dict(
            default_section=options.setdefault(
                "ini_default_section", self.DEFAULT_SECTION
            )
        )

        parser = ConfigParser(**parser_args)
        parser.read_string(self._read_text(path, encoding), path.name)
        result = {}
        for section in parser.sections():
            d = result[section] = {}
            section_obj = parser[section]
            for key in section_obj:
                d[key] = section_obj[key]
        return result
