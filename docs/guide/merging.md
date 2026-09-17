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

## Glob sources

A source containing `*`, `?` or `[` is expanded, and the matches are merged in
a **fixed order**: path components compared by code point, so the same tree
layers the same way on every machine and filesystem. Sources keep the order you
passed them.

```python
# 00-base.yaml, then 10-env/prod.yaml, then 99-local.yaml — whatever the
# directory listing happens to return.
ConfigLoader(base_dir="conf", recursive=True).load("**/*.yaml")
```

- **`recursive=True`** enables `**`. Write `**/*` (or `**/*.yaml`) to select
  every file at every depth: a bare `**` follows the running interpreter, as
  `pathlib` does — it selects files too from Python 3.13, and directories only
  before, which means it loads nothing on 3.9-3.12 once directories are
  skipped.
- **Directory matches are skipped**, so `envs/*` loads the files directly under
  `envs/` and ignores its subdirectories. A source that *names* a directory
  outright still fails, as it should.
- **Dotfiles are matched** by wildcards, as `pathlib` does.
- **A missing directory matches nothing** rather than raising, and a pattern
  that matches nothing contributes nothing.
- **`base_dir` is never pattern text.** A base directory called `proj [v2]`
  works without escaping, because expansion runs relative to it.
- **A path that exists is loaded literally.** If a file really is named
  `z[1].yaml`, that is what loads. For an *absolute* pattern under a directory
  whose name contains `[`, `*` or `?`, pass that directory as `base_dir` (or
  `glob.escape` it), since there is no literal base to expand from.
- **A directory that cannot be listed raises.** If a directory under a glob is
  unreadable (permission denied, say), the `OSError` is raised rather than the
  files quietly vanishing. `ignore_error` can skip it — the predicate is called
  with `phase="glob"` and `path=` that directory, once per directory — and the
  rest of the pattern still loads, so one locked subdirectory does not drop a
  whole layer. A parent that does not exist, or is not a directory, still just
  matches nothing.
- **The same file loads once**, however it was named: `conf/app.yaml`,
  `./conf/app.yaml`, `conf/../conf/app.yaml` and (on Windows) `conf/App.yaml`
  are one file. The key is lexical — no symlink resolution — so two symlinked
  names stay distinct on purpose.
- **An explicitly named file beats a glob match for it**, wherever the two
  appear, and keeps its own position. That is what makes both layering idioms
  work: `load("base.yaml", "*.yaml")` and `load("*.yaml", "local.yaml")`.
- On Windows, a `**` source that crosses a **junction** can repeat files; that
  is an upstream `pathlib-next` limitation, not a yaconfiglib rule.

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
  directory of files, keyed by filename" patterns. Two sources with the same
  key do not both survive — the later one replaces the earlier, and a warning
  says so.

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

For a command source, `pathname` — and therefore the default key — is the
**whole source text**, `cmd+json://...` included, since a command has no
filename to key on.

The default key is the **filename stem**, which a directory glob repeats: every
match of `services/*/config.yaml` is keyed `config`, so all but the last are
dropped (with a warning). Key on the directory instead:

```python
loader = ConfigLoader(
    merge=ConfigLoaderMergeMethod.Hash,
    key_factory=lambda path, value: path.parent.name,   # api, billing, web
)
# or, as a Jinja2 expression:
#   key_factory="%pathname.parent.name"
```

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

### When a field does not fit

A value the target type cannot accept keeps its own exception type — `int("abc")`
is still a `ValueError` — and the message now says **which field of which
model**:

```
invalid literal for int() with base 10: 'abc' [at db.port (Outer)]
```

List and tuple items appear by index (`ports[1]`), the path is relative to the
model you asked for, and it is also readable as `error.config_key` with
`error.config_model`. `typed_merge`'s own refusals — a scalar where a sequence
is declared, a tuple-length mismatch, a string that is not a boolean — are
`yaconfiglib.ConfigError` subclasses as well as the `TypeError`/`ValueError`
they always were.

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

Every value is parsed **exactly once**, so a parser need not be idempotent
(the `split` above would fail on a second pass). Merging into a
`TypedNamespace` target skips the hooks of any source that is already a
`TypedNamespace`, applies the target's own hooks to raw sources such as a
plain dict, and assembles the result without calling `__init__`. Put anything
else a subclass's `__init__` does into a `_parse_<field>` hook or `__merge__`.

Compose `TypedNamespace` with `OpaqueMerge` for a config object that both
coerces its fields at build time and is opaque to re-merging.

A mapping target is built positionally (`origin(merged)`), so keys stay data:
non-string keys work, and `Dict[int, str]` coerces each key through the key
type — useful because JSON, TOML, INI and env sources can only produce string
keys. Abstract hints (`Mapping[str, int]`) build a plain `dict`, a
`defaultdict` origin keeps the last source's `default_factory`, and a
dataclass's `field(init=False)` names are left out of the constructor call so
`__post_init__` recomputes them.
