from __future__ import annotations

import logging
import re
import typing

import yaml

try:
    from pathlib_next import Path, Pathname
except ImportError:
    from pathlib import Path

    Pathname = Path

from yaconfiglib.backends.base import ConfigBackend, _filter_include_kwargs

logger = logging.getLogger(__name__)

__all__ = ["YamlConfig"]

# Tags registered automatically so users can write !include / !load without manual
# loader setup. They are only ever registered on yaconfiglib-owned loader classes
# (see _owned_loader_cls), never on yaml.SafeLoader or a caller-supplied class.
_INCLUDE_TAGS = ("!include", "!load")


class _IncludeSafeLoader(yaml.SafeLoader):
    """yaconfiglib's own SafeLoader subclass, the one that carries ``!include``/``!load``."""

    _yaconfiglib_owned = True


# Owned subclasses generated for other loader bases (a caller's ``loader_cls=`` or
# the class of a ``master`` loader), keyed by that base.
_OWNED_LOADERS: dict = {}


def _owned_loader_cls(base: type) -> type:
    """Return a yaconfiglib-owned loader class for *base*, never *base* itself unless owned.

    Registering the include constructors mutates the class-level
    ``yaml_constructors`` table, so doing it on ``yaml.SafeLoader`` made every
    later ``yaml.safe_load`` in the process resolve ``!include``. Ownership is
    read from the class's own ``__dict__`` so a user subclass of an owned class
    is wrapped too, rather than mutated.
    """
    if base.__dict__.get("_yaconfiglib_owned"):
        return base
    owned = _OWNED_LOADERS.get(base)
    if owned is None:
        owned = type(
            "_Yaconfiglib" + base.__name__, (base,), {"_yaconfiglib_owned": True}
        )
        _OWNED_LOADERS[base] = owned
    return owned


