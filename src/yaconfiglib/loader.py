from __future__ import annotations

import contextvars
import logging
import os
import types
import typing

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

from pathlib import PurePosixPath

try:
    from .utils import jinja2
except ImportError:
    jinja2 = None

from .backends import ConfigBackend
from .backends.command import CommandBackend
from .utils.enum import IntEnum
from .utils.log import LogLevel
from .utils.merge import Merge, MergeMethod, is_array
from .utils.source import SourceLike, parse_sources
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
    "load",
    "loads",
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
      merge key (see ``key_factory``).

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
        if self is ConfigLoaderMergeMethod.List:
            return [initial]
        elif self is ConfigLoaderMergeMethod.Hash:
            return {configloaderkey: initial}
        else:
            return initial

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


class _IgnoreError(typing.Protocol):
    def __call__(self, error: Exception, *args, **kwargs) -> bool: ...


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
        log_level: int | LogLevel = LogLevel.Warning,
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
            log_level: Logging verbosity for this loader's module logger.
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
        # Stored for introspection only. Library code must never call
        # logger.setLevel() on the module logger: doing it here made every
        # ConfigLoader construction (including the module-import-time
        # DEFAULT_LOADER) mutate global logging state, and two loaders with
        # different levels fought over one logger. Callers configure logging.
        self._log_level = LogLevel(log_level or LogLevel.Warning)
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
                raise ValueError(f"Unknown configuration format/loader: {loader}")
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
                    return _eval(value=value, pathname=PurePosixPath(path.as_posix()))

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
                value=value, pathname=PurePosixPath(path.as_posix())
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
            default: Initial value merged against, used when *pathname*
                yields no sources.
            key_factory: Overrides the instance's *key_factory* for this
                call.
            flatten: If True, the final merged result (expected to be a
                mapping-of-mappings or sequence-of-sequences) is flattened
                one level — useful when each source contributes items to a
                shared top-level collection instead of being keyed by
                itself.
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
                        try:
                            results = merge.init(
                                initial=result,
                                configloaderkey=name,
                                **merge_options,
                            )
                        except AttributeError:
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
                    result = {
                        prop: value
                        for _key, result in results.items()
                        for prop, value in result.items()
                    }
                elif is_array(results):
                    result = [r for result in results for r in result]
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

            # Wrap dict results in a helper class that supports dot-notation
            if isinstance(result, dict):
                result = DotAccessibleDict(result)

            return result

    def load_as(self, model_cls: type[T], *pathname: SourceLike, **kwargs) -> T:
        """Load configuration sources and instantiate as *model_cls*.

        Supports Pydantic models (if installed) or dataclasses. If neither matches,
        falls back to passing kwargs/dict unpacking to the constructor.
        """
        data = self.load(*pathname, **kwargs)
        if not isinstance(data, dict):
            raise TypeError(
                "Loaded configuration must be a dictionary to load as a model"
            )

        # Try Pydantic integration (strictly optional)
        try:
            import pydantic

            if issubclass(model_cls, pydantic.BaseModel):
                # Pydantic V2 and V1 compatibility helper
                if hasattr(model_cls, "model_validate"):
                    return model_cls.model_validate(data)
                elif hasattr(model_cls, "parse_obj"):
                    return model_cls.parse_obj(data)
        except ImportError:
            pass

        # Try dataclass
        from dataclasses import is_dataclass

        if is_dataclass(model_cls):
            # Safe init passing only valid dataclass field names
            import inspect

            sig = inspect.signature(model_cls.__init__)
            valid_keys = {
                name
                for name, param in sig.parameters.items()
                if param.kind
                in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
            }
            filtered = {k: v for k, v in data.items() if k in valid_keys}
            return model_cls(**filtered)

        return model_cls(**data)

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
                if isinstance(value, dict):
                    value = DotAccessibleDict(value)
                yield value

            except (
                Exception
            ) as error:  # noqa: BLE001 - feeds the ignore_error predicate
                logger.debug("load_all error for %s: %s", path, error)
                if not self.ignore_error(error, path=path, value=value, loader=self):
                    raise


class DotAccessibleDict(dict):
    """Dictionary subclass supporting dot-notation queries and attribute access."""

    def __getattr__(self, name: str) -> object:
        try:
            val = self[name]
            if isinstance(val, dict) and not isinstance(val, DotAccessibleDict):
                val = DotAccessibleDict(val)
                self[name] = val
            return val
        except KeyError:
            raise AttributeError(
                f"'DotAccessibleDict' object has no attribute '{name}'"
            )

    def __setattr__(self, name: str, value: object) -> None:
        self[name] = value

    def get(self, key: str, default: object = None, dig: bool = True) -> object:
        """Support dot-notation traversal, e.g., get("database.credentials.user", dig=True)."""
        if key in self:
            val = super().get(key, default)
            if isinstance(val, dict) and not isinstance(val, DotAccessibleDict):
                val = DotAccessibleDict(val)
                self[key] = val
            return val

        if dig and "." in key:
            parts = key.split(".")
            current = self
            for part in parts:
                if not isinstance(current, dict):
                    return default
                parent = current
                try:
                    current = current[part]
                except KeyError:
                    return default
                if current is None:
                    return default
                if isinstance(current, dict) and not isinstance(
                    current, DotAccessibleDict
                ):
                    current = DotAccessibleDict(current)
                    parent[part] = current
            return current
        val = super().get(key, default)
        if isinstance(val, dict) and not isinstance(val, DotAccessibleDict):
            val = DotAccessibleDict(val)
            self[key] = val
        return val


def load(fp: typing.Any, **kwargs) -> object:
    """Load configuration from a file pointer or file path."""
    load_keys = {
        "recursive",
        "encoding",
        "loader",
        "transform",
        "default",
        "key_factory",
        "flatten",
        "interpolate",
        "merge",
        "merge_options",
        "master",
    }
    loader_kwargs = {k: v for k, v in kwargs.items() if k not in load_keys}
    load_kwargs = {k: v for k, v in kwargs.items() if k in load_keys}
    loader_inst = ConfigLoader(**loader_kwargs)
    return loader_inst.load(fp, **load_kwargs)


def loads(s: str | bytes, **kwargs) -> object:
    """Load configuration from a string or bytes in memory."""
    load_keys = {
        "recursive",
        "encoding",
        "loader",
        "transform",
        "default",
        "key_factory",
        "flatten",
        "interpolate",
        "merge",
        "merge_options",
        "master",
    }
    loader_kwargs = {k: v for k, v in kwargs.items() if k not in load_keys}
    load_kwargs = {k: v for k, v in kwargs.items() if k in load_keys}
    loader_inst = ConfigLoader(**loader_kwargs)
    # Use parse_sources inline memory doc marker
    marker = "#!\n"
    if isinstance(s, bytes):
        content = marker.encode("utf-8") + s
    else:
        content = marker + s
    return loader_inst.load(content, **load_kwargs)


def dump(obj: object, fp: typing.Any, **kwargs) -> None:
    """Dump configuration object to a file pointer or file path."""
    content = dumps(obj, **kwargs)
    if hasattr(fp, "write"):
        fp.write(content)
    else:
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)


def dumps(obj: object, **kwargs) -> str:
    """Dump configuration object to string (delegates to YamlConfig dumper by default)."""
    from .backends.yaml import YamlConfig

    backend = YamlConfig()
    return backend.dumps(obj, **kwargs)


DEFAULT_LOADER = ConfigLoader()
