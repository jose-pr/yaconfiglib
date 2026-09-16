from __future__ import annotations

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
    def extend(
        cls,
        other: "_ty.Type[T]",
        *,
        name: "_ty.Optional[str]" = None,
        module: "_ty.Optional[str]" = None,
    ) -> "_ty.Type[IntEnum]":
        """Return a new enum holding this enum's members plus *other*'s.

        Methods and other class attributes of *other*, then of this class, are
        copied onto the result unless it already has them, so hooks defined on
        either side keep working on the extension.

        *name* overrides the new enum's name (default: *other*'s). *module* is
        the module the members claim to live in (default: *other*'s), which is
        what lets :mod:`pickle` find them again — so bind the result in that
        module under *name*.
        """
        enum_name = name or other.__name__
        enum = IntEnum(
            enum_name,
            [(i.name, i.value) for i in _chain(cls, other)],
            module=module or other.__module__,
            qualname=enum_name,
        )
        enum_: dict = enum.__dict__
        added: "_ty.List[str]" = []
        for _cls in [other, cls]:
            decl: dict = _cls.__dict__
            for attr, obj in decl.items():
                if attr not in enum_ and attr not in added:
                    added.append(attr)
                    setattr(enum, attr, obj)

        return enum
