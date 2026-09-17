from __future__ import annotations

import logging
import os as _os
import re as _re
import typing as _ty

from ..errors import ConfigTypeError as _ConfigTypeError
from ..errors import UnsupportedFormatError as _UnsupportedFormatError

try:
    from pathlib_next import LocalPath as _LocalPath
    from pathlib_next import Path as _Path
    from pathlib_next import Pathname as _Pathname
except ImportError:
    from pathlib import Path as _Path  # type: ignore[no-redef]

    _LocalPath = _Path
    _Pathname = _Path

if _ty.TYPE_CHECKING:
    import yaml as _yaml
else:

    try:
        import yaml as _yaml
    except ImportError:
        _yaml = None
        ...

logger = logging.getLogger(__name__)

#: `typing.Self` is 3.11+, and the floor is 3.9: a bound TypeVar keeps the
#: subclass precision (`YamlConfig.get_class_by_name` is typed as returning a
#: `YamlConfig` subclass) while resolving with `get_type_hints` everywhere.
_BackendT = _ty.TypeVar("_BackendT", bound="ConfigBackend")

#: Keyword arguments an ``!include``/``!load`` mapping node may pass to the nested
#: load. Everything else a document supplies (``allow_commands``, ``sandbox``,
#: ``interpolate``, ``loader``, ``master``, ``environment``, unknown keys) is
#: dropped: an included document can only narrow what the caller allowed.
_INCLUDE_KWARGS = frozenset(
    (
        "encoding",
        "transform",
        "default",
        "flatten",
        "merge",
        "merge_options",
        "recursive",
    )
)


#: Optional backends whose import failed, as
#: ``{name: (compiled PATHNAME_REGEX, hint, reason)}``. Populated by
#: ``backends/__init__.py``, which is where the imports are attempted.
_MISSING_BACKENDS: "dict[str, tuple[_re.Pattern, str, str]]" = {}


def _record_missing_backend(name: str, pattern: str, hint: str, reason: str) -> None:
    """Remember that an optional backend could not be imported.

    A missing backend stays unregistered — that keeps dispatch, and dotenv's
    give-way rule, exactly as they are — so the only thing left to improve is
    the error, which would otherwise say a format is unknown without saying
    why.
    """
    _MISSING_BACKENDS[name] = (_re.compile(pattern, _re.IGNORECASE), hint, reason)


def _include_call(loader: "_yaml.Loader", node: "_yaml.Node") -> tuple:
    """Read an ``!include``/``!load`` node as ``(pathname, args, kwargs)``.

    One place for the three documented forms, so the tag constructor and the
    loader-driven one cannot disagree about them. A malformed form raises
    PyYAML's own `ConstructorError`, whose mark names the file and line —
    where a mapping without ``pathname`` used to surface as a bare
    ``KeyError: 'pathname'`` and an empty sequence as an unpacking
    `ValueError`, neither of which said where.

    Validation runs **before** the allowlist filter, so a document that omits
    ``pathname`` is reported as malformed rather than as a dropped key.
    """
    args: tuple = ()
    kwargs: dict = {}
    if isinstance(node, _yaml.nodes.ScalarNode):
        pathname = loader.construct_scalar(node)
    elif isinstance(node, _yaml.nodes.SequenceNode):
        items = loader.construct_sequence(node, deep=True)
        if not items:
            raise _yaml.constructor.ConstructorError(
                None,
                None,
                "an !include/!load sequence needs a path as its first item",
                node.start_mark,
            )
        pathname, *rest = items
        args = tuple(rest)
    elif isinstance(node, _yaml.nodes.MappingNode):
        kwargs = loader.construct_mapping(node, deep=True)
        pathname = kwargs.pop("pathname", None)
        if not pathname:
            raise _yaml.constructor.ConstructorError(
                None,
                None,
                "an !include/!load mapping needs a non-empty 'pathname' key",
                node.start_mark,
            )
        kwargs = _filter_include_kwargs(kwargs)
    else:
        raise _ConfigTypeError(f"Un-supported YAML node {node!r}")
    return pathname, args, kwargs


def _filter_include_kwargs(kwargs: dict) -> dict:
    """Keep only the include-mapping keys a document is allowed to set.

    ``key_factory`` is kept only in its ``"%<jinja-expr>"`` form: the plain-name
    form calls any zero-argument attribute of the included ``Path`` (``unlink``
    deletes the file). Dropped keys are logged once, by name, at WARNING.
    """
    kept = {}
    dropped = []
    for key, value in kwargs.items():
        if key in _INCLUDE_KWARGS or (
            key == "key_factory" and isinstance(value, str) and value.startswith("%")
        ):
            kept[key] = value
        else:
            dropped.append(key)
    if dropped:
        logger.warning(
            "ignoring !include/!load option(s) not allowed in a document: %s",
            ", ".join(sorted(dropped)),
        )
    return kept


