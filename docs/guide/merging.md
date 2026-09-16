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
  unique non-mapping items from the new source, appended in that source's
  order. An item counts as already present only when an existing item has
  the **same type** and compares equal, so `True` is not a duplicate of
  `1`. Mapping items are always appended; pass `mergelists=True` via
  `merge_options` to instead merge them positionally with the mapping at
  the same index, when the two share a key.
- **`Last`** — each new source simply replaces the previous result
  outright, ignoring its structure.
- **`List`** — instead of merging, collect every source's result into a
  list, one entry per source, in load order.
- **`Hash`** — collect every source's result into a dict, keyed by each
  source's merge key (see `key_factory` below). Useful for "load a
  directory of files, keyed by filename" patterns.

"Scalar" above means any **leaf**: a value that is neither a mapping nor a
list. Strings, numbers, dates, datetimes, `Decimal`s, sets and objects a
backend produced are all leaves, and a leaf replaces. When the two sides have
different shapes — a mapping overridden by a number, a string by a list — the
later value replaces the earlier one rather than raising.

The one special case is a mapping overridden by a **non-empty list of
mappings**: `Substitute` and `Deep` fold those into the mapping, in order.
Any other list replaces it, so `section: []` clears a section.

The strategies **never modify their inputs** — each returns a new result and
leaves both sides as they were. That is what makes YAML anchors and `<<:`
merge keys safe to override: several keys can share one mapping, and
overriding it for one environment leaves the others alone.

```yaml
defaults: &defaults
  db: {host: shared, pool: 5}

development:
  <<: *defaults        # shares one `db` mapping with production

production:
  <<: *defaults
```

An override layer setting `production.db.host` leaves `development.db.host`
as `shared`. A self-referencing document merges too, instead of recursing
forever.

The result may still *share* unchanged sub-objects with the sources, so
deep-copy it first if you plan to mutate it and need the sources intact.

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
field, sequences take the last object's value with each element coerced
through the element type, and scalars last-object-wins.

```python
from yaconfiglib import typed_merge

merged = typed_merge(MyConfig, base_cfg, override_cfg)
```

`None` objects are skipped at every level, so a later `None` never overrides
an earlier value; a field whose every value is `None` stays `None`, and if
nothing but `None` is given the result is `None`. To clear a field, pass an
explicit empty value (`""`, `[]`, `{}`) rather than `None`.

An `Optional[X]` or `Union[...]` hint drops its `None` member and then coerces
through the **first member the value is already an instance of** — so a hint
that already accepts the value leaves it alone (`Union[int, str]` keeps
`"8080"` as a string). A value no member accepts is coerced through the first
member. To force a coercion, narrow the hint to the one type you want (`int`,
not `Union[int, str]`); reordering the union does not change the result.
`Annotated[X, ...]` is stripped and coerces through `X`, and an `Any` member
anywhere in the union makes the whole hint `Any` — last object wins, uncoerced.

A sequence hint needs a sequence value: a string or a mapping raises
`TypeError` rather than being split into characters or keys, so pass a list.
Abstract hints (`Sequence[str]`, `MutableSequence[int]`) keep the value's own
concrete type, a `NamedTuple` is rebuilt through its field hints,
`Tuple[int, str]` coerces by position and raises on a length mismatch, and a
type that cannot be rebuilt from its items — `range`, or a `tuple` subclass with
a fixed `__new__` — is returned as it is.

A `bool` hint parses strings, since INI, dotenv and command output have no
booleans: `true/yes/on/1` and `false/no/off/0`, case- and space-insensitive.
Any other string raises `ValueError` instead of silently becoming `True`.

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

Use it when a config object's `__init__` already normalized its fields:
`typed_merge` then returns the last object unchanged instead of introspecting
its fields and coercing them. A field annotated by a factory function rather
than a class needs no mixin — it is coerced through that callable per field —
so reach for `OpaqueMerge` to skip the introspection, not to survive it. The
hook is found through `Optional[Zone]` and other unions, not only on a direct
class hint.

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
