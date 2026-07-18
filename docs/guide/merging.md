# Merging

When `ConfigLoader.load()` receives multiple sources, it loads them in
order and merges each new result into the running total using a
**merge strategy**. This is what makes hiera-style layered configuration
(base config → environment overrides → local overrides) work.

```python
from yaconfiglib import ConfigLoader, ConfigLoaderMergeMethod

loader = ConfigLoader(merge=ConfigLoaderMergeMethod.Deep)
config = loader.load("base.yaml", "production.yaml", "local.yaml")
```

Later sources take precedence. With `base.yaml` containing
`{"server": {"host": "0.0.0.0", "port": 8080}}` and `production.yaml`
containing `{"server": {"port": 443}}`, a `Deep` merge produces
`{"server": {"host": "0.0.0.0", "port": 443}}`.

## Strategies

| Strategy | Dicts | Lists | Scalars |
| --- | --- | --- | --- |
| `Simple` (default) | shallow update (top-level keys only) | replaced wholesale | replaced |
| `Substitute` | merged recursively | replaced wholesale | replaced |
| `Deep` | merged recursively | extended (see below) | replaced |
| `Last` | replaced wholesale | replaced wholesale | replaced |
| `List` | collected into a list, one entry per source | — | — |
| `Hash` | collected into a dict keyed by each source's merge key | — | — |

- **`Simple`** — the default. Good when configs override entire
  sub-sections rather than individual nested keys.
- **`Substitute`** — like `Simple`, but nested dicts are merged
  recursively instead of only at the top level; lists still replace.
- **`Deep`** — fully recursive dict merging. Lists are extended with
  unique scalar/array items from the new source; pass `mergelists=True`
  via `merge_options` to also merge dict elements positionally within a
  list.
- **`Last`** — each new source simply replaces the previous result
  outright, ignoring its structure.
- **`List`** — instead of merging, collect every source's result into a
  list, one entry per source, in load order.
- **`Hash`** — collect every source's result into a dict, keyed by each
  source's merge key (see `key_factory` below). Useful for "load a
  directory of files, keyed by filename" patterns.

## Controlling list merges

```python
loader = ConfigLoader(
    merge=ConfigLoaderMergeMethod.Deep,
    merge_options={"mergelists": True},
)
```

With `mergelists=True`, dict elements at the same position in two lists
are merged into each other (when they share at least one key) instead of
both being kept as separate entries.

## Per-source merge keys

`Hash` merging (and any custom `key_factory` use) needs a key per source.
By default it's the source's filename stem, but you can override it:

```python
loader = ConfigLoader(
    merge=ConfigLoaderMergeMethod.Hash,
    key_factory="%pathname.name",  # a Jinja2 expression, evaluated per source
)
```

`key_factory` accepts a callable `(path, value) -> str`, a string
attribute name looked up on the path object, or a `"%<jinja-expr>"`
string evaluated with `pathname` and `value` in scope.

## Overriding merge strategy per call

Every option accepted by `ConfigLoader.__init__` can also be passed to an
individual `load()` call, overriding the instance default for just that
call:

```python
loader = ConfigLoader(merge=ConfigLoaderMergeMethod.Simple)

# Uses Deep merging just this once.
result = loader.load("a.yaml", "b.yaml", merge=ConfigLoaderMergeMethod.Deep)
```

## Typed merge and custom merge behavior

`typed_merge` merges several objects into one instance of a target type,
guided by its type hints: mappings and dataclasses/namespaces merge field by
field, sequences element by element, and scalars last-object-wins.

```python
from yaconfiglib import typed_merge

merged = typed_merge(MyConfig, base_cfg, override_cfg)
```

Two per-type hooks let a class customize how it is merged.

### `__merge__` — take over merging for a type

Define a `__merge__(cls, *objects, init=True)` classmethod to fully override
how instances of that type are merged. The most common need — "this is a
fully-built object, just take the last one" — is packaged as `OpaqueMerge`
(a mixin) and `opaque` (a class decorator):

```python
from yaconfiglib import OpaqueMerge, opaque

class Zone(OpaqueMerge, argparse.Namespace):
    network: IPNetwork          # a factory function, not a class

# ...or, without changing the base classes:
@opaque
class Zone(argparse.Namespace):
    ...
```

Use it when a config object's `__init__` already normalized its fields, or
when a field is annotated by a factory function rather than a class:
`typed_merge` then returns the last object unchanged instead of introspecting
its fields (which would otherwise fail on a non-class annotation).

### `_parse_<field>` — coerce a field as it is merged

If a source object defines a `_parse_<name>(value)` method, `typed_merge`
applies it to that field's value while collecting it. `TypedNamespace`
applies the same convention at construction time, so a built object is
already normalized:

```python
from yaconfiglib import TypedNamespace

class ServerConfig(TypedNamespace):
    def _parse_port(self, value):
        return int(value)

ServerConfig(port="8080").port      # -> 8080 (int)
```

Compose `TypedNamespace` with `OpaqueMerge` for a config object that both
coerces its fields at build time and is opaque to re-merging.
