"""
Merge strategies for combining configuration documents.

This module provides the :class:`MergeMethod` enum with three built-in strategies:

* **Simple** — shallow merge: scalars/lists replace, dicts update (top-level keys only).
* **Substitute** — like simple, but dicts are merged recursively while lists always replace.
* **Deep** — fully recursive: dicts merged key-by-key, lists extended with unique
  non-mapping items.

A *leaf* is anything that is neither a mapping nor a list: a string, a number, a
date, a ``Decimal``, a set, a custom object. Leaves replace, and when the two
sides have different shapes the later value replaces the earlier one.

Every strategy is copy-on-write: it **never modifies its inputs**, so a document
loaded once can be overridden many times — which matters for YAML anchors and
``<<:`` merge keys, where several keys share one object. The result may still
*share* unchanged sub-objects with the inputs; deep-copy it first if you intend
to mutate it and keep the sources intact.
"""

from __future__ import annotations

import copy
import logging
import typing
import typing as _ty

from .enum import IntEnum

logger = logging.getLogger(__name__)

# Tuple used for isinstance() checks — must be a plain tuple for 3.9 compat.
_SCALAR_TYPES = (int, str, bool, float, type(None), bytes)


def is_scalar(obj: object) -> bool:
    return isinstance(obj, _SCALAR_TYPES)


def is_array(obj, mutable: bool = False) -> bool:
    """Return True if *obj* is a sequence but not a mapping.

    Strings and binary buffers (``bytes``, ``bytearray``, ``memoryview``) are
    **not** arrays: they are values in their own right, so merging replaces them
    instead of combining them element by element.

    When *mutable* is True, also require that the sequence supports item
    assignment (i.e. is a :class:`~typing.MutableSequence`).
    """
    if isinstance(obj, (list, tuple)):
        return not mutable or isinstance(obj, list)
    if isinstance(obj, (str, bytes, bytearray, memoryview, dict)):
        return False
    if isinstance(obj, typing.Mapping):
        return False
    if mutable:
        return isinstance(obj, typing.MutableSequence)
    return isinstance(obj, typing.Sequence)


def _is_leaf(obj: object) -> bool:
    """True for a value that merges by replacement: not a mapping, not an array.

    Deliberately not a type whitelist. Dates, datetimes, ``Decimal``, sets, enum
    members and objects a backend produced are all ordinary configuration values;
    listing the acceptable ones meant everything else raised instead of merging.
    """
    return not isinstance(obj, typing.Mapping) and not is_array(obj)


def _mapping_with(a: typing.Mapping, items: dict) -> typing.Mapping:
    """Return a mapping of *a*'s type holding *items*, without touching *a*.

    A ``dict`` subclass is shallow-copied so its type and state (a
    ``defaultdict``'s factory, for instance) survive. Any other Mapping is
    rebuilt through its own constructor, falling back to a plain dict when that
    signature does not accept the keys — which is also what makes a read-only
    mapping with overlapping keys work. A non-dict Mapping is never
    ``copy.copy``-ed: a wrapper's shallow copy can share its backing store.
    """
    if isinstance(a, dict):
        result = copy.copy(a)
        result.clear()
        result.update(items)
        return result
    try:
        return type(a)(**items)
    except TypeError:
        return dict(items)


def _sequence_with(a: typing.Sequence, items: list) -> typing.Sequence:
    """Return a sequence of *a*'s type holding *items*, without touching *a*."""
    if isinstance(a, list):
        if type(a) is list:
            return items
        result = copy.copy(a)
        result[:] = items
        return result
    try:
        return type(a)(items)
    except TypeError:
        return items


def _memo_key(a: object, b: object) -> tuple:
    return (id(a), id(b))


