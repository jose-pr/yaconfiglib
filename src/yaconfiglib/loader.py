from __future__ import annotations

import codecs
import contextlib
import contextvars
import copy as _copy
import dataclasses
import inspect
import logging
import os
import sys
import types
import typing
import warnings

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

from pathlib import PurePosixPath

try:
    from .utils import jinja2

    _JINJA2_IMPORT_ERROR: "typing.Optional[ImportError]" = None
except ImportError as exc:  # Jinja2 missing, or installed but unimportable
    jinja2 = None
    # Kept so the error can say WHY, not just that the extra is missing: a
    # Jinja2 2.x beside MarkupSafe 2.1+ fails on `soft_unicode`, which looks
    # nothing like "not installed".
    _JINJA2_IMPORT_ERROR = exc

from .backends import ConfigBackend
from .backends.command import CommandBackend
from .errors import (
    ConfigError,
    ConfigTypeError,
    ConfigValueError,
    ConfinementError,
    ErrorFrame,
    UnknownLoaderError,
    UnsupportedFormatError,
    _add_error_context,
    load_error_types,
)
from .utils.enum import IntEnum
from .utils.merge import Merge, MergeMethod, is_array
from .utils.source import (
    CommandSource,
    SourceLike,
    _backend_claims,
    _confinement_key,
    _confinement_kind,
    _iter_sources,
    _marker_view,
    _within_roots,
)
from .utils.trust import (
    CommandsDisabledError,
    current_policy,
    is_hardened,
    use_policy,
)

__all__ = [
    "ConfigLoader",
    "ConfigLoaderMergeMethod",
    "CommandsDisabledError",
    "DotAccessibleDict",
    "load",
    "loads",
    "load_as",
    "dump",
    "dumps",
]

logger = logging.getLogger(__name__)

T = typing.TypeVar("T")

#: What a source resolves to before a backend reads it: a path object, or — for
#: a command URI — the command text itself, which `parse_sources` yields as a
#: `CommandSource`. `key_factory` and `loader_factory` are called with this.
_SourcePath = typing.Union[Path, CommandSource]

#: Anything with `__fspath__`, so a stdlib `pathlib.Path` and a pathlib-next
#: path are both accepted, as the runtime already does.
_PathLike = typing.Union[str, "os.PathLike[str]"]


class _ConfigLoaderMergeMethod(IntEnum):
    """Loader-specific merge strategies layered on top of :class:`~yaconfiglib.utils.merge.MergeMethod`.

    * **Last** — each new source simply replaces the running result.
    * **List** — sources are collected into a list, one entry per source,
      in load order.
    * **Hash** — sources are collected into a dict keyed by each source's
      merge key (see ``key_factory``). Two sources with the same key do not
      both survive: the later document replaces the earlier one, and a
      warning is logged.

    Exposed to callers as :class:`ConfigLoaderMergeMethod`, which extends
    :class:`~yaconfiglib.utils.merge.MergeMethod` with these three values
    in addition to ``Simple``/``Deep``/``Substitute``.
    """

    Last = 4
    List = 5
    Hash = 6

    def init(
        self,
        initial: object,
        configloaderkey: str,
        memo: "typing.Optional[dict]" = None,
        **options: typing.Any,
    ):
        """Seed the running result from the first source's document.

        Dispatches by member name to an optional ``_init_<name>`` hook, the way
        ``__call__`` dispatches to ``_<name>``; a member without one starts from
        the document itself. Identity checks against this module's enum would
        break on every :meth:`~yaconfiglib.utils.enum.IntEnum.extend`, which is
        what makes `List`/`Hash` work on an extended enum.
        """
        hook = getattr(self, f"_init_{self.name.lower()}", None)
        if hook is None:
            return initial
        return hook(initial, configloaderkey)

    def _init_list(self, initial: object, configloaderkey: str):
        return [initial]

    def _init_hash(self, initial: object, configloaderkey: str):
        return {configloaderkey: initial}

    def _last(
        self,
        a: object,
        b: object,
        *,
        configloaderkey: str,
        memo: "typing.Optional[dict]" = None,
        **options: typing.Any,
    ):
        return b

    def _list(
        self,
        a: list,
        b: object,
        *,
        configloaderkey: str,
        memo: "typing.Optional[dict]" = None,
        **options: typing.Any,
    ):
        a.append(b)
        return a

    def _hash(
        self,
        a: dict,
        b: object,
        *,
        configloaderkey: str,
        memo: "typing.Optional[dict]" = None,
        **options: typing.Any,
    ):
        if configloaderkey in a:
            # The default key is the filename stem, so a directory glob like
            # services/*/config.yaml gives every source the key "config" and
            # keeps only the last. Warning, not raising: overriding a key on
            # purpose is part of the documented design.
            logger.warning(
                "Hash merge: key %r is produced by more than one source; the later "
                "document replaces the earlier one (set key_factory to keep both)",
                configloaderkey,
            )
        a[configloaderkey] = b
        return a


if typing.TYPE_CHECKING:
    # What a type checker reads. The runtime class is built by
    # `MergeMethod.extend(...)`, which returns `type[IntEnum]` — so a checker
    # saw no members at all and `ConfigLoaderMergeMethod.Deep` was an error.
    # A member-bearing enum cannot be subclassed, at runtime or statically,
    # so the members are restated here and pinned against the runtime enum by
    # `TestMergeTypingSurface.test_merge_method_shim_matches_runtime_enum`.

    class ConfigLoaderMergeMethod(IntEnum):
        Simple = 1
        Deep = 2
        Substitute = 3
        Last = 4
        List = 5
        Hash = 6

        def __call__(
            self,
            a: typing.Any,
            b: typing.Any,
            *,
            memo: "typing.Optional[dict]" = None,
            **options: typing.Any,
        ) -> typing.Any: ...

        def init(
            self,
            initial: object,
            configloaderkey: str,
            memo: "typing.Optional[dict]" = None,
            **options: typing.Any,
        ): ...

else:
    ConfigLoaderMergeMethod = MergeMethod.extend(
        _ConfigLoaderMergeMethod,
        name=_ConfigLoaderMergeMethod.__name__.removeprefix("_"),
    )


#: str(path) of every source whose backend load() is on the current call stack,
#: outermost first; used to detect include cycles.
_LOAD_CHAIN: "contextvars.ContextVar[typing.Tuple[str, ...]]" = contextvars.ContextVar(
    "yaconfiglib_load_chain", default=()
)


def _add_encoding_hint(error: BaseException, encoding: typing.Optional[str]) -> None:
    """Tell a decode failure which ``encoding=`` would have worked.

    The codec is a load argument, so "invalid start byte" is actionable only
    once the message says which codec was used and names a likely one. A
    UTF-16 byte-order mark is recognizable, so it is named outright.
    """
    if not isinstance(error, UnicodeDecodeError):
        return
    data = error.object if isinstance(error.object, (bytes, bytearray)) else b""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        suggestion = "encoding='utf-16'"
    else:
        suggestion = "pass encoding=... naming the file's codec"
    reason = getattr(error, "reason", "")
    marker = f" (read as {encoding or 'utf-8'}; {suggestion})"
    if marker not in reason:
        error.reason = f"{reason}{marker}"


#: Where `ConfigLoader(confine_to=None)` looks for roots instead. Read only
#: when the argument is None: an environment variable that could widen an
#: explicit in-code allowlist would be a privilege escalation available to
#: anyone who can set the environment.
CONFINE_TO_ENV = "YACONFIGLIB_CONFINE_TO"


def _resolve_confine_to(
    confine_to: "typing.Optional[typing.Union[bool, str, typing.Sequence[_PathLike]]]",
) -> "typing.Union[None, bool, typing.Tuple[str, ...]]":
    """The roots *confine_to* names, normalised for comparison.

    Returns `None` for "no confinement", `True` for "take ``base_dir`` at
    check time" (``base_dir`` has a setter, so it can change after
    construction), and otherwise a tuple of `_confinement_key` strings — which
    may be **empty**, meaning nothing is allowed.

    Accepted forms, in the order they are tested: `True`/`False`, a `str`
    (split on `os.pathsep`, like ``PATH``, so one value can come from an
    environment variable; a `str` without a separator is one root, never a
    sequence of characters), a single `os.PathLike`, or a sequence of either.
    A sequence's elements are each one root and are **not** split, since a
    POSIX filename may legitimately contain ``:``.
    """
    if confine_to is None:
        env = os.environ.get(CONFINE_TO_ENV)
        if env is None:
            return None
        # An unset variable means "no confinement"; an empty one means "an
        # empty allowlist", which allows nothing. They are deliberately
        # different, because the second is a decision and the first is silence.
        confine_to = env
    if confine_to is False:
        return None
    if confine_to is True:
        return True
    if isinstance(confine_to, str):
        parts: "typing.List[_PathLike]" = [
            part for part in confine_to.split(os.pathsep) if part
        ]
    elif isinstance(confine_to, os.PathLike):
        parts = [confine_to]
    else:
        try:
            parts = list(confine_to)
        except TypeError:
            raise ConfigTypeError(
                "confine_to= takes True, a path, a "
                f"{os.pathsep!r}-separated string or a sequence of paths, not "
                f"{type(confine_to).__name__}"
            ) from None
        for part in parts:
            if not isinstance(part, (str, os.PathLike)):
                raise ConfigTypeError(
                    "every confine_to= root must be a path, not "
                    f"{type(part).__name__}"
                )
    return tuple(_confinement_key(part) for part in parts)


