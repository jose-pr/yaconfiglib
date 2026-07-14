from __future__ import annotations

import re
import typing

import yaml
try:
    from pathlib_next import Path, Pathname
except ImportError:
    from pathlib import Path
    Pathname = Path

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["YamlConfig"]

# Tags automatically registered on SafeLoader so users can write !include / !load
# without manual loader setup.
_INCLUDE_TAGS = ("!include", "!load")


class YamlConfig(ConfigBackend):
    """Backend for ``*.yaml``/``*.yml`` files.

    Automatically registers ``!include`` and ``!load`` tag constructors on
    the active PyYAML loader class so nested configuration files can be
    pulled in directly from YAML, e.g. ``database: !include "db.toml"``.
    Registration happens once per loader class and only when a parent
    :class:`~yaconfiglib.loader.ConfigLoader` is supplied via ``loader=``.
    """

    PATHNAME_REGEX = re.compile(r".*\.((yaml)|(yml))$", re.IGNORECASE)
    DEFAULT_LOADER_CLS = yaml.SafeLoader
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
                class if given, else :attr:`DEFAULT_LOADER_CLS`.
            path_factory: Path constructor used when *path* is a string.
            loader: The parent :class:`~yaconfiglib.loader.ConfigLoader`.
                When supplied, ``!include``/``!load`` tags are registered
                on *loader_cls* so nested includes resolve through it.

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

        # Auto-register !include / !load tags if a loader is provided
        # and the tags haven't already been registered on this loader class.
        if loader is not None:
            self._register_include_tags(loader_cls, loader, path_factory)

        loader_instance = loader_cls(path.read_text(encoding=encoding))
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
        """
        if getattr(loader_cls, "_yaconfiglib_include_registered", False):
            return

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
            else:
                raise TypeError(f"Un-supported YAML node {node!r}")

            kwargs.setdefault("master", ldr)
            return loader.load(pathname, *args, **kwargs)

        for tag in _INCLUDE_TAGS:
            loader_cls.add_constructor(tag, _construct)

        loader_cls._yaconfiglib_include_registered = True  # type: ignore[attr-defined]

    def dumps(self, data: str, dumper_cls: yaml.Dumper = None, **options) -> str:
        """Serialize *data* to a YAML string using *dumper_cls* (defaults to :attr:`DEFAULT_DUMPER_CLS`)."""
        options.setdefault("Dumper", dumper_cls or self.DEFAULT_DUMPER_CLS)
        return yaml.dump(data, **options)
