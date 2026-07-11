"""
Merge strategies for combining configuration documents.

This module provides the :class:`MergeMethod` enum with three built-in strategies:

* **Simple** — shallow merge: scalars/lists replace, dicts update (top-level keys only).
* **Substitute** — like simple, but dicts are merged recursively while lists always replace.
* **Deep** — fully recursive: dicts merged key-by-key, lists extended with unique items.
"""

import logging
import types
import typing
from argparse import Namespace
from dataclasses import is_dataclass

from .enum import IntEnum

logger = logging.getLogger(__name__)

SCALAR = int | str | bool | float | types.NoneType | bytes


def is_scalar(obj) -> typing.TypeGuard[SCALAR]:
    return isinstance(obj, typing.get_args(SCALAR))


def is_array(obj, mutable: bool = False) -> bool:
    """Return True if *obj* is a sequence but not a mapping.

    When *mutable* is True, also require that the sequence supports item
    assignment (i.e. is a :class:`~typing.MutableSequence`).
    """
    if isinstance(obj, typing.Mapping) or isinstance(obj, str):
        return False
    if mutable:
        return isinstance(obj, typing.MutableSequence)
    return isinstance(obj, typing.Sequence)


@typing.runtime_checkable
class Merge(typing.Protocol):
    """Protocol for any callable that merges two objects."""

    def __call__(
        self,
        a: object,
        b: object,
        *,
        memo: dict | None = None,
        **options,
    ) -> object: ...