def _error_phase(error: BaseException) -> str:
    """``"include"`` when *error* came from a nested include, else ``"load"``.

    Read from `error.config_frames`, not guessed from the error type: the same
    exception is offered once for the included file (as ``"load"``, before any
    include frame exists) and again for each file that included it, and a
    predicate has to be able to tell those offers apart.
    """
    for frame in getattr(error, "config_frames", ()) or ():
        if frame.kind == "include":
            return "include"
    return "load"


def _environ_snapshot() -> typing.Mapping[str, str]:
    """``inject_env``'s ``env``: a read-only copy, so templates cannot change the process environment."""
    return types.MappingProxyType(dict(os.environ))


def _template_references(value: object, environment, cache: dict = None) -> "set[str]":
    """Top-level names every template string inside *value* refers to.

    *cache* memoizes containers by ``id()`` for one document pass, so a shared
    container (a YAML anchor referenced many times) is scanned once.
    """
    cache = {} if cache is None else cache

    def walk(current: object) -> "set[str]":
        if isinstance(current, str):
            if not any(marker in current for marker in jinja2._JINJA_MARKERS):
                return set()
            return set(jinja2.references(current, environment))
        if not isinstance(current, typing.Mapping) and not is_array(current):
            return set()
        seen = cache.get(id(current))
        if seen is not None:
            return seen
        cache[id(current)] = set()  # a self-referential container terminates here
        names: "set[str]" = set()
        if isinstance(current, typing.Mapping):
            for key, item in current.items():
                names |= walk(key)
                names |= walk(item)
        else:
            for item in current:
                names |= walk(item)
        cache[id(current)] = names
        return names

    return walk(value)


def _interpolate_document(
    value: object,
    *,
    environment,
    extra_globals: typing.Mapping,
    strict: bool,
    on_error: typing.Optional[typing.Callable[[BaseException, tuple], bool]] = None,
) -> object:
    """Render every template in *value* once, against the whole document.

    Each top-level key is rendered after the keys it refers to, so a chain
    (``logs: "{{ base }}/logs"``, ``err: "{{ logs }}/err"``) resolves fully in
    one pass whatever order the keys are written in. A value is never rendered
    twice, so an escaped literal stays literal.

    *extra_globals* (the ``inject_env`` snapshot) wins over a document key of
    the same name. A reference to a key that is still being resolved uses that
    key's current value; a cycle between keys raises when *strict* is set.

    *on_error* is offered every failure, as ``on_error(error, keypath)``, once
    where it happens: returning True leaves that one value as its template text
    and rendering continues, so a single bad template no longer costs the rest
    of the document.
    """
    if not isinstance(value, typing.Mapping):
        return jinja2._interpolate(
            value, dict(extra_globals), environment, {}, on_error=on_error
        )

    memo: dict = {}
    references: dict = {}
    scope = dict(value)
    scope.update(extra_globals)
    rendered: dict = {}
    resolving: list = []

    def resolve(key):
        if key in rendered:
            return
        if key in resolving:
            if strict:
                chain = resolving[resolving.index(key) :] + [key]
                error = ConfigValueError(
                    "interpolation reference cycle: "
                    + " -> ".join(str(item) for item in chain)
                )
                _add_error_context(error, key=(key,))
                # Offered like any other interpolation failure. A skip leaves
                # this key's merged, unrendered value and resolution goes on.
                if on_error is not None and on_error(error, (key,)):
                    return
                raise error
            return  # lenient: the reference sees the key's current value
        resolving.append(key)
        try:
            for name in _template_references(value[key], environment, references):
                if name in value and name not in extra_globals:
                    resolve(name)
        finally:
            resolving.pop()
        result = jinja2._interpolate(
            value[key],
            scope,
            environment,
            memo,
            keypath=(key,),
            on_error=on_error,
        )
        rendered[key] = result
        if key not in extra_globals:
            scope[key] = result

    for key in list(value):
        resolve(key)

    # Keys that are themselves templates render last, so they see the document's
    # rendered values; insertion order is preserved.
    return {
        jinja2._interpolate(
            key, scope, environment, memo, keypath=(key,), on_error=on_error
        ): rendered[key]
        for key in value
    }


def _expression_environment():
    """Environment for ``transform``/``%``-key_factory expressions in this load.

    These expressions can come from a document (an ``!include`` mapping), so they
    are evaluated sandboxed whenever the effective policy is hardened
    (``sandbox=True`` or ``allow_commands=False``). Otherwise ``None`` keeps
    ``jinja2.DEFAULT_ENV``. ``strict`` is not applied: it governs interpolation only.
    """
    if is_hardened():
        return jinja2.get_environment(False, True)
    return None


def _pydantic_model(model_cls: object) -> bool:
    """True when *model_cls* is a Pydantic model class.

    Probes ``sys.modules`` instead of importing pydantic: a class can only
    subclass ``pydantic.BaseModel`` if pydantic is already imported, so this is
    exact, and a project that does not use pydantic never pays the import.
    """
    pydantic = sys.modules.get("pydantic")
    if pydantic is None or not isinstance(model_cls, type):
        return False
    base_model = getattr(pydantic, "BaseModel", None)
    return isinstance(base_model, type) and issubclass(model_cls, base_model)


def _model_field_types(model_cls: type) -> dict:
    """Resolved annotations of *model_cls*, empty when they cannot be resolved."""
    try:
        return typing.get_type_hints(model_cls)
    except (NameError, TypeError, AttributeError):
        # e.g. a user's `X | None` annotation on the 3.9 floor: hydrate shallowly.
        return {}


def _nested_model(hint: object) -> object:
    """The model class *hint* names, looking through ``Optional[...]``."""
    candidates = [hint]
    if typing.get_origin(hint) is typing.Union:
        candidates = list(typing.get_args(hint))
    for candidate in candidates:
        if candidate is type(None) or not isinstance(candidate, type):
            continue
        if dataclasses.is_dataclass(candidate) or _pydantic_model(candidate):
            return candidate
    return None


@contextlib.contextmanager
def _model_field(model_cls: type, segment: object = None):
    """Attribute a hydration failure to *model_cls*, and to *segment* if given.

    The same contract as the typed-merge wrapper: only `TypeError` and
    `ValueError` (which covers a Pydantic v2 `ValidationError`), the error is
    always re-raised unchanged, and the key is built inside out so the path
    reads relative to the model the caller asked for.
    """
    try:
        yield
    except (TypeError, ValueError) as error:
        if segment is not None:
            existing = getattr(error, "config_key", ()) or ()
            try:
                error.config_key = (segment,) + tuple(existing)
            except (AttributeError, TypeError):
                pass
        _add_error_context(error, model=getattr(model_cls, "__name__", None))
        raise


