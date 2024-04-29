import typing as _ty
from enum import IntEnum as _IntEnum
from itertools import chain as _chain

T = _ty.TypeVar("T", bound="IntEnum")


class IntEnum(_IntEnum):
    @classmethod
    def _missing_(cls, value: object):
        if not isinstance(value, int):
            name = str(value).lower()
            for member in cls:
                if member.name.lower() == name:
                    return member
        super()._missing_(value)

    @classmethod
    def extend(cls, other: type[T], *, name: str = None) -> T | _ty.Self:
        enum = IntEnum(
            name or other.__name__, [(i.name, i.value) for i in _chain(cls, other)]
        )
        enum_: dict = enum.__dict__
        added: list[str] = []
        for _cls in [other, cls]:
            decl: dict[str] = _cls.__dict__
            for name, obj in decl.items():
                if name not in enum_ and name not in added:
                    added.append(name)
                    setattr(enum, name, obj)

        return enum
