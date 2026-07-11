"""
Merge strategies for combining configuration documents.

This module provides the :class:`MergeMethod` enum with three built-in strategies:

* **Simple** — shallow merge: scalars/lists replace, dicts update (top-level keys only).
* **Substitute** — like simple, but dicts are merged recursively while lists always replace.
* **Deep** — fully recursive: dicts merged key-by-key, lists extended with unique items.
"""

from __future__ import annotations

import logging
import types
import typing
from argparse import Namespace
from dataclasses import is_dataclass

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


from .typing_merge import typed_merge
