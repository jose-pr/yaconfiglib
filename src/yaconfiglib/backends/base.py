import re as _re
import typing as _ty

from pathlib_next import Path as _Path


class ConfigBackend(_ty.Protocol):
    PATHNAME_REGEX: _re.Pattern = None
    NAME: str = None

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
                _name = scls.__name__.lower().removesuffix("configloader")
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
