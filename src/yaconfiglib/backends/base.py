import re as _re
import typing as _ty

from pathlib_next import LocalPath as _LocalPath
from pathlib_next import Path as _Path
from pathlib_next import Pathname as _Pathname

if _ty.TYPE_CHECKING:
    import yaml as _yaml
else:

    try:
        import yaml as _yaml
    except ImportError:
        _yaml = None
        ...


class ConfigBackend(_ty.Protocol):
    PATHNAME_REGEX: _re.Pattern = None
    NAME: str = None
    DEFAULT_ENCODING = "utf-8"
    DEFAULT_PATH_FACTORY = _LocalPath

    def __call__(self, *args, **kwds):
        if (
            len(args) == 2
            and _yaml is not None
            and isinstance(args[0], _yaml.constructor.BaseConstructor)
        ):
            return self._yaml_tag_constructor(*args, **kwds)

    def _yaml_tag_constructor(self, loader: "_yaml.Loader", node: "_yaml.Node"):
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
        raise NotImplementedError()

    def load_all(self, path: _Path, **options) -> _ty.Iterable[object]:
        yield self.load(path, **options)

    def dumps(self, data: str, **options) -> str:
        raise NotImplementedError

    @classmethod
    def __subclasses__(cls, *, recursive=False) -> list[type[_ty.Self]]:
        subclasess: _ty.Sequence[_ty.Self] = type.__subclasses__(cls)
        if not recursive:
            return subclasess
        return set(subclasess).union(
            [_cls for cls in subclasess for _cls in cls.__subclasses__(recursive=True)]
        )

    @classmethod
    def get_class_by_name(cls, name: str) -> type[_ty.Self]:
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
        return (
            cls.PATHNAME_REGEX.match(path.name) != None if cls.PATHNAME_REGEX else False
        )

    @classmethod
    def get_class_by_path(cls, path: _Path):
        for scls in cls.__subclasses__(recursive=True):
            if scls.can_load_path(path):
                return scls
        raise NotImplementedError(f"Not reader for {path}")
