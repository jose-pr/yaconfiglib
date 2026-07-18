from __future__ import annotations

from .backends import ConfigBackend as ConfigBackend
from .loader import (
    ConfigLoader as ConfigLoader,
    ConfigLoaderMergeMethod as ConfigLoaderMergeMethod,
    load as load,
    loads as loads,
    dump as dump,
    dumps as dumps,
)
from .utils.merge import (
    MergeMethod as MergeMethod,
    OpaqueMerge as OpaqueMerge,
    TypedNamespace as TypedNamespace,
    opaque as opaque,
    typed_merge as typed_merge,
)

__all__ = [
    "ConfigLoader",
    "ConfigLoaderMergeMethod",
    "MergeMethod",
    "typed_merge",
    "OpaqueMerge",
    "opaque",
    "TypedNamespace",
    "ConfigBackend",
    "load",
    "loads",
    "dump",
    "dumps",
]
