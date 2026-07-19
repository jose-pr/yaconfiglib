from __future__ import annotations

import re as _re
import typing as _ty

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

    def __call__(self, *args, **kwds):
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
        args = ()
        kwargs = {}
        pathname: str | _Pathname | _ty.Sequence[str | _Pathname]
        if isinstance(node, _yaml.nodes.ScalarNode):
            pathname = loader.construct_scalar(node)
        elif isinstance(node, _yaml.nodes.SequenceNode):
            pathname, *args = loader.construct_sequence(node, deep=True)
        elif isinstance(node, _yaml.nodes.MappingNode):
            kwargs = loader.construct_mapping(node, deep=True)
            pathname = kwargs.pop("pathname")
        else:
            raise TypeError(f"Un-supported YAML node {node!r}")

        return self.load(pathname, *args, **kwargs, master=loader)

    def load(self, path: _Path, **options) -> object:
        """Read *path* and return the parsed configuration object.

        Subclasses must override this. Implementations typically accept
        additional keyword-only options specific to their format (e.g.
        ``encoding``); unrecognized options should generally be ignored via
        ``**options`` rather than raising, since :class:`~yaconfiglib.loader.ConfigLoader`
        forwards a shared set of options to every backend it invokes.
        """
        raise NotImplementedError()

    def load_all(self, path: _Path, **options) -> _ty.Iterable[object]:
        """Yield one or more parsed documents from *path*.

        The default implementation yields a single document produced by
        :meth:`load`. Backends that support multi-document sources (e.g. a
        directory or a multi-document YAML stream) should override this.
        """
        yield self.load(path, **options)

    def dumps(self, data: str, **options) -> str:
        """Serialize *data* back to this backend's text format.

        Optional — only needed for backends used with
        :func:`yaconfiglib.dump`/:func:`yaconfiglib.dumps`. Raises
        :class:`NotImplementedError` by default.
        """
        raise NotImplementedError

    @classmethod
    def __subclasses__(cls, *, recursive=False) -> list[type[_ty.Self]]:
        """Return direct (or, if *recursive*, all transitive) subclasses.

        Overrides :meth:`type.__subclasses__` to add the *recursive* flag,
        which :meth:`get_class_by_name` and :meth:`get_class_by_path` use
        to discover every registered backend regardless of how deep its
        class hierarchy is.
        """
        direct: list[type[_ty.Self]] = type.__subclasses__(cls)
        if not recursive:
            return direct
        # Deterministic, definition-order walk (depth-first, deduplicated).
        # The previous implementation returned a set, which made
        # get_class_by_path()'s "first match wins" depend on hash order —
        # backend resolution could differ between runs when two backends'
        # regexes both matched a path.
        ordered: list[type[_ty.Self]] = []
        for scls in direct:
            if scls not in ordered:
                ordered.append(scls)
            for nested in scls.__subclasses__(recursive=True):
                if nested not in ordered:
                    ordered.append(nested)
        return ordered

    @classmethod
    def get_class_by_name(cls, name: str) -> type[_ty.Self]:
        """Look up a registered backend class by its :attr:`NAME`.

        Falls back to a derived name (lowercased class name with a
        trailing ``Loader``/``Config`` suffix stripped) for backends that
        don't set :attr:`NAME` explicitly. Used when a caller passes
        ``loader="yaml"`` (or similar) instead of a backend instance.
        """
        for scls in cls.__subclasses__(recursive=True):
            _name = getattr(scls, "NAME", None)
            if not _name:
                _name = (
                    scls.__name__.lower().removesuffix("loader").removesuffix("config")
                )
            if _name == name:
                return scls

    @classmethod
    def can_load_path(cls, path: _Path) -> bool:
        """Return True if this backend's :attr:`PATHNAME_REGEX` matches *path*'s filename."""
        return (
            cls.PATHNAME_REGEX.match(path.name) is not None
            if cls.PATHNAME_REGEX
            else False
        )

    @classmethod
    def get_class_by_path(cls, path: _Path):
        """Find the first registered backend class whose :meth:`can_load_path` matches *path*.

        Raises:
            NotImplementedError: If no registered backend claims *path*.
        """
        for scls in cls.__subclasses__(recursive=True):
            if scls.can_load_path(path):
                return scls
        raise NotImplementedError(f"Not reader for {path}")
