from __future__ import annotations

import logging
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
from .utils.enum import IntEnum
from .utils.log import Logger, LogLevel, getLogger
from .utils.merge import Merge, MergeMethod, is_array
from .utils.source import SourceLike, parse_sources

__all__ = ["ConfigLoader", "ConfigLoaderMergeMethod", "load", "loads", "dump", "dumps"]

logger = logging.getLogger(__name__)

T = typing.TypeVar("T")


_JINJA_ENVS = {}

def _get_jinja_env(strict: bool) -> object:
    if strict not in _JINJA_ENVS:
        from jinja2 import Environment, StrictUndefined
        env_kwargs = {}
        if strict:
            env_kwargs["undefined"] = StrictUndefined
        _JINJA_ENVS[strict] = Environment(extensions=["jinja2.ext.do"], **env_kwargs)
    return _JINJA_ENVS[strict]


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
    ) -> None:
        """Configure a reusable loader.

        Args:
            base_dir: Directory relative-path sources are resolved
                against. Accepts a string or ``Path``.
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
        """
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
        recursive: bool = None,
        loader: str = None,
        transform: str = None,
        key_factory: str | typing.Callable[[Path], str] = None,
        interpolate: bool = None,
        **reader_args,
    ) -> tuple[str, object]:

        recursive = self.recursive if recursive is None else recursive

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
                _eval = jinja2.eval(key_factory.removeprefix("%"))

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
        _options = dict(
            encoding=encoding,
            path_factory=self.path_factory,
            loader=self,
            base_dir=self.base_dir,
            interpolate=(
                False if (loader is self and self.interpolate) else interpolate
            ),
        )
        _options.update(reader_args)

        value = _loader.load(path, **_options)
        if transform:
            value = jinja2.eval(transform)(
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
            encoding: Overrides the instance's *encoding* for this call.
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
        merge = (
            merge
            if isinstance(merge, Merge)
            else (ConfigLoaderMergeMethod(merge) if merge else self.merge)
        )
        if not merge:
            merge = self.merge
        # Per-call override only — must NOT rewrite self.merge_options (doing so
        # made one call's override silently leak into every later load()).
        merge_options = (
            self.merge_options if merge_options is None else merge_options
        )

        results = default
        _join_init = False

        if not pathname:
            pathname = ("#!\n",)

        for path in parse_sources(
            pathname,
            base_dir=self.base_dir,
            encoding=encoding,
            path_factory=self.path_factory,
        ):
            try:
                name, result = self._load(
                    path,
                    recursive=recursive,
                    encoding=encoding,
                    loader=loader,
                    transform=transform,
                    key_factory=key_factory,
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
            except Exception as error:
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
            import os
            custom_env = _get_jinja_env(self.strict)

            # Auto-inject env context if requested
            globals_dict = {}
            if isinstance(result, typing.Mapping):
                globals_dict.update(result)
            if self.inject_env:
                globals_dict["env"] = os.environ

            try:
                result = jinja2.interpolate(
                    result,
                    globals=globals_dict,
                    environment=custom_env,
                )
            except Exception as error:
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
            raise TypeError("Loaded configuration must be a dictionary to load as a model")

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
            valid_keys = {name for name, param in sig.parameters.items() if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)}
            filtered = {k: v for k, v in data.items() if k in valid_keys}
            return model_cls(**filtered)

        return model_cls(**data)

    def load_all(
        self,
        *pathname: Path | typing.Sequence[Path],
        encoding: str = None,
        interpolate: bool = None,
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
            encoding: Overrides the instance's *encoding* for this call.
            interpolate: Overrides the instance's *interpolate* for this
                call; applied independently to each yielded document.
            **reader_args: Additional keyword arguments forwarded to each
                backend's ``load()``.

        Yields:
            Each source's parsed document, with dict results wrapped in
            :class:`DotAccessibleDict`.
        """
        interpolate = self.interpolate if interpolate is None else interpolate
        encoding = encoding or self.encoding
        custom_env = _get_jinja_env(self.strict) if interpolate else None
        for path in parse_sources(
            pathname,
            base_dir=self.base_dir,
            encoding=encoding,
            path_factory=self.path_factory,
        ):
            value = None
            try:
                key, value = self._load(
                    path,
                    encoding=encoding,
                    **reader_args,
                )
                if interpolate:
                    globals_dict = {}
                    if isinstance(value, typing.Mapping):
                        globals_dict.update(value)
                    if self.inject_env:
                        import os
                        globals_dict["env"] = os.environ
                    value = jinja2.interpolate(value, globals_dict, environment=custom_env)
                if isinstance(value, dict):
                    value = DotAccessibleDict(value)
                yield value

            except Exception as error:
                if not self.ignore_error(
                    error, path=path, value=value, loader=self
                ):
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
            raise AttributeError(f"'DotAccessibleDict' object has no attribute '{name}'")

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
                if isinstance(current, dict) and not isinstance(current, DotAccessibleDict):
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
    load_keys = {"recursive", "encoding", "loader", "transform", "default", "key_factory", "flatten", "interpolate", "merge", "merge_options", "master"}
    loader_kwargs = {k: v for k, v in kwargs.items() if k not in load_keys}
    load_kwargs = {k: v for k, v in kwargs.items() if k in load_keys}
    loader_inst = ConfigLoader(**loader_kwargs)
    return loader_inst.load(fp, **load_kwargs)


def loads(s: str | bytes, **kwargs) -> object:
    """Load configuration from a string or bytes in memory."""
    load_keys = {"recursive", "encoding", "loader", "transform", "default", "key_factory", "flatten", "interpolate", "merge", "merge_options", "master"}
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