class _SeenValues:
    """Membership tracker for Deep list extension, aware of the value's type.

    ``True``, ``1`` and ``1.0`` compare equal but are different configuration
    values, so an override of ``[1]`` with ``[True]`` must keep both. Hashable
    items are tracked in a set keyed by ``(type, value)``; anything unhashable
    falls back to a scan of the unhashable ones.
    """

    def __init__(self, items: typing.Iterable = ()):
        self._hashed = set()
        self._unhashable = []
        for item in items:
            self.add(item)

    def add(self, item: object) -> bool:
        """Record *item*; return True when it was not already present."""
        try:
            key = (type(item), item)
        except TypeError:  # pragma: no cover - type() never raises in practice
            key = None
        if key is not None:
            try:
                if key in self._hashed:
                    return False
                self._hashed.add(key)
                return True
            except TypeError:
                pass  # unhashable value: fall through to the linear scan
        for known in self._unhashable:
            if type(known) is type(item) and known == item:
                return False
        self._unhashable.append(item)
        return True


@typing.runtime_checkable
class Merge(typing.Protocol):
    """Protocol for any callable that merges two objects."""

    def __call__(
        self,
        a: object,
        b: object,
        *,
        memo: "_ty.Optional[dict]" = None,
        **options: typing.Any,
    ) -> typing.Any: ...


