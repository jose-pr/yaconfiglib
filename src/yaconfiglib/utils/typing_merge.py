from __future__ import annotations

import types
import typing
from argparse import Namespace
from dataclasses import is_dataclass

from .merge import is_scalar, is_array

T = typing.TypeVar("T")


def typed_merge(cls: type[T], *objects: object, init: bool = True) -> T:
    """Recursively merge *objects* into an instance of *cls*.

    The merge is type-guided: for mappings/dataclasses, fields are collected
    across all objects and merged field-by-field; for sequences, the last
    value wins element-by-element.  For simple scalars, the last object wins.
    """
    if not objects:
        return None

    merge_fn = getattr(cls, "__merge__", None)
    if merge_fn:
        return merge_fn(*objects, init=init)

    hints: dict[str, type] = {}
    child_cls: type | None = None

    # Unwrap Union types — use the first concrete option.
    # types.UnionType (X | Y syntax at runtime) is 3.10+; guard with hasattr.
    _union_type = getattr(types, "UnionType", None)
    origin = cls
    while True:
        raw_origin = getattr(origin, "__origin__", origin)
        if raw_origin is typing.Union or (_union_type and raw_origin is _union_type):
            origin = typing.get_args(origin)[0]
            continue
        break

    cls_args = typing.get_args(cls) if cls is not origin else ()
    origin = getattr(origin, "__origin__", origin)

    # A resolved hint that is not a class (e.g. an ipaddress-style factory
    # FUNCTION used as a field annotation, such as netutils.IPNetwork) cannot
    # drive the issubclass()/isinstance(_, origin) branch selection below and
    # would raise TypeError. Treat it as an opaque coercer: last value wins,
    # coerced through the callable when it is one, else returned as-is. This
    # mirrors the scalar tail, which is unreachable for a non-class origin.
    if not isinstance(origin, type):
        value = objects[-1]
        if callable(origin):
            try:
                return origin(value)
            except (TypeError, ValueError):
                return value
        return value

    try:
        hints = typing.get_type_hints(origin)
    except Exception:
        hints = {}

    if issubclass(origin, typing.Mapping) and len(cls_args) > 1:
        child_cls = cls_args[1]
    elif issubclass(origin, typing.Sequence) and not issubclass(origin, str):
        if cls_args:
            child_cls = cls_args[0]

    # Sequence type: use last object, convert each element via child type.
    if is_array(origin) and not is_scalar(origin):
        value = objects[-1]
        return origin(
            typed_merge(child_cls or type(item), item, init=init) for item in value
        )

    # Mapping / Namespace / dataclass: merge field by field.
    if issubclass(origin, (typing.Mapping, Namespace)) or is_dataclass(origin):
        fields: dict[str, list] = {}
        for obj in objects:
            props = obj if isinstance(obj, typing.Mapping) else vars(obj)
            for prop, value in props.items():
                parser = getattr(obj, f"_parse_{prop}", None)
                if parser:
                    value = parser(value)
                fields.setdefault(prop, []).append(value)

        merged: dict[str, object] = {}
        for name, values in fields.items():
            hint = hints.get(name, child_cls or type(values[-1]))
            merged[name] = typed_merge(hint, *values, init=init) if hint else values[-1]

        if init:
            return origin(**merged)

        inst = origin.__new__(origin)
        for prop, value in merged.items():
            if issubclass(origin, typing.MutableMapping):
                inst[prop] = value
            else:
                setattr(inst, prop, value)
        return inst

    # Scalar / unknown: last value wins.
    value = objects[-1]
    return value if isinstance(value, origin) else origin(value)