class YamlConfig(ConfigBackend):
    """Backend for ``*.yaml``/``*.yml`` files.

    Automatically registers ``!include`` and ``!load`` tag constructors on a
    private, yaconfiglib-owned subclass of the PyYAML loader class, so nested
    configuration files can be pulled in directly from YAML, e.g.
    ``database: !include "db.toml"``. ``yaml.SafeLoader`` and any
    caller-supplied loader class are never modified. Registration happens once
    per owned class and only when a parent
    :class:`~yaconfiglib.loader.ConfigLoader` is supplied via ``loader=``.
    """

    PATHNAME_REGEX = re.compile(r".*\.((yaml)|(yml))$", re.IGNORECASE)
    DEFAULT_LOADER_CLS = _IncludeSafeLoader
    DEFAULT_DUMPER_CLS = yaml.Dumper

    def load(
        self,
        path: Path | str,
        encoding: str = None,
        master: yaml.Loader = None,
        loader_cls: type[yaml.Loader] = None,
        path_factory: type[Path] = None,
        loader: ConfigBackend = None,
        **options,
    ) -> object:
        """Parse *path* as YAML and return the resulting object.

        Args:
            path: File to parse, either a ``Path`` or a string (converted
                via *path_factory*).
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
            master: An in-progress PyYAML loader instance to inherit
                anchors/aliases from — used when this call originates from
                a ``!include``/``!load`` tag within another YAML document.
            loader_cls: PyYAML loader class to use. Defaults to *master*'s
                class if given, else :attr:`DEFAULT_LOADER_CLS`. Parsing uses a
                private subclass of it, so the class itself is never modified.
            path_factory: Path constructor used when *path* is a string.
            loader: The parent :class:`~yaconfiglib.loader.ConfigLoader`.
                When supplied, ``!include``/``!load`` tags are registered
                on the owned subclass of *loader_cls* so nested includes
                resolve through it.

        Returns:
            The parsed YAML document (typically a ``dict``, ``list``, or
            scalar).
        """
        encoding = encoding or self.DEFAULT_ENCODING

        if path_factory is None:
            path_factory = self.DEFAULT_PATH_FACTORY
        if isinstance(path, str):
            path = path_factory(path)
        if master and not loader_cls:
            loader_cls = type(master)
        if loader_cls is None:
            loader_cls = self.DEFAULT_LOADER_CLS
        loader_cls = _owned_loader_cls(loader_cls)

        # Auto-register !include / !load tags if a loader is provided
        # and the tags haven't already been registered on this owned class.
        if loader is not None:
            self._register_include_tags(loader_cls, loader, path_factory)

        loader_instance = loader_cls(path.read_text(encoding=encoding))
        # Make the driving ConfigLoader reachable from the include constructor
        # (see _register_include_tags._construct) so nested !include/!load
        # resolve through THIS loader's settings, not the first one registered.
        if loader is not None:
            loader_instance._yaconfiglib_config_loader = loader
        try:
            if master:
                loader_instance.anchors = master.anchors
            data = loader_instance.get_single_data()
            return data
        finally:
            loader_instance.dispose()

    @staticmethod
    def _register_include_tags(
        loader_cls: type[yaml.Loader],
        loader,
        path_factory,
    ) -> None:
        """Register ``!include`` and ``!load`` constructors on *loader_cls*.

        Idempotent — safe to call multiple times; only registers once per class.
        The flag is read from the class's own ``__dict__``, so a subclass of a
        registered class still gets its own registration.
        """
        if loader_cls.__dict__.get("_yaconfiglib_include_registered", False):
            return

        # A consumer may have manually registered an !include/!load constructor
        # (e.g. ``yaml.add_constructor("!include", ...)``). yaconfiglib's own
        # registration is authoritative and replaces it; warn so the override is
        # visible -- manual registration is unnecessary, since this runs
        # automatically on the first load through a ConfigLoader.
        # Inspect the class's OWN constructors (``__dict__``), not inherited
        # ones: a tag inherited from a parent is either PyYAML's default or
        # yaconfiglib's own earlier registration, neither of which is a foreign
        # override worth warning about. ``add_constructor`` always populates
        # ``cls.__dict__["yaml_constructors"]``, so a manual registration on
        # exactly this class (the pixy pattern) is caught here.
        own_constructors = loader_cls.__dict__.get("yaml_constructors", {})
        preexisting = [tag for tag in _INCLUDE_TAGS if tag in own_constructors]
        if preexisting:
            logger.warning(
                "overriding pre-existing YAML constructor(s) %s on %s with "
                "yaconfiglib's include handler; a manual "
                "yaml.add_constructor(%r, ...) is unnecessary (yaconfiglib "
                "registers !include/!load automatically on first load)",
                ", ".join(preexisting),
                loader_cls.__name__,
                _INCLUDE_TAGS[0],
            )

        def _construct(ldr: yaml.Loader, node: yaml.Node) -> object:
            args = ()
            kwargs = {}
            if isinstance(node, yaml.nodes.ScalarNode):
                pathname = ldr.construct_scalar(node)
            elif isinstance(node, yaml.nodes.SequenceNode):
                pathname, *args = ldr.construct_sequence(node, deep=True)
            elif isinstance(node, yaml.nodes.MappingNode):
                kwargs = ldr.construct_mapping(node, deep=True)
                pathname = kwargs.pop("pathname")
                # A document may only pass the allowlisted options; trust settings
                # reach the nested load through the effective policy instead.
                kwargs = _filter_include_kwargs(kwargs)
            else:
                raise TypeError(f"Un-supported YAML node {node!r}")

            kwargs["master"] = ldr
            # Route the include through the ConfigLoader driving THIS parse, never
            # one captured at registration time: that leaked the first loader's
            # settings (base_dir, allow_commands, merge, ...) into every later
            # loader. A parse with no driving ConfigLoader (e.g. a standalone
            # YamlConfig().load) gets no include support at all.
            active = getattr(ldr, "_yaconfiglib_config_loader", None)
            if active is None:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    "!include/!load are only available through a yaconfiglib ConfigLoader",
                    node.start_mark,
                )
            return active.load(pathname, *args, **kwargs)

        for tag in _INCLUDE_TAGS:
            loader_cls.add_constructor(tag, _construct)

        loader_cls._yaconfiglib_include_registered = True  # type: ignore[attr-defined]

    def dumps(self, data: str, dumper_cls: yaml.Dumper = None, **options) -> str:
        """Serialize *data* to a YAML string using *dumper_cls* (defaults to :attr:`DEFAULT_DUMPER_CLS`)."""
        options.setdefault("Dumper", dumper_cls or self.DEFAULT_DUMPER_CLS)
        return yaml.dump(data, **options)