def _hydrate(model_cls: type, data: typing.Mapping) -> object:
    """Build *model_cls* from *data*, recursing into model-typed fields.

    Fields annotated with a dataclass or Pydantic model (or ``Optional`` of one)
    are built as instances rather than left as plain dicts. Containers of models
    (``List[Model]``) stay as they are.
    """
    if _pydantic_model(model_cls):
        # Pydantic validates and coerces nested models itself.
        if hasattr(model_cls, "model_validate"):  # v2
            return model_cls.model_validate(data)
        if hasattr(model_cls, "parse_obj"):  # v1
            return model_cls.parse_obj(data)

    if dataclasses.is_dataclass(model_cls):
        # The class signature, not __init__: it excludes `self` and init=False
        # fields while keeping InitVar parameters, which the field list drops.
        valid = {
            name
            for name, param in inspect.signature(model_cls).parameters.items()
            if param.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        hints = _model_field_types(model_cls)
        values = {}
        for key, value in data.items():
            if key not in valid:
                continue
            nested = _nested_model(hints.get(key))
            if nested is not None and isinstance(value, typing.Mapping):
                with _model_field(model_cls, key):
                    value = _hydrate(nested, value)
            values[key] = value
        with _model_field(model_cls):
            return model_cls(**values)

    with _model_field(model_cls):
        return model_cls(**data)


class _IgnoreError(typing.Protocol):
    """An ``ignore_error`` predicate: decide whether to skip one failure.

    Every offer passes `phase`, `path` and `loader` by keyword, plus the extras
    of that phase, so a predicate written with explicit parameters works in all
    of them. `error` stays positional and annotated `Exception`, so a predicate
    written against the older signature still satisfies this.
    """

    def __call__(
        self,
        error: Exception,
        *,
        phase: str,
        path: typing.Any,
        loader: "ConfigLoader",
        **extra: typing.Any,
    ) -> bool: ...


def _pathname(path: object) -> object:
    """What a `key_factory` or `transform` expression sees as ``pathname``.

    A command source is its own text (a `CommandSource` already answers `name`,
    `stem` and `as_posix()` with the whole command), so an expression never sees
    a fragment of it. Everything else is a POSIX-spelled path, as before.
    """
    if isinstance(path, CommandSource):
        return path
    return PurePosixPath(path.as_posix())


def _require_jinja2(feature: str):
    """Return the Jinja2 helper module, or explain what to install.

    Called before a load's source loop, so the error reaches the caller rather
    than an `ignore_error` predicate: without this, `transform=` and a `%`
    key_factory raised `AttributeError: 'NoneType' object has no attribute
    'eval'`, and returned None when errors were being ignored.
    """
    if jinja2 is not None:
        return jinja2
    hint = f"{feature} requires Jinja2: pip install yaconfiglib[jinja2]"
    if _JINJA2_IMPORT_ERROR is None:
        raise ImportError(hint)
    raise ImportError(f"{hint} ({_JINJA2_IMPORT_ERROR})") from _JINJA2_IMPORT_ERROR


class ConfigLoader(ConfigBackend):
    """Orchestrates loading, merging, and interpolating configuration sources.

    A ``ConfigLoader`` is the main entrypoint for hiera-like configuration
    loading: given one or more sources (file paths, glob patterns, command
    URIs, in-memory strings, or nested lists thereof), it resolves each
    source to the appropriate :class:`~yaconfiglib.backends.base.ConfigBackend`,
    parses it, and merges the results together in order using a configurable
    :class:`ConfigLoaderMergeMethod`/:class:`~yaconfiglib.utils.merge.Merge`
    strategy. Optionally, the merged result is interpolated with Jinja2
    (see :attr:`interpolate`) and wrapped in a :class:`DotAccessibleDict`
    for ``config.some.nested.key`` style access.

    Most users can use the module-level :func:`load`/:func:`loads` helpers,
    which construct a ``ConfigLoader`` for a single call. Construct a
    ``ConfigLoader`` instance directly when you need to reuse the same
    settings (base directory, merge strategy, interpolation options) across
    multiple loads.
    """

    def __init__(
        self,
        base_dir: "_PathLike" = "",
        *,
        encoding: typing.Optional[str] = None,
        path_factory: "typing.Optional[typing.Callable[[str], os.PathLike[str]]]" = None,
        loader_factory: (
            "typing.Optional[typing.Callable[[_SourcePath], ConfigBackend]]"
        ) = None,
        recursive: typing.Optional[bool] = None,
        bound_loops: bool = False,
        key_factory: "typing.Optional[typing.Union[str, typing.Callable[[_SourcePath, typing.Any], str]]]" = None,
        log_level: typing.Optional[typing.Any] = None,
        interpolate: typing.Optional[bool] = None,
        merge: "typing.Union[str, ConfigLoaderMergeMethod, Merge]" = (
            ConfigLoaderMergeMethod.Simple
        ),
        merge_options: "typing.Optional[typing.Mapping[str, typing.Any]]" = None,
        ignore_error: "typing.Union[_IgnoreError, bool]" = False,
        inject_env: bool = False,
        strict: bool = False,
        allow_commands: bool = True,
        sandbox: bool = False,
        confine_to: (
            "typing.Optional[typing.Union[bool, str, typing.Sequence[_PathLike]]]"
        ) = None,
    ) -> None:
        """Configure a reusable loader.

        Args:
            base_dir: Directory the relative-path sources passed to
                :meth:`load` are resolved against, and the anchor for
                includes inside documents that are not files (``loads()``,
                ``#!`` strings, streams, command output). A relative
                ``!include`` inside a YAML *file* resolves against that
                file's own directory. Accepts a string or any
                `os.PathLike` — a stdlib `pathlib.Path` and a pathlib-next
                path both qualify.
            encoding: Default text encoding for reading sources.
            path_factory: Callable used to build a ``Path`` from a bare
                string source. Defaults to :attr:`DEFAULT_PATH_FACTORY`.
            loader_factory: Callable ``(path) -> ConfigBackend instance``
                used to select a backend per source. Defaults to
                :meth:`~yaconfiglib.backends.base.ConfigBackend.get_class_by_path`-based
                dispatch.
            recursive: Whether glob sources should recurse into
                subdirectories by default.
            bound_loops: If True, each ``**`` descends any one directory at
                most once. Set it when a tree contains a **Windows junction
                loop** — a junction pointing at one of its own ancestors,
                which otherwise walks until the filesystem refuses the path,
                failing the load. The cost: a directory deliberately reachable
                under two names is then read under only one of them, because
                the bound is by directory identity. A POSIX directory symlink
                is never descended by ``**``, so this changes nothing there.
                Instance-wide, with no per-call override, since a loop is a
                property of the tree rather than of one call.
            key_factory: How to name each loaded document for merging
                (e.g. for :attr:`ConfigLoaderMergeMethod.Hash`). Either a
                callable ``(path, value) -> str``, which receives the source
                path — or, for a command URI, the
                :class:`~yaconfiglib.utils.source.CommandSource` carrying the
                command text — or a string: a `Path` attribute name, or a
                ``"%<jinja-expr>"`` template. Both string forms work here and
                per call. Defaults to the source's filename stem.
            log_level: Deprecated and ignored. It never changed anything:
                library code must not call ``setLevel`` on a shared logger.
                Configure the ``yaconfiglib`` logger through :mod:`logging`.
            interpolate: If True, run Jinja2 interpolation over the merged
                result after loading (see :func:`yaconfiglib.utils.jinja2.interpolate`).
            merge: The merge strategy applied between successive sources —
                a :class:`ConfigLoaderMergeMethod` or any
                :class:`~yaconfiglib.utils.merge.Merge`-compatible callable.
            merge_options: Extra keyword options forwarded to the merge
                callable on every call (e.g. ``{"mergelists": True}``).
            ignore_error: Either a bool (ignore/re-raise every failure
                uniformly) or a predicate deciding per-failure whether to skip
                and continue. Every offer passes the same keywords, so a
                predicate can name them explicitly::

                    def predicate(error, *, phase, path, loader, **extra) -> bool: ...

                *phase* is ``"load"`` (reading or parsing *path* failed),
                ``"include"`` (a source included by *path* failed), ``"merge"``
                (merging *path* into the running result failed),
                ``"interpolate"`` (rendering the merged document failed) or
                ``"glob"`` (a directory could not be listed while expanding a
                pattern — *path* is that directory, and skipping it still loads
                the rest of the pattern). *path* is the source, or ``None``
                where no single source applies. Extras: ``result=`` for ``"interpolate"`` in
                :meth:`load`, ``value=`` in :meth:`load_all`.

                A failure inside an included file is offered **once per level**:
                first for the included file itself (``phase="load"``), then for
                each file that included it (``phase="include"``), with the same
                exception object each time — so a predicate can skip just that
                include, or the whole including file.

                Logging: every offer is logged at DEBUG. A skip under the
                **bool** form is also logged at WARNING, naming the source, the
                phase and the error's type but never its text (which can quote a
                configuration line). A skip a predicate approved stays at DEBUG,
                with the traceback attached.
            inject_env: If True and *interpolate* is set, expose
                ``os.environ`` to templates as the ``env`` global.
            strict: If True, undefined Jinja2 variables raise during
                interpolation instead of rendering as empty.
            allow_commands: If False, loading a command source (``cmd://``,
                ``exec://``, ``sh://``, ``*+fmt://``, or a script-extension
                file) — including one reached via ``!include`` — raises
                :class:`CommandsDisabledError` instead of executing it. Set
                this when loading configuration you do not fully trust. Does
                not restrict a directly-constructed ``CommandBackend``.
            sandbox: If True, interpolation runs in Jinja2's
                ``SandboxedEnvironment``, blocking attribute traversal into
                Python internals (SSTI). Set this when config values may be
                untrusted.
            confine_to: Roots every local file read must fall inside, or
                `None` (the default) for no confinement. Without it a document
                can read any file the process can, through an `!include` with
                an absolute path or ``..`` traversal; with it, such a read
                raises :class:`ConfinementError` **before** the file is
                opened. Accepts:

                * a sequence of paths — a target inside **any** of them is
                  allowed;
                * one string, split on `os.pathsep` like ``PATH``, so the
                  value can come from an environment variable (a string
                  without a separator is one root);
                * `True`, meaning *base_dir*, read at check time;
                * `False` or `None`, meaning off.

                When, and only when, the argument is `None`, the
                ``YACONFIGLIB_CONFINE_TO`` environment variable is read by the
                same rules. An explicit argument ignores it entirely: a
                variable that could widen an in-code allowlist would be an
                escalation for anyone able to set the environment. An **unset**
                variable means no confinement; an **empty** one — like
                ``confine_to=[]`` — is an empty allowlist and refuses every
                local file read.

                The check is on the **logical** path: absolute,
                ``..``-resolved, case-folded where the platform is. Symlinks
                are deliberately *not* resolved, so a link inside a root may
                point outside it — whoever can write to a config root can
                already put a config there. What this closes is a hostile
                *document*, not a hostile root.

                Applies to every file source, a top-level one included, so
                ``ConfigLoader(base_dir="conf", confine_to=True)`` also
                refuses a caller's own absolute path outside ``conf``. Command
                sources (governed by *allow_commands*) and in-memory ``#!``
                documents are exempt — neither is a location — while a remote
                URI source is refused, being inside no local root. Unlike
                *allow_commands* and *sandbox* this is an instance setting
                only: it does not tighten per call, and it is read from the
                loader that performs the read.
        """
        self.allow_commands = bool(allow_commands)
        self.sandbox = bool(sandbox)
        self.merge = (
            merge if isinstance(merge, Merge) else ConfigLoaderMergeMethod(merge)
        )
        self.merge_options = {} if merge_options is None else merge_options
        self.interpolate = False if interpolate is None else bool(interpolate)
        self.inject_env = bool(inject_env)
        self.strict = bool(strict)
        if log_level is not None:
            # Never had an effect: setting the level here made every construction
            # (including the import-time DEFAULT_LOADER) mutate global logging
            # state, so it was removed — but the parameter stayed, silently
            # ignoring valid values and rejecting ints that are not a LogLevel.
            warnings.warn(
                "ConfigLoader(log_level=...) has no effect and will be removed; "
                "configure the 'yaconfiglib' logger with the logging module instead",
                DeprecationWarning,
                stacklevel=2,
            )
        self.path_factory = path_factory or self.DEFAULT_PATH_FACTORY
        self.base_dir = base_dir or ""
        self.encoding = encoding or self.DEFAULT_ENCODING
        self.recursive = False if recursive is None else recursive
        self.bound_loops = bool(bound_loops)
        self.confine_to = confine_to
        # Resolved once: normalising roots per read would re-walk the
        # environment on every source. The True form stays unresolved, since
        # base_dir can be reassigned.
        self._confine_roots = _resolve_confine_to(confine_to)
        self.loader_factory = loader_factory or (
            lambda path: ConfigBackend.get_class_by_path(path)()
        )
        self.key_factory = key_factory or (lambda path, value: path.stem)
        # Remembered because the two forms log differently: a user predicate
        # has already seen the error, a bool never did.
        self._ignore_error_is_bool = not callable(ignore_error)
        self.ignore_error = (
            ignore_error
            if callable(ignore_error)
            else lambda error, *args, **kwargs: bool(ignore_error)
        )

    def _offer_error(
        self,
        error: BaseException,
        *,
        phase: str,
        path: typing.Optional[object],
        **extra: object,
    ) -> bool:
        """Offer one failure to `ignore_error`, and log the outcome.

        The only caller of `self.ignore_error`, so every phase passes the same
        keywords. Returns the predicate's answer: True to skip and continue.

        Logging: every offer is DEBUG. A skip under the **bool** form is also
        WARNING, because nothing else would show it and a blanket
        ``ignore_error=True`` must not make failures invisible; that line names
        the source, the phase and the error's **type** only — never its text,
        which can quote a configuration line. A skip a predicate approved stays
        at DEBUG (with the traceback attached), since the predicate saw it.
        """
        logger.debug("%s error for %s: %s", phase, path, type(error).__name__)
        skip = bool(
            self.ignore_error(error, phase=phase, path=path, loader=self, **extra)
        )
        if not skip:
            return False
        if self._ignore_error_is_bool:
            logger.warning(
                "skipped %s during %s after %s (ignore_error=True)",
                path,
                phase,
                type(error).__name__,
            )
        else:
            logger.debug(
                "ignore_error predicate skipped %s during %s",
                path,
                phase,
                exc_info=error,
            )
        return True

    def _getpath(self, path: "_PathLike"):
        return path if isinstance(path, Path) else self.path_factory(path)

    def _confinement_roots(self) -> "typing.Optional[typing.Tuple[str, ...]]":
        """The roots a file read must fall inside, or None when confinement is off."""
        roots = self._confine_roots
        if roots is True:
            # base_dir defaults to "", which means the working directory —
            # the same directory a relative source resolves against.
            return (_confinement_key(str(self.base_dir) or os.getcwd()),)
        return roots

    def _check_confinement(self, path: "_SourcePath") -> None:
        """Refuse *path* unless ``confine_to=`` allows reading it.

        Called for every source before its backend runs, so an `!include`
        target and a top-level source are governed by one rule. Nothing has
        been read when this raises, which is what makes skipping it through
        ``ignore_error`` safe.
        """
        roots = self._confinement_roots()
        if roots is None:
            return
        kind = _confinement_kind(path)
        if kind == "exempt":
            return
        if kind == "check" and _within_roots(path, roots):
            return
        if not roots:
            detail = "confine_to= allows nothing: the allowlist is empty"
        elif kind == "remote":
            detail = (
                "confine_to= allows only local files, and this source is not one; "
                f"allowed roots: {', '.join(roots)}"
            )
        else:
            detail = f"outside every confine_to= root: {', '.join(roots)}"
        # The resolved target for a local path — the ``..``-free, absolute
        # spelling is the one the roots were compared against, so that is what
        # a reader needs to see. A remote source has no such form, so it is
        # named as written.
        named = str(path) if kind == "remote" else _confinement_key(path)
        error = ConfinementError(f"refusing to read {named}: {detail}")
        _add_error_context(error, source=str(path))
        raise error

    @property
    def base_dir(self):
        return self._base_dir

    @base_dir.setter
    def base_dir(self, value: "_PathLike"):
        self._base_dir = self._getpath(value)

    def _load(
        self,
        path: Path,
        *,
        encoding: typing.Optional[str],
        loader: "typing.Optional[typing.Union[str, ConfigBackend, typing.Callable[[_SourcePath], ConfigBackend]]]" = None,
        transform: typing.Optional[str] = None,
        key_factory: "typing.Optional[typing.Union[str, typing.Callable[[_SourcePath, typing.Any], str]]]" = None,
        allow_commands: typing.Optional[bool] = None,
        **reader_args: typing.Any,
    ) -> "typing.Tuple[str, typing.Any]":

        # NOTE: `recursive` is deliberately NOT a parameter here. Glob expansion
        # happens in parse_sources(), before _load() is ever called, so a
        # `recursive` resolved at this point could never affect anything — it
        # used to be computed here and dropped on the floor. load()/load_all()
        # now pass it to parse_sources() instead. No backend reads it either, so
        # it must not reach **reader_args.
        # The effective policy (set by load()/load_all(), tightened by every
        # enclosing load) can only narrow what this call or instance allows.
        allow_commands = current_policy()[0] and (
            self.allow_commands if allow_commands is None else allow_commands
        )

        if isinstance(loader, str):
            backend_cls = ConfigBackend.get_class_by_name(loader)
            if not backend_cls:
                raise UnknownLoaderError(
                    f"Unknown configuration format/loader: {loader!r}"
                    f"{ConfigBackend._missing_backend_hint(name=loader)}"
                    f"; registered: "
                    f"{', '.join(ConfigBackend._registered_names())}"
                )
            loader_factory = lambda path: backend_cls()
        elif callable(getattr(loader, "load", None)):
            loader_factory = lambda path: loader
        else:
            loader_factory = loader or self.loader_factory

        if loader is self:
            loader_factory = self.loader_factory

        key_factory = key_factory or self.key_factory
        if not callable(key_factory):
            if key_factory.startswith("%"):
                _eval = jinja2.eval(
                    key_factory.removeprefix("%"),
                    environment=_expression_environment(),
                )

                def _key(path: Path, value):
                    return _eval(value=value, pathname=_pathname(path))

            else:
                _keyname = key_factory

                def _key(path: Path, value):
                    val = getattr(path, _keyname)
                    if callable(val):
                        val = val()
                    return str(val)

            key_factory = _key
        # Before the backend is even chosen: a refused source must not reach
        # anything that could read it, and the refusal should not be preceded
        # by an unrelated "no backend reads this" error.
        self._check_confinement(path)
        logger.debug("Loading file: %s", path)
        _loader = loader_factory(path)
        is_command = isinstance(_loader, CommandBackend)
        if is_command and not allow_commands:
            raise CommandsDisabledError(
                f"refusing to run command source {str(path)!r}: " "allow_commands=False"
            )
        _options = dict(
            encoding=encoding,
            path_factory=self.path_factory,
            loader=self,
            base_dir=self.base_dir,
            # A document loaded here is a part of a bigger one: it is interpolated
            # by the caller, once, in the merged scope. Never in isolation — that
            # rendered escaped literals twice and left cross-document references
            # empty. CommandBackend forwards this into its inner loads(), so the
            # output of a command source is not rendered on its own either.
            interpolate=False,
        )
        _options.update(reader_args)

        try:
            if is_command:
                # A command is not a file that can include itself.
                value = _loader.load(path, **_options)
            else:
                # Sources currently being loaded, outermost first. A path that is
                # already in the chain is an include cycle (a.yaml -> b.yaml -> a.yaml),
                # which used to recurse until RecursionError.
                chain = _LOAD_CHAIN.get()
                source = str(path)
                if source in chain:
                    raise ConfigValueError(
                        f"include cycle: {' -> '.join(chain + (source,))}"
                    )
                token = _LOAD_CHAIN.set(chain + (source,))
                try:
                    value = _loader.load(path, **_options)
                finally:
                    _LOAD_CHAIN.reset(token)
            if transform:
                value = jinja2.eval(transform, environment=_expression_environment())(
                    value=value, pathname=_pathname(path)
                )
            return key_factory(path, value), value
        except Exception as error:  # noqa: BLE001 - names the source, re-raises
            # Deliberately every type: a parse error, a decode error, a missing
            # file or a backend's own error must all say which source failed.
            # The error is re-raised unchanged — same type, same identity.
            # Hint first: it belongs to the error's own text, which
            # _add_error_context then snapshots before appending its suffix.
            _add_encoding_hint(error, encoding)
            _add_error_context(error, source=str(path))
            raise

    def load(
        self,
        *pathname: SourceLike,
        recursive: typing.Optional[bool] = None,
        encoding: typing.Optional[str] = None,
        loader: "typing.Optional[typing.Union[str, ConfigBackend, typing.Callable[[_SourcePath], ConfigBackend]]]" = None,
        transform: typing.Optional[str] = None,
        default: typing.Any = None,
        key_factory: "typing.Optional[typing.Union[str, typing.Callable[[_SourcePath, typing.Any], str]]]" = None,
        flatten: bool = False,
        interpolate: typing.Optional[bool] = None,
        merge: (
            "typing.Optional[typing.Union[str, ConfigLoaderMergeMethod, Merge]]"
        ) = None,
        merge_options: "typing.Optional[typing.Mapping[str, typing.Any]]" = None,
        allow_commands: typing.Optional[bool] = None,
        sandbox: typing.Optional[bool] = None,
        **reader_args: typing.Any,
    ) -> typing.Any:
        """Load, merge, and (optionally) interpolate one or more configuration sources.

        Each item in *pathname* is resolved via
        :func:`~yaconfiglib.utils.source.parse_sources` (expanding globs,
        nested lists, in-memory ``#!``-marked strings, streams, and command
        URIs), parsed with the backend selected for it, and merged into the
        running result in order using *merge*.

        Args:
            *pathname: One or more sources — file paths, glob patterns,
                command URIs (``cmd://...``), in-memory content, open file
                objects (anything with ``read()``, parsed by the backend
                their file name selects), or nested iterables of any of
                these. If omitted entirely, loads a single empty in-memory
                document.
            recursive: Overrides the instance's *recursive* for glob
                expansion during this call.
            encoding: Overrides the instance's *encoding* for this call,
                including every ``!include``/``!load`` target that does not
                set its own. It applies to files, bytes documents and binary
                streams. A `str` document or a text stream is already text: it
                is stored in this codec, or in UTF-8 when this codec cannot
                represent it, and read back accordingly.
            loader: Backend name, backend instance, or callable selecting
                the backend for every source loaded in this call,
                overriding per-source auto-detection.
            transform: A Jinja2 expression string evaluated against each
                loaded document (as ``value``) before merging, letting you
                reshape a document inline.
            default: Returned when **no** source loads — nothing matched the
                given patterns, or every source failed and *ignore_error*
                skipped it. It is never merged with a loaded document; put
                defaults in the first source to layer them.
            key_factory: Overrides the instance's *key_factory* for this
                call.
            flatten: If True, the final merged result (expected to be a
                mapping-of-mappings or sequence-of-sequences) is flattened
                one level — useful when each source contributes items to a
                shared top-level collection instead of being keyed by
                itself. Empty (``None``) members are skipped; any other
                member that cannot be flattened raises ``TypeError``.
            interpolate: Overrides the instance's *interpolate* for this
                call.
            merge: Overrides the instance's *merge* strategy for this call.
            merge_options: Overrides the instance's *merge_options* for
                this call.
            **reader_args: Additional keyword arguments forwarded to each
                backend's ``load()`` (e.g. backend-specific options like
                ``json_decoder_options`` or ``ini_default_section``).

        Returns:
            The merged (and possibly interpolated) result. Dict results
            are wrapped in :class:`DotAccessibleDict`.
        """
        encoding = encoding or self.encoding
        interpolate = self.interpolate if interpolate is None else interpolate
        sandbox = self.sandbox if sandbox is None else sandbox
        recursive = self.recursive if recursive is None else recursive
        merge = (
            merge
            if isinstance(merge, Merge)
            else (ConfigLoaderMergeMethod(merge) if merge else self.merge)
        )
        # Per-call override only — must NOT rewrite self.merge_options (doing so
        # made one call's override silently leak into every later load()).
        merge_options = self.merge_options if merge_options is None else merge_options

        # Fail here, before the source loop, so a missing Jinja2 reaches the
        # caller instead of an ignore_error predicate.
        _effective_key_factory = (
            self.key_factory if key_factory is None else key_factory
        )
        if transform is not None:
            _require_jinja2("transform=")
        if isinstance(
            _effective_key_factory, str
        ) and _effective_key_factory.startswith("%"):
            _require_jinja2("key_factory='%...'")
        if interpolate:
            _require_jinja2("interpolate=True")

        # Every source, nested !include and interpolation below runs under the
        # effective trust policy, so a per-call allow_commands=False/sandbox=True
        # reaches nested loads too. The policy can only tighten.
        with use_policy(
            self.allow_commands if allow_commands is None else allow_commands,
            sandbox,
            self.strict,
        ) as (_effective_allow, effective_sandbox, _effective_strict):
            results = default
            _join_init = False

            if not pathname:
                pathname = ("#!\n",)

            for path, read_encoding in _iter_sources(
                pathname,
                base_dir=self.base_dir,
                encoding=encoding,
                path_factory=self.path_factory,
                recursive=recursive,
                on_error=lambda error, directory: self._offer_error(
                    error, phase="glob", path=directory
                ),
                bound_loops=self.bound_loops,
                text_fallback=True,
            ):
                # Which step this source reached, so the one handler below can
                # name the phase and add the frame without a second offer.
                step = "load"
                try:
                    name, result = self._load(
                        path,
                        encoding=read_encoding or encoding,
                        loader=loader,
                        transform=transform,
                        key_factory=key_factory,
                        allow_commands=allow_commands,
                        **reader_args,
                    )
                    # The merge is its own step, so a strategy's failure is
                    # attributed as "while merging <source>" rather than as a
                    # failure to read it.
                    step = "merge"
                    if _join_init:
                        results = merge(
                            results,
                            result,
                            configloaderkey=name,
                            **merge_options,
                        )
                    else:
                        # Probe for the hook instead of catching
                        # AttributeError: an error raised inside a real
                        # init() must surface, not look like "this strategy
                        # has no init".
                        init = getattr(merge, "init", None)
                        if callable(init):
                            results = init(
                                initial=result,
                                configloaderkey=name,
                                **merge_options,
                            )
                        else:
                            results = result
                        _join_init = True
                # Deliberately broad: ``ignore_error`` is a user predicate designed
                # to decide per-error whether to skip ANY load failure (a YAML parse
                # error, a missing file, a backend error...), so narrowing the tuple
                # would break that contract. KeyboardInterrupt/SystemExit are
                # BaseException and already excluded. Every offer is logged, and a
                # skip under ignore_error=True is logged at WARNING (see
                # _offer_error).
                except (
                    Exception
                ) as error:  # noqa: BLE001 - feeds the ignore_error predicate
                    # One offer per failure: the step says which phase it is,
                    # so a merge failure is never also offered as a load one.
                    if step == "merge":
                        _add_error_context(error, frame=ErrorFrame("merge", str(path)))
                    if self._offer_error(
                        error,
                        phase="merge" if step == "merge" else _error_phase(error),
                        path=path,
                    ):
                        continue
                    raise

            if flatten:
                if isinstance(results, typing.Mapping):
                    result = {}
                    for member_key, member in results.items():
                        # An empty document (a placeholder file in a conf.d glob)
                        # loads as None and contributes nothing.
                        if member is None:
                            continue
                        if not isinstance(member, typing.Mapping):
                            raise ConfigTypeError(
                                f"flatten=True: member {member_key!r} is a "
                                f"{type(member).__name__}, not a mapping"
                            )
                        result.update(member)
                elif is_array(results):
                    result = []
                    for index, member in enumerate(results):
                        if member is None:
                            continue
                        if isinstance(member, (str, bytes)) or not is_array(member):
                            raise ConfigTypeError(
                                f"flatten=True: member {index} is a "
                                f"{type(member).__name__}, not a sequence"
                            )
                        result.extend(member)
                else:
                    raise ConfigTypeError(
                        "flatten=True requires merged results to be a mapping or sequence"
                    )
            else:
                result = results

            if interpolate:
                # No try around the whole document: each failing value is
                # offered where it occurs, with its key path, and a skip costs
                # only that value.
                result = _interpolate_document(
                    result,
                    environment=jinja2.get_environment(self.strict, effective_sandbox),
                    extra_globals=(
                        {"env": _environ_snapshot()} if self.inject_env else {}
                    ),
                    strict=current_policy()[2],
                    on_error=lambda error, key: self._offer_error(
                        error,
                        phase="interpolate",
                        path=None,
                        key=key,
                        result=result,
                    ),
                )

            # Make every nested mapping dot-accessible, once, here. This also
            # covers a list or Hash/List result, whose members are mappings.
            result = _to_dot_access(result, {})

            return result

    def load_as(
        self, model_cls: "typing.Type[T]", *pathname: SourceLike, **kwargs: typing.Any
    ) -> T:
        """Load configuration sources and instantiate as *model_cls*.

        Supports Pydantic models (when pydantic is already imported) or
        dataclasses. If neither matches, falls back to passing the loaded
        mapping to the constructor as keyword arguments.

        Note that *kwargs* goes to :meth:`load`, so constructor-only options
        (``strict``, ``base_dir``, ...) belong on the ``ConfigLoader``.
        :func:`yaconfiglib.load_as` routes them for you.
        """
        data = self.load(*pathname, **kwargs)
        if not isinstance(data, dict):
            # Naming the model and the actual type separates "this document is
            # the wrong shape" from "nothing matched", which used to share one
            # message.
            message = (
                f"load_as({getattr(model_cls, '__name__', model_cls)}) needs a "
                f"mapping, got {type(data).__name__}"
            )
            if data is None:
                message += "; no source was loaded"
            raise ConfigTypeError(message)
        return _hydrate(model_cls, data)

    def load_all(
        self,
        *pathname: SourceLike,
        encoding: typing.Optional[str] = None,
        interpolate: typing.Optional[bool] = None,
        sandbox: typing.Optional[bool] = None,
        allow_commands: typing.Optional[bool] = None,
        **reader_args: typing.Any,
    ) -> "typing.Iterator[typing.Any]":
        """Yield each source's parsed (and optionally interpolated) document individually, without merging.

        Unlike :meth:`load`, which merges every source into a single
        result, this generator yields one value per resolved source —
        useful when sources represent independent documents rather than
        layers of the same configuration (e.g. iterating a directory of
        unrelated config files).

        Args:
            *pathname: Sources to resolve, same semantics as :meth:`load`.
            encoding: Overrides the instance's *encoding* for this call,
                including every ``!include``/``!load`` target that does not
                set its own. It applies to files, bytes documents and binary
                streams. A `str` document or a text stream is already text: it
                is stored in this codec, or in UTF-8 when this codec cannot
                represent it, and read back accordingly.
            interpolate: Overrides the instance's *interpolate* for this
                call; applied independently to each yielded document.
            sandbox: Overrides the instance's *sandbox* for this call,
                including nested ``!include`` targets.
            allow_commands: Overrides the instance's *allow_commands* for
                this call, including nested ``!include`` targets.
            **reader_args: Additional keyword arguments forwarded to each
                backend's ``load()``.

        Yields:
            Each source's parsed document, with dict results wrapped in
            :class:`DotAccessibleDict`.
        """
        interpolate = self.interpolate if interpolate is None else interpolate
        encoding = encoding or self.encoding
        sandbox = self.sandbox if sandbox is None else sandbox
        allow_commands = (
            self.allow_commands if allow_commands is None else allow_commands
        )
        # Same pre-flight as load(): reader_args may carry transform= or a
        # "%..." key_factory through to each source.
        _all_transform = reader_args.get("transform")
        _all_key_factory = reader_args.get("key_factory", self.key_factory)
        if _all_transform is not None:
            _require_jinja2("transform=")
        if isinstance(_all_key_factory, str) and _all_key_factory.startswith("%"):
            _require_jinja2("key_factory='%...'")
        if interpolate:
            _require_jinja2("interpolate=True")
        for path, read_encoding in _iter_sources(
            pathname,
            base_dir=self.base_dir,
            encoding=encoding,
            path_factory=self.path_factory,
            recursive=self.recursive,
            on_error=lambda error, directory: self._offer_error(
                error, phase="glob", path=directory
            ),
            bound_loops=self.bound_loops,
            text_fallback=True,
        ):
            value = None
            # Set when an interpolation failure was already offered and
            # declined, so the handler below re-raises it without asking a
            # second time. A local flag, not something read off the error: a
            # load-phase error could one day carry a key path too.
            declined_interpolation = False

            def _offer_interpolation(error, key, path=path):
                nonlocal declined_interpolation
                if self._offer_error(
                    error,
                    phase="interpolate",
                    path=path,
                    key=key,
                    value=value,
                    source=str(path),
                ):
                    return True
                declined_interpolation = True
                return False

            try:
                # The policy covers this source's load and interpolation only and
                # is exited before yield: a generator runs in its consumer's
                # context, so holding it across yield would leak it into the
                # caller's loop body.
                with use_policy(allow_commands, sandbox, self.strict) as (
                    effective_allow,
                    effective_sandbox,
                    _effective_strict,
                ):
                    key, value = self._load(
                        path,
                        encoding=read_encoding or encoding,
                        allow_commands=effective_allow,
                        **reader_args,
                    )
                    if interpolate:
                        value = _interpolate_document(
                            value,
                            environment=jinja2.get_environment(
                                self.strict, effective_sandbox
                            ),
                            extra_globals=(
                                {"env": _environ_snapshot()} if self.inject_env else {}
                            ),
                            strict=current_policy()[2],
                            on_error=_offer_interpolation,
                        )
                value = _to_dot_access(value, {})
                yield value

            except (
                Exception
            ) as error:  # noqa: BLE001 - feeds the ignore_error predicate
                if declined_interpolation:
                    raise
                if not self._offer_error(
                    error, phase=_error_phase(error), path=path, value=value
                ):
                    raise


#: Distinguishes "absent" from a stored ``None`` while digging a dotted path.
_MISSING = object()


def _to_dot_access(value: object, memo: dict) -> object:
    """Return *value* with every nested mapping dot-accessible.

    Copies rather than mutating: a backend may hand back an object the caller
    still owns (`PythonBackend` does), and merges are copy-on-write for the
    same reason.

    *memo* maps ``id(source container)`` to its converted object, so structure
    shared in the source stays shared in the result — a YAML anchor used twice
    is still one object afterwards — and a self-referencing document
    terminates. A container is registered before its children are converted.
    """
    existing = memo.get(id(value), _MISSING)
    if existing is not _MISSING:
        return existing

    if isinstance(value, dict):
        # dict.__new__ keeps a DotAccessibleDict subclass's own type and calls
        # no __init__ (which would convert a second time).
        converted = (
            dict.__new__(type(value))
            if isinstance(value, DotAccessibleDict)
            else DotAccessibleDict()
        )
        memo[id(value)] = converted
        for key, item in value.items():
            dict.__setitem__(converted, key, _to_dot_access(item, memo))
        return converted

    # Only exact list/tuple: a list subclass may carry state a plain list
    # cannot, and a namedtuple would lose its type through tuple(...).
    if type(value) is list:
        converted = []
        memo[id(value)] = converted
        converted.extend(_to_dot_access(item, memo) for item in value)
        return converted
    if type(value) is tuple:
        # Built first, so it can only be registered afterwards; a tuple cannot
        # take part in a cycle it owns.
        converted = tuple(_to_dot_access(item, memo) for item in value)
        memo[id(value)] = converted
        return converted

    # Everything else is returned as it is and not descended into: a non-dict
    # Mapping may be lazy or proxy live state, and materializing it is not this
    # library's decision.
    return value


class DotAccessibleDict(dict):
    """Dictionary subclass supporting dot-notation queries and attribute access.

    Nested mappings are converted **once, at construction**, so item access,
    attribute access and object identity all agree however the value is
    reached, whatever the read order. Reads never write: `get()` follows
    `dict.get`'s contract and a miss returns the default without storing it.

    Values assigned after construction are stored exactly as given — a plain
    dict written with ``cfg["x"] = {...}`` stays a plain dict.

    A key that collides with something the class defines (``items``, ``get``,
    ``copy``, ...) is reachable only through item access: attribute *reads*
    find the method, so attribute writes and deletes of such a name raise
    `AttributeError` rather than letting the two disagree. A subclass that
    needs a real instance attribute uses ``object.__setattr__``.

    ``dict(cfg)`` is the way to get a plain `dict` back.
    """

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        # Seed the memo with this object, and with the source mapping when
        # there is one, so a self-referencing source resolves to this instance.
        memo = {id(self): self}
        if len(args) == 1 and isinstance(args[0], dict):
            memo[id(args[0])] = self
        for key, value in list(self.items()):
            dict.__setitem__(self, key, _to_dot_access(value, memo))

    def __getattr__(self, name: str) -> typing.Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            ) from None

    def __setattr__(self, name: str, value: object) -> None:
        # A dict subclass cannot expose "items", "keys", "get" and friends as
        # attributes, so refusing the write is the only way attribute reads and
        # writes cannot disagree about such a name. Use cfg[name] for the key;
        # object.__setattr__ for a real instance attribute in a subclass.
        if hasattr(type(self), name):
            raise AttributeError(
                f"{name!r} is read-only on {type(self).__name__!r} because the class "
                f"defines it; use cfg[{name!r}] to set that key"
            )
        self[name] = value

    def __delattr__(self, name: str) -> None:
        if hasattr(type(self), name):
            raise AttributeError(
                f"{name!r} is read-only on {type(self).__name__!r} because the class "
                f"defines it; use del cfg[{name!r}] to remove that key"
            )
        try:
            del self[name]
        except KeyError:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            ) from None

    def copy(self) -> "DotAccessibleDict":
        """A shallow copy of the same class — like `dict.copy`, but typed."""
        return _copy.copy(self)

    def __or__(self, other: typing.Any) -> typing.Any:
        if not isinstance(other, dict):
            return NotImplemented
        merged = type(self)()
        # Built like dict's own operator: key order of the left operand, then
        # the right's, right wins. Values are stored as given -- conversion
        # happens at construction only.
        for mapping in (self, other):
            for key, value in mapping.items():
                dict.__setitem__(merged, key, value)
        return merged

    def __ror__(self, other: typing.Any) -> typing.Any:
        if not isinstance(other, dict):
            return NotImplemented
        merged = type(self)()
        for mapping in (other, self):
            for key, value in mapping.items():
                dict.__setitem__(merged, key, value)
        return merged

    def get(
        self, key: typing.Hashable, default: typing.Any = None, dig: bool = True
    ) -> typing.Any:
        """Look up *key*, optionally as a path into nested containers.

        The order is: an **exact** key wins; then, when *dig* is true, a
        dotted-string or tuple path is traversed; otherwise *default*.

        A dotted string (``"database.credentials.user"``) walks mappings by
        segment. A segment of ASCII digits indexes a list or tuple
        (``"servers.0.host"``), but on a mapping it is always the *string* key,
        so ``"codes.0"`` never means the integer ``0``. Exact-key precedence
        applies at the top level only, so a nested key that itself contains a
        dot is unreachable this way — pass a **tuple** path for that
        (``("metadata", "labels", "app.kubernetes.io/name")``), which also
        reaches integer keys and uses ints, not digit strings, as indexes.

        A ``None`` (or any non-traversable value) reached with segments still
        to go yields *default*; the final segment's value is returned as
        stored, ``None`` included — so an explicit ``null`` stays
        distinguishable from an absent key.

        Never inserts: a miss returns *default* itself, as `dict.get` does.
        """
        if key in self:
            return super().get(key, default)
        if not dig:
            return default

        if isinstance(key, str):
            if "." not in key:
                return default
            # Inlined rather than delegated: this is the benchmarked read path,
            # and a helper call with keyword arguments showed up in it.
            segments = key.split(".")
            current: object = self
            last = len(segments) - 1
            for index, segment in enumerate(segments):
                if isinstance(current, dict):
                    # dict.get, so a defaultdict stored later does not grow a
                    # key through a read, and __missing__ is never consulted.
                    current = dict.get(current, segment, _MISSING)
                    if current is _MISSING:
                        return default
                elif current.__class__ is list or current.__class__ is tuple:
                    # ASCII digits only: a Unicode digit must not reach int(),
                    # and a negative or non-numeric segment ends the path.
                    if not (segment.isascii() and segment.isdigit()):
                        return default
                    position = int(segment)
                    if position >= len(current):
                        return default
                    current = current[position]
                else:
                    # A scalar, a None, or a non-dict Mapping: the path stops.
                    return default
                if current is None and index != last:
                    return default
            return current
        if isinstance(key, tuple):
            if not key:
                return default
            return self._dig(key, default)
        # Any other hashable key: absent is absent. (dict.get's contract; an
        # unhashable one has already raised TypeError above.)
        return default

    def _dig(
        self, segments: typing.Sequence[typing.Hashable], default: object
    ) -> object:
        """Walk a **tuple** path, returning *default* the moment it breaks.

        Each segment is used as given: any hashable key on a mapping, and on a
        list or tuple only a real `int`. The string form is inlined in `get()`.
        """
        current: object = self
        last = len(segments) - 1
        for index, segment in enumerate(segments):
            if isinstance(current, dict):
                # dict.get, so a defaultdict stored later does not grow a key
                # through a read, and __missing__ is never consulted.
                current = dict.get(current, segment, _MISSING)
                if current is _MISSING:
                    return default
            elif current.__class__ is list or current.__class__ is tuple:
                # bool is excluded: True == 1 would index silently.
                if (
                    not isinstance(segment, int)
                    or isinstance(segment, bool)
                    or not 0 <= segment < len(current)
                ):
                    return default
                current = current[segment]
            else:
                # A scalar, a None, or a non-dict Mapping: the path stops here.
                return default
            if current is None and index != last:
                return default
        return current


