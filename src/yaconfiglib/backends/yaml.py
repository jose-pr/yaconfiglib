from __future__ import annotations

import logging
import os
import re
import typing

import yaml

try:
    from pathlib_next import Path, Pathname
except ImportError:
    from pathlib import Path

    Pathname = Path

from yaconfiglib.backends.base import ConfigBackend, _include_call
from yaconfiglib.errors import ErrorFrame, _add_error_context
from yaconfiglib.utils.source import _rebase_include_sources

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


class _PlainMappingDumper(yaml.Dumper):
    """Dumper that writes every ``dict`` subclass as a plain YAML mapping.

    Loaded configurations are `DotAccessibleDict`s, and PyYAML's default dumper
    tags an unknown dict subclass as ``!!python/object/new:...``, which its own
    safe loader then refuses. A multi-representer covers subclasses (including
    children wrapped lazily on attribute access) without touching the exact-type
    representers, so tuples, OrderedDicts and the rest dump exactly as before.
    ``add_multi_representer`` copies the table into this subclass: the global
    ``yaml.Dumper`` is left alone.
    """


_PlainMappingDumper.add_multi_representer(
    dict, yaml.representer.SafeRepresenter.represent_dict
)
# Exact `tuple` only: interpolation of a bare `{{ expr }}` can produce one, and
# PyYAML writes it as !!python/tuple, which this library's own loader refuses.
# A namedtuple keeps its own representer, like every other object.
_PlainMappingDumper.add_representer(
    tuple, yaml.representer.SafeRepresenter.represent_list
)


