"""
Merge strategies for combining configuration documents.

This module provides the :class:`MergeMethod` enum with three built-in strategies:

* **Simple** — shallow merge: scalars/lists replace, dicts update (top-level keys only).
* **Substitute** — like simple, but dicts are merged recursively while lists always replace.
* **Deep** — fully recursive: dicts merged key-by-key, lists extended with unique items.

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

from .enum import IntEnum

logger = logging.getLogger(__name__)

# Tuple used for isinstance() checks — must be a plain tuple for 3.9 compat.
_SCALAR_TYPES = (int, str, bool, float, type(None), bytes)


def is_scalar(obj: object) -> bool:
    return isinstance(obj, _SCALAR_TYPES)


def is_array(obj, mutable: bool = False) -> bool:
    """Return True if *obj* is a sequence but not a mapping.

    When *mutable* is True, also require that the sequence supports item
    assignment (i.e. is a :class:`~typing.MutableSequence`).
    """
    if isinstance(obj, (list, tuple)):
        return not mutable or isinstance(obj, list)
    if isinstance(obj, (str, bytes, dict)):
        return False
    if isinstance(obj, typing.Mapping):
        return False
    if mutable:
        return isinstance(obj, typing.MutableSequence)
    return isinstance(obj, typing.Sequence)


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
        memo: dict | None = None,
        **options,
    ):
        method: Merge = getattr(self, f"_{self.name.lower()}")
        return method(a, b, memo={} if memo is None else memo, **options)

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
                # Element-by-element replacement up to len(b); b's extra
                # elements are appended, and a's tail beyond len(b) is kept.
                # (The comment used to say "truncate extras", which described
                # neither branch — tests pin the append-and-keep behavior.)
                result = list(a)
                for i, v in enumerate(b):
                    if i < len(result):
                        result[i] = self._simple(result[i], v, memo=memo, **options)
                    else:
                        result.append(v)
                return _sequence_with(a, result)
            return b

        if isinstance(b, typing.Mapping):
            if isinstance(a, typing.Mapping):
                merged = dict(a)
                merged.update(b)
                return _mapping_with(a, merged)
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
        memo: dict | None,
        mergelists: bool,
        **options,
    ) -> typing.Sequence:
        key = _memo_key(a, b)
        if key in memo:
            return memo[key][2]
        result = list(a)

        if mergelists:
            # Collect dict elements from b for potential positional merge.
            b_dicts: dict[int, typing.Mapping] = {
                i: item for i, item in enumerate(b) if isinstance(item, typing.Mapping)
            }

            # Extend with unique non-dict items from b.
            for item in b:
                if (is_scalar(item) or is_array(item)) and item not in result:
                    result.append(item)

            # Merge dict elements by position if requested.
            for i, a_item in enumerate(result):
                if isinstance(a_item, typing.Mapping) and i in b_dicts:
                    # PEEK, do not pop: only a dict that actually merges leaves
                    # b_dicts here. Popping before the overlap check dropped a
                    # non-overlapping positional dict entirely — it was neither
                    # merged nor left for the append loop below (silent data
                    # loss, e.g. Deep([{"k":1}], [{"z":9}], mergelists=True)
                    # returned [{'k': 1}]).
                    b_item = b_dicts[i]
                    # Only merge when at least one key overlaps.
                    if any(k in a_item for k in b_item):
                        del b_dicts[i]
                        result[i] = self._deep(
                            a_item, b_item, memo=memo, mergelists=mergelists, **options
                        )

            # Append any remaining b dict entries that were not merged.
            for v in b_dicts.values():
                result.append(v)
        else:
            # Fast path: extend unique non-dict items, then append dict items to preserve exact behavior
            for item in b:
                if not isinstance(item, typing.Mapping):
                    if (is_scalar(item) or is_array(item)) and item not in result:
                        result.append(item)
            for item in b:
                if isinstance(item, typing.Mapping):
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