#: Every keyword :class:`ConfigLoader` itself accepts. The module-level helpers
#: route those to the constructor and everything else to ``.load()``; only this
#: side can be enumerated, because backends are pluggable and their reader
#: options are open-ended.
_LOADER_INIT_PARAMS = frozenset(
    name
    for name in inspect.signature(ConfigLoader.__init__).parameters
    if name != "self"
)


def _split_loader_kwargs(kwargs: dict) -> "tuple[dict, dict]":
    """Split *kwargs* into ``(ConfigLoader(...) kwargs, .load(...) kwargs)``.

    A constructor keyword configures the whole one-shot load, so it also governs
    what nested ``!include`` targets do. Anything else — backend reader options
    such as ``json_decoder_options`` or ``environment`` — goes to ``.load()``,
    which forwards it to the backend.
    """
    loader_kwargs = {}
    load_kwargs = {}
    for key, value in kwargs.items():
        if key in _LOADER_INIT_PARAMS:
            # `merge=None` means "use the default" for .load(); the constructor
            # would reject it, so drop it and let the default apply.
            if key == "merge" and value is None:
                continue
            loader_kwargs[key] = value
        else:
            load_kwargs[key] = value
    return loader_kwargs, load_kwargs


def load(fp: typing.Any, **kwargs: typing.Any) -> typing.Any:
    """Load configuration from a file path or an open file object.

    An open file object is anything with ``read()``, and it is parsed by the
    backend its file name selects — so ``load(open("settings.toml"))`` reads
    TOML. A name no backend recognizes (``<stdin>``, ``x.yaml.gz``, a stream
    with no name) is parsed as YAML; pass ``loader=`` to choose explicitly.

    Keyword arguments :class:`ConfigLoader` accepts configure the loader (and so
    also apply to nested ``!include`` targets); every other keyword is passed to
    :meth:`ConfigLoader.load`, which forwards unknown ones to the backend.

    Raises:
        TypeError: If *fp* is ``None``.
        ValueError: If *fp* is an empty string. For optional layers, pass them
            among the sources of :meth:`ConfigLoader.load`, which skips falsy
            ones.
    """
    if fp is None:
        raise TypeError(
            "load() got None for fp; check the path before calling, or pass it "
            "among the sources of ConfigLoader().load(...), which skips a falsy "
            "source"
        )
    if fp == "" or fp == b"":
        raise ValueError(
            "load() got an empty source; check the path before calling, or pass "
            "it among the sources of ConfigLoader().load(...), which skips a "
            "falsy source"
        )
    loader_kwargs, load_kwargs = _split_loader_kwargs(kwargs)
    loader_inst = ConfigLoader(**loader_kwargs)
    return loader_inst.load(fp, **load_kwargs)