class YamlConfig(ConfigBackend):
    """Backend for ``*.yaml``/``*.yml`` files.

    Automatically registers ``!include`` and ``!load`` tag constructors on a
    private, yaconfiglib-owned subclass of the PyYAML loader class, so nested
    configuration files can be pulled in directly from YAML, e.g.
    ``database: !include "db.toml"``. A relative include path resolves against
    the directory of the document it is written in (see *origin*), and is read
    with the encoding of the load call unless it names its own. ``yaml.SafeLoader`` and any
    caller-supplied loader class are never modified. Registration happens once
    per owned class and only when a parent
    :class:`~yaconfiglib.loader.ConfigLoader` is supplied via ``loader=``.
    """

    PATHNAME_REGEX = re.compile(r".*\.((yaml)|(yml))$", re.IGNORECASE)
    DEFAULT_LOADER_CLS = _IncludeSafeLoader
    DEFAULT_DUMPER_CLS = _PlainMappingDumper

    def load(
        self,
        path: "typing.Union[str, os.PathLike]",
        encoding: typing.Optional[str] = None,
        master: "typing.Optional[yaml.Loader]" = None,
        loader_cls: "typing.Optional[typing.Type[yaml.Loader]]" = None,
        path_factory: "typing.Optional[typing.Callable[[str], os.PathLike]]" = None,
        loader: "typing.Optional[ConfigBackend]" = None,
        origin: "typing.Optional[typing.Union[str, os.PathLike]]" = None,
        **options: typing.Any,
    ) -> typing.Any:
        """Parse *path* as YAML and return the resulting object.

        Args:
            path: File to parse, either a ``Path`` or a string (converted
                via *path_factory*).
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
                Also the default for this document's own includes.
            master: An in-progress PyYAML loader instance to inherit
                anchors/aliases from — used when this call originates from
                a ``!include``/``!load`` tag within another YAML document.
                It also carries the load call's include encoding.
            loader_cls: PyYAML loader class to use. Defaults to *master*'s
                class if given, else :attr:`DEFAULT_LOADER_CLS`. Parsing uses a
                private subclass of it, so the class itself is never modified.
            path_factory: Path constructor used when *path* is a string.
            loader: The parent :class:`~yaconfiglib.loader.ConfigLoader`.
                When supplied, ``!include``/``!load`` tags are registered
                on the owned subclass of *loader_cls* so nested includes
                resolve through it.
            origin: The document relative ``!include``/``!load`` paths in
                *path* resolve against. Defaults to *path* itself; a
                rendered template passes the template's path.

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

        try:
            loader_instance = loader_cls(path.read_text(encoding=encoding))
        except yaml.reader.ReaderError as error:
            # Raised from inside the constructor (a non-printable byte), so
            # there is no instance whose name could be set.
            error.name = str(path)
            raise
        # Every mark PyYAML makes from here on names this file.
        loader_instance.name = str(path)
        # Make the driving ConfigLoader reachable from the include constructor
        # (see _register_include_tags._construct) so nested !include/!load
        # resolve through THIS loader's settings, not the first one registered.
        if loader is not None:
            loader_instance._yaconfiglib_config_loader = loader
        # Relative includes in this document resolve against its own directory.
        # The origin is per-document, like the parser instance; the ConfigLoader
        # is shared across depths and cannot carry it.
        if origin is None:
            origin = path
        elif isinstance(origin, str):
            origin = path_factory(origin)
        loader_instance._yaconfiglib_include_origin = origin
        # The encoding of the load call that started this parse reaches every
        # include, at every depth. A mapping-form `encoding:` applies to its own
        # target only, so the inherited value keeps travelling past it.
        inherited = getattr(master, "_yaconfiglib_include_encoding", None)
        loader_instance._yaconfiglib_include_encoding = inherited or encoding
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
            # Shared with ConfigBackend._yaml_tag_constructor: one reading of
            # the three documented forms, and one error for a malformed one. A
            # document may only pass the allowlisted options; trust settings
            # reach the nested load through the effective policy instead.
            pathname, args, kwargs = _include_call(ldr, node)

            kwargs["master"] = ldr
            # An included document is part of a bigger one: the driving load
            # interpolates the merged result once, in its own scope.
            kwargs["interpolate"] = False
            # Absent or null `encoding:` on the include means "whatever this load
            # call is using"; an explicit one wins for that target.
            if kwargs.get("encoding") is None and getattr(
                ldr, "_yaconfiglib_include_encoding", None
            ):
                kwargs["encoding"] = ldr._yaconfiglib_include_encoding
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
            # Rebase after the allowlist filter and before the loader's cycle
            # check, so the cycle key is the resolved absolute path: the same
            # relative name at two depths is two different files.
            origin = getattr(ldr, "_yaconfiglib_include_origin", None)
            pathname = _rebase_include_sources(pathname, origin)
            if args:
                args = tuple(_rebase_include_sources(arg, origin) for arg in args)
            try:
                return active.load(pathname, *args, **kwargs)
            except Exception as error:  # noqa: BLE001 - adds context, re-raises
                # Deliberately every type: any load failure must say which
                # document included the file, and narrowing would drop exactly
                # the backend errors this attribution exists for.
                _add_error_context(
                    error,
                    frame=ErrorFrame(
                        "include", node.start_mark.name, node.start_mark.line + 1
                    ),
                )
                raise

        for tag in _INCLUDE_TAGS:
            loader_cls.add_constructor(tag, _construct)

        loader_cls._yaconfiglib_include_registered = True  # type: ignore[attr-defined]

    def dumps(
        self,
        data: typing.Any,
        dumper_cls: "typing.Optional[typing.Type[yaml.Dumper]]" = None,
        **options: typing.Any,
    ) -> str:
        """Serialize *data* to a YAML string using *dumper_cls*.

        Defaults to :attr:`DEFAULT_DUMPER_CLS`, which writes every ``dict``
        subclass — a loaded :class:`~yaconfiglib.loader.DotAccessibleDict`
        included — as a plain mapping, so the output loads back, and a
        ``tuple`` as a plain sequence. Every other Python object keeps
        PyYAML's own tag (a `~decimal.Decimal` stays
        ``!!python/object/apply:decimal.Decimal``). A *dumper_cls* (or a
        ``Dumper=`` option) of your own is used as given, which restores
        PyYAML's representers.

        Two `yaml.dump` keywords default differently here, whatever dumper is
        used: ``sort_keys=False``, because a configuration's key order is
        written on purpose, and ``allow_unicode=True``, so text stays readable
        instead of being escaped. Pass either explicitly to get PyYAML's
        behaviour back.

        Raises:
            TypeError: If ``encoding=`` is passed. This returns `str`; encode
                the result yourself, or use
                :func:`~yaconfiglib.loader.dump` with ``encoding=``.
        """
        if "encoding" in options:
            raise TypeError(
                "dumps() returns str and does not accept encoding=; call "
                ".encode() on the result, or use dump(obj, fp, encoding=...)"
            )
        options.setdefault("Dumper", dumper_cls or self.DEFAULT_DUMPER_CLS)
        options.setdefault("sort_keys", False)
        options.setdefault("allow_unicode", True)
        return yaml.dump(data, **options)
