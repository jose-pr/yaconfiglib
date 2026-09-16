from __future__ import annotations

import sys
import types
import typing
from argparse import Namespace
from dataclasses import is_dataclass

T = typing.TypeVar("T")

__all__ = ["typed_merge", "OpaqueMerge", "opaque", "TypedNamespace"]

_NONE_TYPE = type(None)
# types.UnionType (the X | Y syntax at runtime) is 3.10+; guard with getattr.
_UNION_TYPE = getattr(types, "UnionType", None)


def _strip_annotated(hint: object) -> object:
    """Return *hint* with any ``Annotated[...]`` wrappers removed."""
    while typing.get_origin(hint) is typing.Annotated:
        hint = typing.get_args(hint)[0]
    return hint


def _runtime_class(member: object) -> type | None:
    """The class an ``isinstance`` test for union *member* should use, if any."""
    member = _strip_annotated(member)
    cls = typing.get_origin(member) or member
    if cls is typing.Any or not isinstance(cls, type):
        return None
    return cls


def _resolve_hint(hint: object, value: object) -> object:
    """Normalize *hint* for merging *value*: strip Annotated, pick a union member.

    A union drops its ``NoneType`` members (None sources are filtered out before
    this is called), collapses to ``typing.Any`` if any member is ``Any``, and
    otherwise resolves to the first member *value* is already an instance of —
    so a hint that already accepts the value never coerces it. Failing that it
    falls back to the documented first member.
    """
    while True:
        hint = _strip_annotated(hint)
        origin = typing.get_origin(hint)
        if origin is typing.Union or (
            _UNION_TYPE is not None and origin is _UNION_TYPE
        ):
            members = [m for m in typing.get_args(hint) if m is not _NONE_TYPE]
            if not members:
                return _NONE_TYPE
            if any(_strip_annotated(m) is typing.Any for m in members):
                return typing.Any
            chosen = None
            for member in members:
                runtime_cls = _runtime_class(member)
                if runtime_cls is None:
                    continue
                try:
                    matches = isinstance(value, runtime_cls)
                except TypeError:
                    # A non-runtime-checkable Protocol; it cannot claim the value.
                    continue
                if matches:
                    chosen = member
                    break
            hint = members[0] if chosen is None else chosen
            continue
        return hint


if sys.version_info >= (3, 14):

    def _own_annotations(klass: type) -> dict:
        # PEP 649: a class __dict__ no longer carries __annotations__ at all.
        import annotationlib

        return annotationlib.get_annotations(
            klass, format=annotationlib.Format.FORWARDREF
        )

else:

    def _own_annotations(klass: type) -> dict:
        return klass.__dict__.get("__annotations__", {})


def _type_hints(origin: type) -> dict:
    """Resolve *origin*'s annotations, entry by entry if the whole set fails.

    ``typing.get_type_hints`` is all-or-nothing: one unresolvable forward
    reference used to leave every sibling field unhinted and so uncoerced. The
    fallback walks the MRO base-first and resolves each class's own annotations
    one at a time, keeping what resolves and dropping only what does not.
    """
    try:
        return typing.get_type_hints(origin)
    except (TypeError, NameError, AttributeError):
        # A new exception type here is a bug we want to see, not swallow
        # (project error-handling stance: no bare except Exception).
        pass

    hints: dict = {}
    for klass in reversed(getattr(origin, "__mro__", (origin,))):
        for name, annotation in _own_annotations(klass).items():
            holder = types.SimpleNamespace(__annotations__={name: annotation})
            try:
                hints.update(
                    typing.get_type_hints(
                        holder,
                        # sys.modules.get, not indexing: a KeyError from a class
                        # whose module is not imported would escape this except.
                        globalns=getattr(
                            sys.modules.get(klass.__module__), "__dict__", {}
                        ),
                        localns=dict(vars(klass)),
                    )
                )
            except (TypeError, NameError, AttributeError):
                # A subclass re-annotating a name unresolvably must not leave the
                # base class's resolved hint in place.
                hints.pop(name, None)
    return hints


def typed_merge(cls: type[T], *objects: object, init: bool = True) -> T:
    """Recursively merge *objects* into an instance of *cls*.

    ``None`` objects are skipped at every level, so a later ``None`` never
    overrides an earlier value; if nothing but ``None`` is given the result is
    ``None``.

    The merge is type-guided: for mappings/dataclasses, fields are collected
    across all objects and merged field-by-field; for sequences, the last
    object's value is taken with each element coerced through the element type.
    For simple scalars, the last object wins.
    """
    objects = tuple(obj for obj in objects if obj is not None)
    if not objects:
        return None

    hints: dict[str, type] = {}
    child_cls: type | None = None

    # Normalize the hint once, against the value it will be applied to: strip
    # Annotated, and unwrap a union to a single member (NoneType members dropped).
    origin = _resolve_hint(cls, objects[-1])

    # Generic args come from the UNWRAPPED origin, and must be read before the
    # get_origin strip below. Reading them from `cls` was wrong in both
    # directions: for a plain generic the unwrap leaves `origin is cls`, so
    # the old `if cls is not origin else ()` guard yielded () and no element type
    # was ever recovered; for a union it yielded the UNION's args, so
    # Optional[Dict[str, int]] produced child_cls=NoneType and merging crashed
    # with "NoneType takes no arguments".
    cls_args = typing.get_args(origin)
    _stripped = typing.get_origin(origin)
    origin = origin if _stripped is None else _stripped

    # Looked up on the STRIPPED origin: typing aliases do not forward dunders,
    # so reading it off `cls` missed the hook for Optional[Zone], MyGeneric[int]
    # and every other parameterized or unioned spelling of an opaque type.
    merge_fn = getattr(origin, "__merge__", None)
    if merge_fn:
        return merge_fn(*objects, init=init)

    # typing.Any is a class on 3.11+, so it would otherwise reach the branch
    # selection below instead of the non-class escape hatch. Short-circuit it on
    # every version to the documented rule the 3.9 path already gave: last wins.
    if origin is typing.Any:
        return objects[-1]

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

    hints = _type_hints(origin)

    if issubclass(origin, typing.Mapping) and len(cls_args) > 1:
        child_cls = cls_args[1]
    elif issubclass(origin, typing.Sequence) and not issubclass(origin, str):
        if cls_args:
            child_cls = cls_args[0]

    # Sequence type: use last object, convert each element via child type.
    # The test is a CLASS test on the unwrapped origin. It used to be
    # `is_array(origin) and not is_scalar(origin)` — instance checks applied to a
    # class object, so `isinstance(list, (list, tuple))` was False and this whole
    # branch was unreachable; List[str] fell through to the scalar tail and
    # returned its elements uncoerced. str/bytes are Sequences too and must be
    # excluded, or every string hint would be rebuilt character by character.
    if issubclass(origin, typing.Sequence) and not issubclass(origin, (str, bytes)):
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
            # The fallback hint comes from the last NON-None value: type(None)
            # would drive the merge into NoneType and crash. A field whose every
            # value is None stays None.
            present = [value for value in values if value is not None]
            if not present:
                merged[name] = None
                continue
            hint = hints.get(name, child_cls or type(present[-1]))
            merged[name] = (
                typed_merge(hint, *values, init=init) if hint else present[-1]
            )

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
    field — a fully-built config object whose ``__init__`` already normalized
    its fields. ``typed_merge`` then returns the last object unchanged instead
    of introspecting its fields, including when the hint is ``Optional[...]`` or
    another union. Field annotations that are factory functions rather than
    classes are already coerced per field and need no mixin.
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
