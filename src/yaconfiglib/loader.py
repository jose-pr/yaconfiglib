from __future__ import annotations

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
from .utils.enum import IntEnum
from .utils.merge import Merge, MergeMethod, is_array
from .utils.source import CommandSource, SourceLike, parse_sources
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
        memo: dict = None,
        **options,
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
        memo: dict = None,
        **options,
    ):
        return b

    def _list(
        self,
        a: list,
        b: object,
        *,
        configloaderkey: str,
        memo: dict = None,
        **options,
    ):
        a.append(b)
        return a

    def _hash(
        self,
        a: dict,
        b: object,
        *,
        configloaderkey: str,
        memo: dict = None,
        **options,
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

    class ConfigLoaderMergeMethod(
        _ConfigLoaderMergeMethod, MergeMethod, typing.Protocol
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
) -> object:
    """Render every template in *value* once, against the whole document.

    Each top-level key is rendered after the keys it refers to, so a chain
    (``logs: "{{ base }}/logs"``, ``err: "{{ logs }}/err"``) resolves fully in
    one pass whatever order the keys are written in. A value is never rendered
    twice, so an escaped literal stays literal.

    *extra_globals* (the ``inject_env`` snapshot) wins over a document key of
    the same name. A reference to a key that is still being resolved uses that
    key's current value; a cycle between keys raises when *strict* is set.
    """
    if not isinstance(value, typing.Mapping):
        return jinja2.interpolate(value, dict(extra_globals), environment=environment)

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
                raise ValueError(
                    "interpolation reference cycle: "
                    + " -> ".join(str(item) for item in chain)
                )
            return  # lenient: the reference sees the key's current value
        resolving.append(key)
        try:
            for name in _template_references(value[key], environment, references):
                if name in value and name not in extra_globals:
                    resolve(name)
        finally:
            resolving.pop()
        result = jinja2._interpolate(value[key], scope, environment, memo)
        rendered[key] = result
        if key not in extra_globals:
            scope[key] = result

    for key in list(value):
        resolve(key)

    # Keys that are themselves templates render last, so they see the document's
    # rendered values; insertion order is preserved.
    return {
        jinja2._interpolate(key, scope, environment, memo): rendered[key]
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
                value = _hydrate(nested, value)
            values[key] = value
        return model_cls(**values)

    return model_cls(**data)


class _IgnoreError(typing.Protocol):
    def __call__(self, error: Exception, *args, **kwargs) -> bool: ...


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
        base_dir: str | Path = "",
        *,
        encoding: str = None,
        path_factory: typing.Callable[[str], Path] = None,
        loader_factory: type[ConfigBackend] = None,
        recursive: bool = None,
        key_factory: typing.Callable[[Path, object], str] = None,
        log_level: object = None,
        interpolate: bool = None,
        merge: ConfigLoaderMergeMethod | Merge = ConfigLoaderMergeMethod.Simple,
        merge_options: dict[str] = None,
        ignore_error: _IgnoreError | bool = False,
        inject_env: bool = False,
        strict: bool = False,
        allow_commands: bool = True,
        sandbox: bool = False,
    ) -> None:
        """Configure a reusable loader.

        Args:
            base_dir: Directory the relative-path sources passed to
                :meth:`load` are resolved against, and the anchor for
                includes inside documents that are not files (``loads()``,
                ``#!`` strings, streams, command output). A relative
                ``!include`` inside a YAML *file* resolves against that
                file's own directory. Accepts a string or ``Path``.
            encoding: Default text encoding for reading sources.
            path_factory: Callable used to build a ``Path`` from a bare
                string source. Defaults to :attr:`DEFAULT_PATH_FACTORY`.
            loader_factory: Callable ``(path) -> ConfigBackend instance``
                used to select a backend per source. Defaults to
                :meth:`~yaconfiglib.backends.base.ConfigBackend.get_class_by_path`-based
                dispatch.
            recursive: Whether glob sources should recurse into
                subdirectories by default.
            key_factory: Callable ``(path, value) -> str`` producing the
                merge key used to track/name each loaded document (e.g.
                for :attr:`ConfigLoaderMergeMethod.Hash`). Defaults to the
                source's filename stem. May also be set per-call as a
                string attribute name or a ``"%<jinja-expr>"`` template.
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
            ignore_error: Either a bool (ignore/re-raise all load errors
                uniformly) or a predicate ``(error, **context) -> bool``
                deciding per-error whether to skip and continue.
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
        self.loader_factory = loader_factory or (
            lambda path: ConfigBackend.get_class_by_path(path)()
        )
        self.key_factory = key_factory or (lambda path, value: path.stem)
        self.ignore_error = (
            ignore_error
            if callable(ignore_error)
            else lambda error, *args, **kwargs: bool(ignore_error)
        )

    def _getpath(self, path: str | Path):
        return path if isinstance(path, Path) else self.path_factory(path)

    @property
    def base_dir(self):
        return self._base_dir

    @base_dir.setter
    def base_dir(self, value: str | Path):
        self._base_dir = self._getpath(value)

    def _load(
        self,
        path: Path,
        *,
        encoding: str,
        loader: str = None,
        transform: str = None,
        key_factory: str | typing.Callable[[Path], str] = None,
        allow_commands: bool = None,
        **reader_args,
    ) -> tuple[str, object]:

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
                raise ValueError(
                    f"Unknown configuration format/loader: {loader}"
                    f"{ConfigBackend._missing_backend_hint(name=loader)}"
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
        logger.debug(f"Loading file: {path}")
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
                raise ValueError(f"include cycle: {' -> '.join(chain + (source,))}")
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

    def load(
        self,
        *pathname: SourceLike,
        recursive: bool = None,
        encoding: str = None,
        loader: str = None,
        transform: str = None,
        default: object = None,
        key_factory: str | typing.Callable[[Path], str] = None,
        flatten: bool = False,
        interpolate: bool = None,
        merge: ConfigLoaderMergeMethod | Merge = None,
        merge_options: dict[str] = None,
        allow_commands: bool = None,
        sandbox: bool = None,
        **reader_args: object,
    ) -> object:
        """Load, merge, and (optionally) interpolate one or more configuration sources.

        Each item in *pathname* is resolved via
        :func:`~yaconfiglib.utils.source.parse_sources` (expanding globs,
        nested lists, in-memory ``#!``-marked strings, streams, and command
        URIs), parsed with the backend selected for it, and merged into the
        running result in order using *merge*.

        Args:
            *pathname: One or more sources — file paths, glob patterns,
                command URIs (``cmd://...``), in-memory content, open
                streams, or nested iterables of any of these. If omitted
                entirely, loads a single empty in-memory document.
            recursive: Overrides the instance's *recursive* for glob
                expansion during this call.
            encoding: Overrides the instance's *encoding* for this call,
                including every ``!include``/``!load`` target that does not
                set its own.
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

            for path in parse_sources(
                pathname,
                base_dir=self.base_dir,
                encoding=encoding,
                path_factory=self.path_factory,
                recursive=recursive,
            ):
                try:
                    name, result = self._load(
                        path,
                        encoding=encoding,
                        loader=loader,
                        transform=transform,
                        key_factory=key_factory,
                        allow_commands=allow_commands,
                        **reader_args,
                    )
                    if _join_init:
                        results = merge(
                            results,
                            result,
                            configloaderkey=name,
                            **merge_options,
                        )
                    else:
                        # Probe for the hook instead of catching AttributeError:
                        # an error raised inside a real init() must surface, not
                        # look like "this strategy has no init".
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
                # BaseException and already excluded. The error is never swallowed
                # silently — it is handed to the predicate and logged.
                except (
                    Exception
                ) as error:  # noqa: BLE001 - feeds the ignore_error predicate
                    logger.debug("load error for %s: %s", path, error)
                    if self.ignore_error(error, path=path, loader=self):
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
                            raise TypeError(
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
                            raise TypeError(
                                f"flatten=True: member {index} is a "
                                f"{type(member).__name__}, not a sequence"
                            )
                        result.extend(member)
                else:
                    raise TypeError(
                        "flatten=True requires merged results to be a mapping or sequence"
                    )
            else:
                result = results

            if interpolate:
                try:
                    result = _interpolate_document(
                        result,
                        environment=jinja2.get_environment(
                            self.strict, effective_sandbox
                        ),
                        extra_globals=(
                            {"env": _environ_snapshot()} if self.inject_env else {}
                        ),
                        strict=current_policy()[2],
                    )
                except (
                    Exception
                ) as error:  # noqa: BLE001 - feeds the ignore_error predicate
                    logger.debug("interpolation error: %s", error)
                    if not self.ignore_error(error, result=result, loader=self):
                        raise

            # Make every nested mapping dot-accessible, once, here. This also
            # covers a list or Hash/List result, whose members are mappings.
            result = _to_dot_access(result, {})

            return result

    def load_as(self, model_cls: type[T], *pathname: SourceLike, **kwargs) -> T:
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
            raise TypeError(
                "Loaded configuration must be a dictionary to load as a model"
            )
        return _hydrate(model_cls, data)

    def load_all(
        self,
        *pathname: Path | typing.Sequence[Path],
        encoding: str = None,
        interpolate: bool = None,
        sandbox: bool = None,
        allow_commands: bool = None,
        **reader_args: object,
    ) -> typing.Iterator[object]:
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
                set its own.
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
        for path in parse_sources(
            pathname,
            base_dir=self.base_dir,
            encoding=encoding,
            path_factory=self.path_factory,
            recursive=self.recursive,
        ):
            value = None
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
                        encoding=encoding,
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
                        )
                value = _to_dot_access(value, {})
                yield value

            except (
                Exception
            ) as error:  # noqa: BLE001 - feeds the ignore_error predicate
                logger.debug("load_all error for %s: %s", path, error)
                if not self.ignore_error(error, path=path, value=value, loader=self):
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

    def __getattr__(self, name: str) -> object:
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
        self, key: typing.Hashable, default: object = None, dig: bool = True
    ) -> object:
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


def load(fp: typing.Any, **kwargs) -> object:
    """Load configuration from a file pointer or file path.

    Keyword arguments :class:`ConfigLoader` accepts configure the loader (and so
    also apply to nested ``!include`` targets); every other keyword is passed to
    :meth:`ConfigLoader.load`, which forwards unknown ones to the backend.
    """
    loader_kwargs, load_kwargs = _split_loader_kwargs(kwargs)
    loader_inst = ConfigLoader(**loader_kwargs)
    return loader_inst.load(fp, **load_kwargs)


def loads(s: str | bytes, **kwargs) -> object:
    """Load configuration from a string or bytes in memory.

    Keyword arguments are routed as in :func:`load`.
    """
    loader_kwargs, load_kwargs = _split_loader_kwargs(kwargs)
    loader_inst = ConfigLoader(**loader_kwargs)
    # Use parse_sources inline memory doc marker
    marker = "#!\n"
    if isinstance(s, bytes):
        content = marker.encode("utf-8") + s
    else:
        content = marker + s
    return loader_inst.load(content, **load_kwargs)


def load_as(model_cls: type[T], *pathname: SourceLike, **kwargs) -> T:
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


def dump(obj: object, fp: typing.Any, **kwargs) -> None:
    """Dump configuration object to a file pointer or file path."""
    content = dumps(obj, **kwargs)
    if hasattr(fp, "write"):
        fp.write(content)
    else:
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)


def dumps(obj: object, **kwargs) -> str:
    """Serialize *obj* to a YAML string (always YAML; see :class:`~yaconfiglib.backends.yaml.YamlConfig`).

    A loaded configuration writes as a plain mapping, so the output can be
    loaded back. Call a backend instance's ``dumps()`` for another format.
    """
    from .backends.yaml import YamlConfig

    backend = YamlConfig()
    return backend.dumps(obj, **kwargs)


DEFAULT_LOADER = ConfigLoader()