class MergeMethod(IntEnum):
    """Built-in merge strategies.

    A strategy is called as ``MergeMethod.Deep(a, b, **options)`` and returns the
    merged value. It **never modifies its inputs**: use the return value. The
    result may share unchanged sub-objects with *a* and *b*.

    ``memo`` is internal — a per-call map of the container pairs already merged,
    which keeps a node aliased in both inputs aliased in the result and lets a
    self-referencing document merge instead of recursing forever. Callers leave
    it ``None``.
    """

    Simple = 1
    Deep = 2
    Substitute = 3

    def __call__(
        self,
        a: object,
        b: object,
        *,
        memo: "_ty.Optional[dict]" = None,
        **options: typing.Any,
    ):
        method: Merge = getattr(self, f"_{self.name.lower()}")
        return method(a, b, memo={} if memo is None else memo, **options)

    # ------------------------------------------------------------------
    # Simple merge
    # Leaves and arrays replace; dicts are updated shallowly.
    # ------------------------------------------------------------------
    def _simple(
        self,
        a: object,
        b: object,
        *,
        memo: "_ty.Optional[dict]" = None,
        **options: typing.Any,
    ):
        if b is None:
            return a

        if isinstance(b, typing.Mapping):
            if isinstance(a, typing.Mapping):
                merged = dict(a)
                merged.update(b)
                return _mapping_with(a, merged)
            return b

        # A leaf, or a shape change: the later value wins.
        return b

    # ------------------------------------------------------------------
    # Substitute merge
    # Leaves (anything that is not a mapping or a list) and arrays always
    # replace.  Dicts are merged recursively (existing keys recurse; new keys
    # are inserted).
    # ------------------------------------------------------------------
    def _substitute(
        self,
        a: object,
        b: object,
        *,
        memo: "_ty.Optional[dict]" = None,
        **options: typing.Any,
    ):
        if b is None:
            return a

        if a is None:
            return b

        if isinstance(a, typing.Mapping) and isinstance(b, typing.Mapping):
            key = _memo_key(a, b)
            if key in memo:
                return memo[key][2]
            if isinstance(a, dict):
                # Register the result container before recursing, so a document
                # that refers to itself resolves to the in-progress result
                # instead of recursing until RecursionError.
                result = copy.copy(a)
                result.clear()
                memo[key] = (a, b, result)
                target = dict(a)
                for k, v in b.items():
                    if k in target:
                        target[k] = self._substitute(target[k], v, memo=memo, **options)
                    else:
                        target[k] = v
                result.update(target)
                return result
            target = dict(a)
            for k, v in b.items():
                if k in target:
                    target[k] = self._substitute(target[k], v, memo=memo, **options)
                else:
                    target[k] = v
            result = _mapping_with(a, target)
            memo[key] = (a, b, result)
            return result

        if (
            isinstance(a, typing.Mapping)
            and is_array(b)
            and b
            and all(isinstance(item, typing.Mapping) for item in b)
        ):
            # A non-empty list of mappings folds into the mapping, in order.
            # Anything else — including an empty list, which reads as "clear" —
            # replaces it.
            result = a
            for item in b:
                result = self._substitute(result, item, memo=memo, **options)
            return result

        # A leaf, or a shape change: the later value wins.
        return b

    # ------------------------------------------------------------------
    # Deep merge
    # Dicts merged key-by-key recursively.  Lists extended with unique
    # non-mapping items; mapping elements inside lists are merged by
    # position when mergelists=True.
    # ------------------------------------------------------------------
    def _deep(
        self,
        a: object,
        b: object,
        *,
        memo: "_ty.Optional[dict]" = None,
        mergelists: bool = False,
        **options: typing.Any,
    ):
        if b is None:
            return a

        if a is None:
            return b

        if _is_leaf(b):
            return b

        if is_array(a) and is_array(b):
            return self._deep_lists(a, b, memo=memo, mergelists=mergelists, **options)

        if isinstance(a, typing.Mapping) and isinstance(b, typing.Mapping):
            return self._deep_dicts(a, b, memo=memo, mergelists=mergelists, **options)

        if (
            isinstance(a, typing.Mapping)
            and is_array(b)
            and b
            and all(isinstance(item, typing.Mapping) for item in b)
        ):
            # As in Substitute: a non-empty list of mappings folds, anything
            # else replaces.
            result = a
            for item in b:
                result = self._deep(
                    result, item, memo=memo, mergelists=mergelists, **options
                )
            return result

        # A leaf, or a shape change: the later value wins.
        return b

    def _deep_dicts(
        self,
        a: typing.Mapping,
        b: typing.Mapping,
        *,
        memo: "_ty.Optional[dict]",
        mergelists: bool,
        **options,
    ) -> typing.Mapping:
        key = _memo_key(a, b)
        if key in memo:
            return memo[key][2]
        if isinstance(a, dict):
            # Registered before recursing: see _substitute.
            result = copy.copy(a)
            result.clear()
            memo[key] = (a, b, result)
            target = dict(a)
            for k, v in b.items():
                if k in target:
                    target[k] = self._deep(
                        target[k], v, memo=memo, mergelists=mergelists, **options
                    )
                else:
                    target[k] = v
            result.update(target)
            return result
        target = dict(a)
        for k, v in b.items():
            if k in target:
                target[k] = self._deep(
                    target[k], v, memo=memo, mergelists=mergelists, **options
                )
            else:
                target[k] = v
        result = _mapping_with(a, target)
        memo[key] = (a, b, result)
        return result

    def _deep_lists(
        self,
        a: typing.Sequence,
        b: typing.Sequence,
        *,
        memo: "_ty.Optional[dict]",
        mergelists: bool,
        **options,
    ) -> typing.Sequence:
        key = _memo_key(a, b)
        if key in memo:
            return memo[key][2]
        result = list(a)

        merged_indices = set()
        if mergelists:
            # Merge mapping elements that sit at the same index in both lists and
            # share at least one key. Only those indices are consumed; every other
            # item of b is appended below, in b's own order.
            for index in range(min(len(result), len(b))):
                a_item = result[index]
                b_item = b[index]
                if not isinstance(a_item, typing.Mapping) or not isinstance(
                    b_item, typing.Mapping
                ):
                    continue
                if any(k in a_item for k in b_item):
                    merged_indices.add(index)
                    result[index] = self._deep(
                        a_item, b_item, memo=memo, mergelists=mergelists, **options
                    )

        seen = _SeenValues(
            item for item in result if not isinstance(item, typing.Mapping)
        )
        for index, item in enumerate(b):
            if index in merged_indices:
                continue
            if isinstance(item, typing.Mapping):
                # Mappings are never de-duplicated.
                result.append(item)
            elif seen.add(item):
                result.append(item)

        merged = _sequence_with(a, result)
        memo[key] = (a, b, merged)
        return merged


from .typing_merge import (
    OpaqueMerge,
    TypedNamespace,
    opaque,
    typed_merge,
)
