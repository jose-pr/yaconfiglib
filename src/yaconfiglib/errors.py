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
    "ErrorFrame",
    "load_error_types",
]


class ErrorFrame(_ty.NamedTuple):
    """One step on the way from the load call to the source that failed.

    *kind* is ``"include"``, ``"render"``, ``"command"`` or ``"merge"``;
    *source* is the file, template or command text of that step, and *line* the
    line within it where known (the line an ``!include`` is written on).
    """

    kind: str
    source: str
    line: "_ty.Optional[int]" = None


class ConfigError(Exception):
    """Base class for every error yaconfiglib raises about configuration.

    Catching this covers the library's own conditions — a cycle, a value it
    cannot interpret, a format it has no backend for — but not a parser's or the
    filesystem's errors, which keep their own types. `load_error_types`
    returns a tuple covering both.
    """

    def __str__(self) -> str:
        # super(), not BaseException: a subclass that also inherits a stdlib
        # error (CalledProcessError, TimeoutExpired) must keep that error's own
        # wording and only gain the suffix.
        return super().__str__() + _render_context(self)


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


def _render_key(key: "_ty.Sequence[_ty.Union[str, int]]") -> str:
    """``("a", "b", 0, "c")`` as ``a.b[0].c``."""
    parts: "_ty.List[str]" = []
    for segment in key:
        if isinstance(segment, int) and not isinstance(segment, bool):
            parts.append(f"[{segment}]")
        elif parts:
            parts.append(f".{segment}")
        else:
            parts.append(str(segment))
    return "".join(parts)


def _render_context(error: BaseException) -> str:
    """The ``[in ...; included from ...]`` suffix for *error*, or ``""``.

    Recomputed from the record every time, so adding a frame re-renders rather
    than appending a second copy.
    """
    parts: "_ty.List[str]" = []
    source = getattr(error, "config_source", None)
    if source and not getattr(error, "_config_source_named", False):
        parts.append(f"in {source}")
    for frame in getattr(error, "config_frames", ()) or ():
        if frame.kind == "include":
            where = f"included from {frame.source}"
            parts.append(f"{where}, line {frame.line}" if frame.line else where)
        elif frame.kind == "render":
            # A .j2 document is its own template, and saying the path twice is
            # noise; only a different template is worth naming.
            parts.append(
                "rendered"
                if frame.source == source
                else f"rendered from {frame.source}"
            )
        elif frame.kind == "command":
            parts.append(f"output of command {frame.source!r}")
        elif frame.kind == "merge":
            parts.append(f"while merging {frame.source}")
        else:
            parts.append(f"{frame.kind} {frame.source}")
    key = getattr(error, "config_key", None)
    if key:
        parts.append(f"at {_render_key(key)}")
    return f" [{'; '.join(parts)}]" if parts else ""


def _source_already_named(error: BaseException, source: str) -> bool:
    """Whether *error*'s **own** message already identifies *source*.

    PyYAML marks, INI sources and `UnsupportedFormatError` name it themselves,
    and a second copy in the suffix is noise. Decided once, when the source is
    first recorded, so every later re-render agrees.

    The suffix already written by an earlier call is removed first. Without
    that, a frame naming the same path (the ``render`` frame of a ``.j2``
    document, added before the source is known) would look like the error's own
    wording, and the name would then be dropped from both — measured.
    """
    if isinstance(error, OSError) and error.filename == source:
        return True
    try:
        text = str(error)
    except Exception:  # noqa: BLE001 - a broken __str__ must not hide the error
        return False
    written = _render_context(error)
    # Both spellings: a carrier that holds the suffix on its own line (PyYAML's
    # `note`) stores it without the leading space.
    for spelling in (written, written.strip()):
        if spelling:
            text = text.replace(spelling, "")
    if source in text:
        return True
    # A Windows path in a message written with as_posix(), or vice versa.
    return source.replace("\\", "/") in text.replace("\\", "/")


def _set_attribute(error: BaseException, name: str, value: object) -> bool:
    """Set *name* on *error*, reporting whether it took.

    An exception with ``__slots__`` (or a read-only attribute) refuses; that is
    not a failure worth raising from inside an ``except`` block.
    """
    try:
        setattr(error, name, value)
    except (AttributeError, TypeError):
        return False
    return True


