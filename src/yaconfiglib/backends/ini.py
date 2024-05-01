import re
from configparser import ConfigParser

from pathlib_next import Path

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["IniConfig"]


class IniConfig(ConfigBackend):
    PATHNAME_REGEX = re.compile(r".*\.ini$", re.IGNORECASE)
    DEFAULT_SECTION = "DEFAULT"

    def load(
        self,
        path: Path,
        encoding: str = None,
        **options,
    ) -> object:
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
