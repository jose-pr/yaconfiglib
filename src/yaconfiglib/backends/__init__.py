from __future__ import annotations

from .base import ConfigBackend as ConfigBackend
from .base import _record_missing_backend
from .command import CommandBackend as CommandBackend
from .dotenv import DotenvBackend as DotenvBackend
from .env import EnvVarBackend as EnvVarBackend
from .ini import IniConfig as IniConfig
from .json import JsonConfig as JsonConfig
from .python_backend import PythonBackend as PythonBackend

#: name -> (PATHNAME_REGEX pattern, install hint). The patterns are checked
#: against the real classes by a test, since a missing module cannot be asked.
_OPTIONAL_BACKENDS = {
    "jinja2": (
        r".*\.((j2)|(jinja2))$",
        "`.j2`/`.jinja2` templates require `pip install yaconfiglib[jinja2]`",
    ),
    "toml": (
        r".*\.toml$",
        "TOML support on Python < 3.11 requires `pip install yaconfiglib[toml]`",
    ),
    "yaml": (
        r".*\.((yaml)|(yml))$",
        "YAML support requires `pip install yaconfiglib[yaml]`",
    ),
}


def _record(name: str, exc: ImportError) -> None:
    pattern, hint = _OPTIONAL_BACKENDS[name]
    _record_missing_backend(name, pattern, hint, str(exc))


try:
    from .jinja2 import Jinja2ConfigLoader as Jinja2ConfigLoader
except ImportError as exc:
    _record("jinja2", exc)

try:
    from .toml import TomlConfig as TomlConfig
except ImportError as exc:
    _record("toml", exc)

try:
    from .yaml import YamlConfig as YamlConfig
except ImportError as exc:
    _record("yaml", exc)
