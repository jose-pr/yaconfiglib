from __future__ import annotations

import types
import typing
from argparse import Namespace
from dataclasses import is_dataclass

from .merge import is_scalar, is_array

T = typing.TypeVar("T")

__all__ = ["typed_merge", "OpaqueMerge", "opaque", "TypedNamespace"]


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


# ---------------------------------------------------------------------------
# Extension hooks for consumers
#
# ``typed_merge`` honors two per-type hooks:
#   * ``__merge__(cls, *objects, init=True)`` — a classmethod that fully
#     overrides how instances of ``cls`` are merged.
#   * ``_parse_<field>(value)`` — a per-field coercer looked up on each source
#     object as a field is collected.
# The helpers below package the two most common uses so consumers do not
# reimplement them.
# ---------------------------------------------------------------------------


def _last_wins(_cls, *objects: object, init: bool = True) -> object:
    return objects[-1] if objects else None


class OpaqueMerge:
    """Mixin marking a type *opaque* to :func:`typed_merge` — last object wins.

    Inherit from ``OpaqueMerge`` when instances should NOT be re-merged field by
    field: a fully-built config object whose ``__init__`` already normalized its
    fields, or one whose field annotations are factory functions rather than
    classes. ``typed_merge`` then returns the last object unchanged instead of
    introspecting its fields.
    """

    @classmethod
    def __merge__(cls, *objects: object, init: bool = True) -> object:
        return _last_wins(cls, *objects, init=init)


def opaque(cls: type) -> type:
    """Class decorator equivalent of :class:`OpaqueMerge`.

    Marks *cls* opaque to :func:`typed_merge` (last object wins) without altering
    its base classes — useful when the type already has a fixed hierarchy.
    """
    cls.__merge__ = classmethod(_last_wins)
    return cls


class TypedNamespace(Namespace):
    """An :class:`argparse.Namespace` that applies ``_parse_<field>`` coercers.

    At construction, for every keyword given, if the instance defines a
    ``_parse_<name>(value)`` method it is applied to that field's value. This is
    the same per-field coercion convention :func:`typed_merge` honors, applied at
    build time so a constructed object is already normalized (e.g. a raw string
    field turned into an ``ipaddress`` object). Compose with :class:`OpaqueMerge`
    when such a built object should also be opaque to re-merging.
    """

    def __init__(self, **kwargs: object) -> None:
        for name, value in list(kwargs.items()):
            parser = getattr(self, f"_parse_{name}", None)
            if parser is not None:
                kwargs[name] = parser(value)
        super().__init__(**kwargs)