def _write_context(error: BaseException) -> None:
    """Render the record into whichever field *error*'s ``__str__`` reads.

    `ConfigError` renders in its own ``__str__``, so it needs no field. For
    every other type the original text is kept the first time and the field is
    rewritten as ``original + suffix``, which keeps repeated calls idempotent.
    """
    if isinstance(error, ConfigError):
        return
    suffix = _render_context(error)
    if not suffix:
        return

    # (attribute, is-args) carriers, most specific first. UnicodeDecodeError
    # must reach `reason` before the args[0] fallback: its args are a fixed
    # 5-tuple the type re-reads.
    field: _ty.Optional[str] = None
    if isinstance(error, UnicodeError) and isinstance(
        getattr(error, "reason", None), str
    ):
        field = "reason"
    elif isinstance(error, OSError) and isinstance(error.strerror, str):
        field = "strerror"
    elif isinstance(error, _configparser.Error) and isinstance(
        getattr(error, "message", None), str
    ):
        field = "message"
    elif hasattr(error, "problem_mark") and hasattr(error, "note"):
        # Duck-typed PyYAML MarkedYAMLError, so yaml is never imported here.
        field = "note"

    if field is not None:
        original = error.__dict__.get("_config_original")
        if original is None:
            original = getattr(error, field) or ""
            error.__dict__["_config_original"] = original
        if _set_attribute(error, field, f"{original}{suffix}".lstrip()):
            return

    if _writes_args0(error):
        original = error.__dict__.get("_config_original")
        if original is None:
            original = error.args[0]
            error.__dict__["_config_original"] = original
        if _set_attribute(error, "args", (original + suffix,) + error.args[1:]):
            return

    _add_note(error, suffix.strip(" []"))


def _writes_args0(error: BaseException) -> bool:
    """Whether rewriting ``args[0]`` changes what ``str(error)`` shows."""
    if not error.args or not isinstance(error.args[0], str):
        return False
    jinja2 = _sys.modules.get("jinja2")
    template_error = getattr(getattr(jinja2, "exceptions", None), "TemplateError", None)
    if isinstance(template_error, type) and isinstance(error, template_error):
        return True
    return type(error).__str__ is BaseException.__str__


def _add_note(error: BaseException, note: str) -> None:
    """Attach *note* as a PEP 678 note, on any interpreter.

    ``add_note`` is 3.11+; below that the list is set directly, which pytest
    prints even though a plain traceback does not.
    """
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        notes = getattr(error, "__notes__", None)
        if notes and note in notes:
            return
        add_note(note)
        return
    notes = getattr(error, "__notes__", None)
    if notes is None:
        _set_attribute(error, "__notes__", [note])
    elif note not in notes:
        notes.append(note)


def _add_error_context(
    error: BaseException,
    *,
    source: "_ty.Optional[str]" = None,
    frame: "_ty.Optional[ErrorFrame]" = None,
    key: "_ty.Optional[_ty.Sequence[_ty.Union[str, int]]]" = None,
) -> BaseException:
    """Record where *error* came from, and render it into *error*'s message.

    Returns the same object, with its type and identity untouched — an
    `except yaml.YAMLError` clause and an `ignore_error` predicate see exactly
    what they saw before, plus `config_source`, `config_frames` and
    `config_key`.

    Never raises: it runs inside ``except`` blocks, where an error of its own
    would replace the one being annotated.
    """
    if source and getattr(error, "config_source", None) is None:
        # Decided BEFORE config_source is set: _source_already_named strips the
        # suffix as currently rendered, and setting the source first would
        # change that rendering (a render frame's "rendered from <path>"
        # collapses to "rendered"), leaving the old text to be mistaken for the
        # error's own.
        named = _source_already_named(error, source)
        if _set_attribute(error, "config_source", source):
            _set_attribute(error, "_config_source_named", named)
    if frame is not None:
        frames = getattr(error, "config_frames", ()) or ()
        # Innermost first: an outer include prepends nothing, it appends, since
        # it is reached later and is further out.
        _set_attribute(error, "config_frames", tuple(frames) + (frame,))
    if key and getattr(error, "config_key", None) is None:
        _set_attribute(error, "config_key", tuple(key))
    _write_context(error)
    return error


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