class ConfigBackend(_ty.Protocol):
    """Base contract for a pluggable configuration format backend.

    A backend is responsible for turning a source (typically a file path,
    but also strings, streams, or in-memory data depending on the backend)
    into a Python object — usually a ``dict``. Backends are looked up and
    instantiated automatically by :class:`~yaconfiglib.loader.ConfigLoader`
    based on either an explicit ``loader=`` name/instance or by matching
    :attr:`PATHNAME_REGEX` against the source path.

    To implement a new backend, subclass :class:`ConfigBackend` and override
    :meth:`load` (required) and optionally :meth:`dumps` (for round-trip
    serialization support). Subclasses are auto-discovered — simply
    importing the module that defines the subclass registers it; no
    explicit registry call is needed. See ``yaconfiglib.backends`` for the
    built-in implementations (YAML, TOML, JSON, INI, dotenv, env, command,
    python, jinja2).

    Class attributes:
        PATHNAME_REGEX: Compiled regex matched against a path's filename
            (or, for scheme-based backends like ``CommandBackend``, the
            full path string) to decide whether this backend can handle a
            given source. Set to ``None`` for backends that are only
            selected explicitly by name (e.g. ``EnvVarBackend``).
        NAME: Explicit registry name used by ``loader="name"`` lookups. If
            unset, the name is derived from the class name by lowercasing
            it and stripping a trailing ``Loader``/``Config`` suffix.
        DEFAULT_ENCODING: Text encoding used when a backend reads a file
            and no explicit ``encoding=`` is supplied.
        DEFAULT_PATH_FACTORY: Path constructor used to build path objects
            when the caller passes a bare string rather than a ``Path``.
    """

    PATHNAME_REGEX: _re.Pattern = None
    NAME: str = None
    DEFAULT_ENCODING = "utf-8"
    DEFAULT_PATH_FACTORY = _LocalPath

    def _coerce_path(
        self,
        path: _ty.Union[_Path, str],
        path_factory: _ty.Optional[_ty.Callable[[str], _Path]] = None,
    ) -> _Path:
        """Turn a string *path* into a path object, one rule for every backend.

        A backend that reads files must accept a ``str``: a PyYAML tag
        constructor hands one over, and so does any direct call.
        """
        if path_factory and not isinstance(path, _Path):
            return path_factory(path)
        if isinstance(path, str):
            return (path_factory or self.DEFAULT_PATH_FACTORY)(path)
        return path

    def _read_text(self, path: _Path, encoding: _ty.Optional[str] = None) -> str:
        """Read *path* as text, dropping a leading byte-order mark.

        A U+FEFF left at the start of decoded text is always a BOM artifact —
        the character has no other use there — so one is stripped whatever the
        encoding. Decoding with ``utf-8-sig`` instead is not an option:
        :attr:`DEFAULT_ENCODING` is also used to *write* the ``#!`` marker of
        an in-memory source, and ``utf-8-sig`` would add a BOM there.
        """
        text = path.read_text(encoding=encoding or self.DEFAULT_ENCODING)
        return text[1:] if text.startswith("\ufeff") else text

    def __call__(self, *args: _ty.Any, **kwds: _ty.Any) -> _ty.Any:
        """Dispatch to :meth:`_yaml_tag_constructor` when used as a PyYAML tag constructor.

        This lets a backend instance be registered directly with
        ``yaml.Loader.add_constructor`` (e.g. for ``!include``/``!load``
        tags) — PyYAML calls constructors as ``constructor(loader, node)``,
        which this method recognizes and routes accordingly.
        """
        if (
            len(args) == 2
            and _yaml is not None
            and isinstance(args[0], _yaml.constructor.BaseConstructor)
        ):
            return self._yaml_tag_constructor(*args, **kwds)

    def _yaml_tag_constructor(self, loader: "_yaml.Loader", node: "_yaml.Node"):
        """Build a :meth:`load` call from a YAML ``!include``/``!load`` node.

        Supports scalar nodes (``!include path``), sequence nodes
        (``!include [path, arg1, ...]``), and mapping nodes
        (``!include {pathname: path, ...}``), converting each to the
        equivalent ``self.load(pathname, *args, **kwargs, master=loader)``
        call.
        """
        pathname, args, kwargs = _include_call(loader, node)
        return self.load(pathname, *args, **kwargs, master=loader)

    def load(self, path: "_ty.Any", **options: _ty.Any) -> _ty.Any:
        """Read *path* and return the parsed configuration object.

        Subclasses must override this. Implementations typically accept
        additional keyword-only options specific to their format (e.g.
        ``encoding``); unrecognized options should generally be ignored via
        ``**options`` rather than raising, since :class:`~yaconfiglib.loader.ConfigLoader`
        forwards a shared set of options to every backend it invokes.
        """
        raise NotImplementedError()

    def load_all(self, path: "_ty.Any", **options: _ty.Any) -> "_ty.Iterable[_ty.Any]":
        """Yield one or more parsed documents from *path*.

        The default implementation yields a single document produced by
        :meth:`load`. Backends that support multi-document sources (e.g. a
        directory or a multi-document YAML stream) should override this.
        """
        yield self.load(path, **options)

    def dumps(self, data: _ty.Any, **options: _ty.Any) -> str:
        """Serialize *data* back to this backend's text format.

        Optional. Note that :func:`yaconfiglib.dump`/:func:`yaconfiglib.dumps`
        always write YAML; call a backend instance's ``dumps()`` directly to
        serialize to another format. Raises :class:`NotImplementedError` by
        default.
        """
        raise NotImplementedError

    @classmethod
    def __subclasses__(
        cls: "_ty.Type[_BackendT]", *, recursive: bool = False
    ) -> "_ty.List[_ty.Type[_BackendT]]":
        """Return direct (or, if *recursive*, all transitive) subclasses.

        Overrides :meth:`type.__subclasses__` to add the *recursive* flag,
        which :meth:`get_class_by_name` and :meth:`get_class_by_path` use
        to discover every registered backend regardless of how deep its
        class hierarchy is.
        """
        direct: "_ty.List[_ty.Type[_BackendT]]" = type.__subclasses__(cls)
        if not recursive:
            return direct
        # Deterministic, definition-order walk (depth-first, deduplicated).
        # The previous implementation returned a set, which made
        # get_class_by_path()'s "first match wins" depend on hash order —
        # backend resolution could differ between runs when two backends'
        # regexes both matched a path.
        ordered: "_ty.List[_ty.Type[_BackendT]]" = []
        for scls in direct:
            if scls not in ordered:
                ordered.append(scls)
            for nested in scls.__subclasses__(recursive=True):
                if nested not in ordered:
                    ordered.append(nested)
        return ordered

    @classmethod
    def get_class_by_name(
        cls: "_ty.Type[_BackendT]", name: str
    ) -> "_ty.Optional[_ty.Type[_BackendT]]":
        """Look up a registered backend class by its :attr:`NAME`.

        Falls back to a derived name (lowercased class name with a
        trailing ``Loader``/``Config`` suffix stripped) for backends that
        don't set :attr:`NAME` explicitly. Used when a caller passes
        ``loader="yaml"`` (or similar) instead of a backend instance.
        """
        for scls in cls.__subclasses__(recursive=True):
            if cls._derived_name(scls) == name:
                return scls

    @staticmethod
    def _derived_name(backend_cls: type) -> str:
        """The name ``loader=`` accepts for *backend_cls*.

        An explicit :attr:`NAME` wins; otherwise the class name, lowercased with
        a trailing ``Loader``/``Config`` stripped. One helper, so a lookup by
        name and the name list in an error message cannot drift apart.
        """
        name = getattr(backend_cls, "NAME", None)
        if not name:
            name = (
                backend_cls.__name__.lower()
                .removesuffix("loader")
                .removesuffix("config")
            )
        return name

    @classmethod
    def _registered_names(cls) -> "_ty.List[str]":
        """Every name ``loader=`` currently accepts, sorted and de-duplicated.

        Empty names are dropped: `ConfigLoader` itself derives ``""``.
        """
        names = {cls._derived_name(scls) for scls in cls.__subclasses__(recursive=True)}
        return sorted(name for name in names if name)

    @classmethod
    def can_load_path(cls, path: "_ty.Union[str, _os.PathLike]") -> bool:
        """Return True if this backend's :attr:`PATHNAME_REGEX` matches *path*'s filename."""
        return (
            cls.PATHNAME_REGEX.match(path.name) is not None
            if cls.PATHNAME_REGEX
            else False
        )

    @classmethod
    def _missing_backend_hint(
        cls,
        name: "_ty.Optional[str]" = None,
        path: "_ty.Optional[_Path]" = None,
    ) -> str:
        """Explain an unavailable optional backend, or return ``""``.

        Private: the hint is appended to existing error messages, whose types
        and prefixes do not change.
        """
        for backend, (pattern, hint, reason) in _MISSING_BACKENDS.items():
            if name is not None and name != backend:
                continue
            if path is not None and pattern.match(path.name) is None:
                continue
            if name is None and path is None:
                continue
            return f"; {hint} ({reason})"
        return ""

    @classmethod
    def get_class_by_path(
        cls: "_ty.Type[_BackendT]", path: "_ty.Union[str, _os.PathLike]"
    ) -> "_ty.Type[_BackendT]":
        """Find the first registered backend class whose :meth:`can_load_path` matches *path*.

        Raises:
            UnsupportedFormatError: If no registered backend claims *path*.
                Also a `NotImplementedError`, which is what this raised
                before. The message names the extra to install when the
                format's optional dependency is what is missing, and lists the
                names ``loader=`` accepts.
        """
        for scls in cls.__subclasses__(recursive=True):
            if scls.can_load_path(path):
                return scls
        raise _UnsupportedFormatError(
            f"No backend reads {path}{cls._missing_backend_hint(path=path)}"
            f"; pass loader=<name> (registered: {', '.join(cls._registered_names())})"
        )
