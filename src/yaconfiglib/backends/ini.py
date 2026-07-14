import re
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
        path: Path,
        encoding: str = None,
        **options: object,
    ) -> object:
        """Parse *path* as INI and return a ``{section: {key: value}}`` dict.

        Args:
            path: File to parse.
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
            **options: Accepts ``ini_default_section`` — the section name
                used for :class:`~configparser.ConfigParser`'s
                ``default_section``, defaults to :attr:`DEFAULT_SECTION`.
        """
        encoding = encoding or self.DEFAULT_ENCODING

        parser_args = dict(
            default_section=options.setdefault(
                "ini_default_section", self.DEFAULT_SECTION
            )
        )

        parser = ConfigParser(**parser_args)
        parser.read_string(path.read_text(encoding=encoding), path.name)
        result = {}
        for section in parser.sections():
            d = result[section] = {}
            section_obj = parser[section]
            for key in section_obj:
                d[key] = section_obj[key]
        return result
