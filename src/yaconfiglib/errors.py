"""Exception types yaconfiglib raises for configuration conditions.

Every class here inherits `ConfigError` **and** the builtin exception the same
condition raised before, so an `except ValueError` or `except NotImplementedError`
written against an earlier version keeps matching while `except ConfigError`
becomes possible. Nothing wraps a parser's own error: a `yaml.YAMLError` or a
`FileNotFoundError` reaches the caller as itself, which is what makes
`ignore_error` predicates and `except` clauses on those types work.

For one clause covering every configuration failure, see `load_error_types`.

A leaf module: stdlib imports only, so backends and `utils.trust` can import it
without a cycle.
"""

from __future__ import annotations

import configparser as _configparser
import json as _json
import subprocess as _subprocess
import sys as _sys
import typing as _ty

__all__ = [
    "ConfigError",
    "ConfigValueError",
    "ConfigTypeError",
    "UnsupportedFormatError",
    "UnknownLoaderError",
    "CommandsDisabledError",
    "load_error_types",
]


class ConfigError(Exception):
    """Base class for every error yaconfiglib raises about configuration.

    Catching this covers the library's own conditions — a cycle, a value it
    cannot interpret, a format it has no backend for — but not a parser's or the
    filesystem's errors, which keep their own types. `load_error_types`
    returns a tuple covering both.
    """


class ConfigValueError(ConfigError, ValueError):
    """A configuration value or structure the library cannot accept."""


class ConfigTypeError(ConfigError, TypeError):
    """A configuration value of a type the requested operation cannot use."""


class UnsupportedFormatError(ConfigError, NotImplementedError):
    """No registered backend reads this source.

    Also a `NotImplementedError`, which is what
    `ConfigBackend.get_class_by_path` raised before.
    """


class UnknownLoaderError(ConfigError, ValueError):
    """``loader=`` named a backend that is not registered."""


class CommandsDisabledError(ConfigError, ValueError):
    """A command source was reached while ``allow_commands=False`` is in effect.

    Raised for a ``cmd://``/``exec://``/``sh://`` URI, a ``+fmt`` variant, or a
    script-extension file, including one reached through a nested ``!include``.
    """


#: Optional parsers, as ``(module name, attribute path to its base error)``.
#: Probed in `sys.modules` rather than imported: a parser that was never
#: imported cannot have raised.
_OPTIONAL_ERROR_BASES = (
    ("yaml", ("YAMLError",)),
    ("jinja2", ("exceptions", "TemplateError")),
    ("tomllib", ("TOMLDecodeError",)),
    ("tomli", ("TOMLDecodeError",)),
)


def load_error_types() -> "_ty.Tuple[_ty.Type[BaseException], ...]":
    """Exception types a load can raise for a configuration or I/O reason.

    Written for one `except` clause::

        try:
            config = yaconfiglib.load("app.yaml")
        except yaconfiglib.load_error_types() as error:
            ...

    Always includes `ConfigError`, `OSError`, `UnicodeError`,
    `json.JSONDecodeError`, `configparser.Error` and
    `subprocess.SubprocessError`; adds PyYAML's, Jinja2's and TOML's base errors
    for each of those modules already imported, which is every one that could
    have raised. Evaluated per call, so it reflects the backends loaded by then.

    Bare `ValueError`, `TypeError` and `KeyError` are deliberately absent: a
    library bug should still crash. yaconfiglib's own value and type errors are
    `ConfigError` subclasses, so they are covered.
    """
    types: "_ty.List[_ty.Type[BaseException]]" = [
        ConfigError,
        OSError,
        UnicodeError,
        _json.JSONDecodeError,
        _configparser.Error,
        _subprocess.SubprocessError,
    ]
    for module_name, attributes in _OPTIONAL_ERROR_BASES:
        module = _sys.modules.get(module_name)
        if module is None:
            continue
        target: _ty.Any = module
        for attribute in attributes:
            target = getattr(target, attribute, None)
            if target is None:
                break
        if isinstance(target, type) and issubclass(target, BaseException):
            types.append(target)
    return tuple(types)