class MergeMethod(IntEnum):
    """Built-in merge strategies."""

    Simple = 1
    Deep = 2
    Substitute = 3

    def __call__(
        self,
        a: object,
        b: object,
        *,
        memo: dict | None = None,
        **options,
    ):
        method: Merge = getattr(self, f"_{self.name.lower()}")
        return method(a, b, memo=memo, **options)

    # ------------------------------------------------------------------
    # Simple merge
    # Scalars and arrays replace; dicts are updated shallowly.
    # ------------------------------------------------------------------
    def _simple(
        self,
        a: object,
        b: object,
        *,
        memo: dict | None = None,
        **options,
    ):
        if b is None:
            return a

        if is_scalar(b):
            return b

        if is_array(b):
            if is_array(a):
                # Element-by-element replacement up to len(b); truncate extras.
                result = list(a)
                for i, v in enumerate(b):
                    if i < len(result):
                        result[i] = self._simple(result[i], v, memo=memo, **options)
                    else:
                        result.append(v)
                return type(a)(result) if not isinstance(a, list) else result
            return b

        if isinstance(b, typing.Mapping):
            if isinstance(a, typing.Mapping):
                if isinstance(a, typing.MutableMapping):
                    a.update(b)
                    return a
                return type(a)(**a, **b)
            return b

        raise TypeError(
            f"Cannot simple-merge {type(b).__name__!r} into {type(a).__name__!r}"
        )

    # ------------------------------------------------------------------
    # Substitute merge
    # Scalars and arrays always replace.  Dicts are merged recursively
    # (existing keys recurse; new keys are inserted).
    # ------------------------------------------------------------------
    def _substitute(
        self,
        a: object,
        b: object,
        *,
        memo: dict | None = None,
        **options,
    ):
        if b is None:
            return a

        # When a is a mapping we always try to merge into it — fall through
        # to the mapping branches below.  Only replace outright when a is NOT
        # a mapping (or when b is a scalar/array and a is None).
        if not isinstance(a, typing.Mapping):
            if a is None or is_scalar(b) or is_array(b):
                return b

        if isinstance(a, typing.Mapping) and isinstance(b, typing.Mapping):
            target = dict(a)
            for k, v in b.items():
                if k in target:
                    target[k] = self._substitute(target[k], v, memo=memo, **options)
                else:
                    target[k] = v
            # Preserve the original mapping type where possible.
            if isinstance(a, typing.MutableMapping):
                a.update(target)
                return a
            try:
                return type(a)(**target)
            except TypeError:
                return target

        if isinstance(a, typing.Mapping) and is_array(b):
            # Merge each dict element of b into a sequentially.
            result = a
            for item in b:
                if isinstance(item, typing.Mapping):
                    result = self._substitute(result, item, memo=memo, **options)
                else:
                    raise TypeError(
                        f"Cannot merge list element of type {type(item).__name__!r} "
                        f"into a mapping"
                    )
            return result

        raise TypeError(
            f"Cannot substitute-merge {type(b).__name__!r} into {type(a).__name__!r}"
        )


    # ------------------------------------------------------------------
    # Deep merge
    # Dicts merged key-by-key recursively.  Lists extended with unique
    # scalar/array items; dict elements inside lists are merged by
    # position when mergelists=True.
    # ------------------------------------------------------------------
    def _deep(
        self,
        a: object,
        b: object,
        *,
        memo: dict | None = None,
        mergelists: bool = False,
        **options,
    ):
        if b is None:
            return a

        if a is None or is_scalar(b):
            return b

        if is_array(a) and is_array(b):
            return self._deep_lists(a, b, memo=memo, mergelists=mergelists, **options)

        if isinstance(a, typing.Mapping) and isinstance(b, typing.Mapping):
            return self._deep_dicts(a, b, memo=memo, mergelists=mergelists, **options)

        if isinstance(a, typing.Mapping) and is_array(b):
            result = a
            for item in b:
                if isinstance(item, typing.Mapping):
                    result = self._deep(
                        result, item, memo=memo, mergelists=mergelists, **options
                    )
                else:
                    raise TypeError(
                        f"Cannot deep-merge list element of type "
                        f"{type(item).__name__!r} into a mapping"
                    )
            return result

        raise TypeError(
            f"Cannot deep-merge {type(b).__name__!r} into {type(a).__name__!r}"
        )

    def _deep_dicts(
        self,
        a: typing.Mapping,
        b: typing.Mapping,
        *,
        memo: dict | None,
        mergelists: bool,
        **options,
    ) -> typing.Mapping:
        target = dict(a)
        for k, v in b.items():
            if k in target:
                target[k] = self._deep(
                    target[k], v, memo=memo, mergelists=mergelists, **options
                )
            else:
                target[k] = v
        if isinstance(a, typing.MutableMapping):
            a.update(target)
            return a
        try:
            return type(a)(**target)
        except TypeError:
            return target

    def _deep_lists(
        self,
        a: typing.Sequence,
        b: typing.Sequence,
        *,
        memo: dict | None,
        mergelists: bool,
        **options,
    ) -> typing.Sequence:
        result = list(a)

        # Collect dict elements from b for potential positional merge.
        b_dicts: dict[int, typing.Mapping] = {
            i: item for i, item in enumerate(b) if isinstance(item, typing.Mapping)
        }

        # Extend with unique non-dict items from b.
        for item in b:
            if (is_scalar(item) or is_array(item)) and item not in result:
                result.append(item)

        # Merge dict elements by position if requested.
        if mergelists:
            for i, a_item in enumerate(result):
                if isinstance(a_item, typing.Mapping) and i in b_dicts:
                    b_item = b_dicts.pop(i)
                    # Only merge when at least one key overlaps.
                    if any(k in a_item for k in b_item):
                        result[i] = self._deep(
                            a_item, b_item, memo=memo, mergelists=mergelists, **options
                        )
                        continue

        # Append any remaining b dict entries that were not merged.
        for v in b_dicts.values():
            result.append(v)

        if isinstance(a, typing.MutableSequence):
            a[:] = result
            return a
        try:
            return type(a)(result)
        except TypeError:
            return result


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
    origin = cls
    while True:
        raw_origin = getattr(origin, "__origin__", origin)
        if raw_origin is typing.Union or raw_origin is types.UnionType:
            origin = typing.get_args(origin)[0]
            continue
        break

    cls_args = typing.get_args(cls) if cls is not origin else ()
    origin = getattr(origin, "__origin__", origin)

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