def _marker_name(
    view: typing.Union[str, bytes], encoding: typing.Optional[str]
) -> typing.Optional[str]:
    """The ``#!name`` first line of *view*, if it names a recognized format.

    ``None`` when there is no marker line, when its name decodes to nothing a
    backend claims (``#!/usr/bin/env python``, ``#!gen.sh``), or when it does
    not decode at all — in every one of those cases the line is content.
    """
    marker, newline = ("#!", "\n") if isinstance(view, str) else (b"#!", b"\n")
    if not view.startswith(marker):
        return None
    name = view[len(marker) :].split(newline, 1)[0]
    if isinstance(name, bytes):
        try:
            name = name.decode(encoding or "utf-8")
        except UnicodeDecodeError:
            return None
    name = name.strip()
    return name if name and _backend_claims(name) else None


def loads(s: typing.Union[str, bytes, bytearray], **kwargs: typing.Any) -> typing.Any:
    """Load configuration from a string or bytes in memory.

    The text is parsed as **YAML** unless ``loader=`` is given, or its first
    line is ``#!<name>`` naming a format some backend recognizes
    (``"#!app.toml\\n..."``). Any other first line — a real shebang, a script
    name — is content, so nothing that loads today parses differently.

    ``bytes`` (and ``bytearray``) are read with ``encoding=``, UTF-8 by
    default, and a BOM is ignored. They reach a byte-oriented ``loader=``
    backend unchanged.

    Keyword arguments are routed as in :func:`load`.

    Raises:
        TypeError: For any other type of *s*.
    """
    if isinstance(s, bytearray):
        s = bytes(s)
    elif not isinstance(s, (str, bytes)):
        raise TypeError(f"loads() expects str or bytes, not {type(s).__name__}")

    # Read encoding BEFORE the split, which keeps it in kwargs: it configures
    # the loader (and so every include) as well as decoding this document.
    encoding = kwargs.get("encoding")
    loader_kwargs, load_kwargs = _split_loader_kwargs(kwargs)
    loader_inst = ConfigLoader(**loader_kwargs)

    # The same marker view ConfigLoader.load uses: bytes untouched for a codec
    # that spells "#!" in ASCII, decoded for the codecs that cannot.
    view = _marker_view(s, encoding) if isinstance(s, bytes) else s
    if _marker_name(view, encoding) is None:
        # No usable name: prepend the unnamed marker, so the document keeps
        # yaconfiglib's YAML default and its own first line stays content.
        view = ("#!\n" if isinstance(view, str) else b"#!\n") + view
    return loader_inst.load(view, **load_kwargs)


def load_as(
    model_cls: "typing.Type[T]", *pathname: SourceLike, **kwargs: typing.Any
) -> T:
    """Load one or more sources and instantiate *model_cls* from the result.

    Unlike :func:`load`, this takes **several** sources, merged in order.
    Keyword arguments are routed as in :func:`load`, so constructor options
    (``strict``, ``base_dir``, ``merge``, ...) configure the loader and reach
    nested includes.

    A Pydantic model is validated by pydantic itself (when pydantic is already
    imported); a dataclass gets only the keys its signature accepts, with
    fields annotated as a dataclass or Pydantic model built as instances;
    anything else receives the mapping as keyword arguments.
    """
    loader_kwargs, load_kwargs = _split_loader_kwargs(kwargs)
    return ConfigLoader(**loader_kwargs).load_as(model_cls, *pathname, **load_kwargs)


def _is_utf_codec(name: typing.Optional[str]) -> bool:
    """Whether *name* is a UTF codec, and so can hold any text.

    A name nothing recognizes counts as non-UTF: the conservative answer keeps
    escapes, which every codec can write.
    """
    if not name:
        return False
    try:
        return codecs.lookup(name).name.startswith("utf-")
    except LookupError:
        return False


def dump(
    obj: typing.Any,
    fp: typing.Any,
    *,
    encoding: typing.Optional[str] = None,
    **kwargs: typing.Any,
) -> None:
    """Write *obj* as YAML to *fp*.

    *fp* may be a text file object, a binary one (`io.BytesIO`,
    ``open(path, "wb")``, `gzip.open`), a path as `str`/`bytes`, a
    `pathlib.Path`, or a pathlib-next path such as a ``MemPath``.

    *encoding* (UTF-8 by default) applies to everything this function opens or
    encodes itself. It is **not** used for a text file object, which writes in
    its own codec. Non-ASCII text is written as-is, except where the target
    codec is not a UTF codec (``open(path, "w")`` on a Windows console codepage,
    for example): there it is escaped, because the alternative is
    `UnicodeEncodeError`. Pass ``allow_unicode=`` to decide for yourself.

    A path target is opened in text mode, so it gets the platform's line
    endings, like `json.dump` and `open`. For LF everywhere, pass a file object
    opened with ``newline=""``.

    *obj* is serialized before *fp* is opened, so a value YAML cannot represent
    leaves an existing file untouched.

    Raises:
        TypeError: If *fp* is none of the above.
    """
    enc = encoding or "utf-8"
    # The stream's own codec decides for a text stream; for everything else
    # it is the codec we are about to use.
    target_codec = enc
    if hasattr(fp, "write"):
        stream_codec = getattr(fp, "encoding", None)
        if isinstance(stream_codec, str):
            target_codec = stream_codec
    if not _is_utf_codec(target_codec):
        kwargs.setdefault("allow_unicode", False)

    content = dumps(obj, **kwargs)

    if hasattr(fp, "write"):
        try:
            fp.write(content)
        except TypeError:
            # A binary target rejects str before writing anything, so there is
            # nothing to undo here.
            fp.write(content.encode(enc))
        return
    if isinstance(fp, (str, bytes)):
        with open(fp, "w", encoding=enc) as handle:
            handle.write(content)
        return
    # Before the os.PathLike branch: a MemPath is an os.PathLike whose
    # __fspath__ raises, and write_text is what every path type here has.
    write_text = getattr(fp, "write_text", None)
    if callable(write_text):
        write_text(content, encoding=enc)
        return
    if isinstance(fp, os.PathLike):
        with open(os.fspath(fp), "w", encoding=enc) as handle:
            handle.write(content)
        return
    raise TypeError(
        f"dump() cannot write to {type(fp).__name__}; pass a file object, a "
        "path, or a pathlib path"
    )


def dumps(obj: typing.Any, **kwargs: typing.Any) -> str:
    """Serialize *obj* to a YAML string (always YAML; see :class:`~yaconfiglib.backends.yaml.YamlConfig`).

    A loaded configuration writes as a plain mapping and a ``tuple`` as a plain
    sequence, so the output can be loaded back; every other Python object keeps
    PyYAML's tag. Mapping keys keep their order (``sort_keys=False``) and
    non-ASCII text is written as-is (``allow_unicode=True``) — pass either
    keyword to override. Call a backend instance's ``dumps()`` for another
    format.

    Raises:
        TypeError: If ``encoding=`` is passed; this returns `str`. Encode the
            result, or use :func:`dump` with ``encoding=``.
    """
    from .backends.yaml import YamlConfig

    backend = YamlConfig()
    return backend.dumps(obj, **kwargs)


DEFAULT_LOADER = ConfigLoader()
