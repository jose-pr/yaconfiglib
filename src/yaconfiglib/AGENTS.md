# `yaconfiglib` — public API header

Header-file-style reference for the `yaconfiglib` package: every public export with its
signature, arguments, contract, and gotchas, so this module can be consumed without
reading its source. Kept current with the public API. For the library overview,
features, and code layout, see <https://github.com/jose-pr/yaconfiglib>.

## Top-level (`import yaconfiglib`)

- **`load(fp, **kwargs) -> object`** / **`loads(s: str | bytes, **kwargs) -> object`** —
  one-shot load from a file path/pointer or an in-memory string/bytes; constructs a fresh
  `ConfigLoader` per call. Routing rule: a keyword named in `ConfigLoader.__init__`
  (`base_dir`, `encoding`, `recursive`, `key_factory`, `interpolate`, `merge`,
  `merge_options`, `allow_commands`, `sandbox`, ...) configures that loader, so it also
  governs nested `!include` targets and command-output parses; **every other** keyword
  goes to `.load()` and reaches the backend as a reader argument
  (`json_decoder_options`, `ini_default_section`, `environment=`, `loader=`,
  `transform=`, `flatten=`, `default=`). `merge=None` falls back to the default. A
  keyword no backend reads is ignored, so a misspelled option fails silently rather than
  raising.
- **`load_as(model_cls, *pathname, **kwargs) -> T`** — like `load`, but takes **several**
  sources and hydrates the merged mapping into `model_cls` (see `.load_as` below).
  Keywords route as in `load`, so `strict`/`base_dir`/`merge` configure the loader.
- **`dump(obj, fp, **kwargs) -> None`** / **`dumps(obj, **kwargs) -> str`** — serialize
  *obj* to YAML (delegates to `backends.yaml.YamlConfig.dumps`) and write it to *fp* (a
  path or a writable file-like) or return it as a string. Always YAML, whatever *fp* is
  named. Every `dict` subclass — a loaded `DotAccessibleDict` included — is written as a
  plain mapping, so the output loads back; a caller-supplied `Dumper=`/`dumper_cls=`
  is used as given and restores PyYAML's default handling.
- **`ConfigLoader`** — the main orchestrator; see below.
- **`ConfigLoaderMergeMethod`** — `MergeMethod` extended with `Last`/`List`/`Hash`; see
  "Merge strategies".
- **`CommandsDisabledError(ValueError)`** — defined in `utils.trust`, re-exported from
  `loader` and the package root. Raised when a command source (`cmd://`, `exec://`,
  `sh://`, a `+fmt` variant, or a script-extension file) is reached while
  `allow_commands=False` is in effect, including via a nested `!include` (raised by
  `ConfigLoader._load()`, with a backstop in `CommandBackend.load()` during a load).
- **`ConfigBackend`** — the pluggable-backend protocol; see `backends/base.py` below.
- **`MergeMethod`**, **`typed_merge`**, **`OpaqueMerge`**, **`opaque`**,
  **`TypedNamespace`** — re-exported from `utils.merge` / `utils.typing_merge`; see
  "Merge strategies" / "Typed merge".

## `ConfigLoader` (`loader.py`)

`ConfigLoader(base_dir="", *, encoding=None, path_factory=None, loader_factory=None,
recursive=None, key_factory=None, log_level=None, interpolate=None,
merge=ConfigLoaderMergeMethod.Simple, merge_options=None, ignore_error=False,
inject_env=False, strict=False, allow_commands=True, sandbox=False)`

All constructor args become instance defaults, overridable per-call. Notable ones:

- `base_dir` — directory the relative file-path sources you pass to `.load()` resolve
  against; also the anchor for includes inside documents that are not files (`loads()`,
  `#!` strings, streams, command output). A relative `!include` inside a YAML **file**
  resolves against that file's own directory instead.
- `recursive` — whether glob sources (`**/*.yaml`) recurse into subdirectories.
  Default `False`. Forwarded to `parse_sources` by both `.load()` (which also honors a
  per-call `recursive=`) and `.load_all()` (instance setting only — it has no per-call
  `recursive` parameter).
- `key_factory` — `(path, value) -> str` merge/document key (default: filename stem);
  as a string it's a `Path` attribute name, or `"%<jinja-expr>"` for a template.
- `merge` — a `ConfigLoaderMergeMethod` (or any `Merge`-compatible callable) applied
  between successive sources, left-to-right.
- `ignore_error` — `bool` (ignore/re-raise every load error uniformly) or a predicate
  `(error, **context) -> bool` deciding per-error whether to skip and continue. Errors
  are always handed to the predicate and logged, never silently swallowed.
- `allow_commands=False` — a command source anywhere in the load, including through a
  nested `!include`, raises `CommandsDisabledError` instead of executing. Set this when
  loading configuration you don't fully trust. A per-call value reaches nested includes
  too. Does **not** restrict a `CommandBackend` constructed and called outside a load.
- `interpolate=True` — after every source is merged, each string in the result is
  rendered **once**, with the whole merged document as scope — `!include`d values
  included, so they see the including document's keys. Top-level keys render after the
  keys they refer to, so chains (`logs: "{{ base }}/logs"`, `err: "{{ logs }}/err"`)
  resolve fully whatever order they are written in; a rendered value is never rendered
  again, so an escaped `{{ '{{ x }}' }}` stays literal. A reference to a key still being
  resolved sees that key's current value; a cycle between keys raises `ValueError` when
  `strict` is in effect.
- `sandbox=True` — interpolation runs in Jinja2's `SandboxedEnvironment`, blocking
  attribute traversal into Python internals (SSTI protection) for untrusted config
  values. Applies to both template strings and bare `{{ expr }}` values, and bare
  expressions keep their non-string type under the sandbox exactly as without it. A
  per-call value reaches nested includes too.
- Trust only tightens: the effective `(allow_commands, sandbox, strict)` of a load lives
  in a `contextvars.ContextVar` (`utils.trust.current_policy()`); nested loads and
  included documents can disable commands or enable the sandbox, never the reverse.
  `transform` and `%`-form `key_factory` expressions are evaluated sandboxed whenever
  `sandbox=True` or `allow_commands=False` is in effect.
- `!include` mapping form — accepts only `pathname`, `encoding`, `transform`,
  `key_factory` (`"%<expr>"` form only), `default`, `flatten`, `merge`, `merge_options`,
  `recursive`; other keys are dropped with a WARNING.
- `log_level` — **deprecated and ignored**; it never changed anything. Passing it warns
  (`DeprecationWarning`); configure the `yaconfiglib` logger with `logging` instead.
- `inject_env=True` — with `interpolate`, exposes a read-only snapshot of `os.environ` to
  templates as `env` (templates cannot change the process environment); also exposed to
  `.j2` source rendering.

### Methods

- **`.load(*pathname, recursive=None, encoding=None, loader=None, transform=None,
  default=None, key_factory=None, flatten=False, interpolate=None, merge=None,
  merge_options=None, allow_commands=None, sandbox=None, **reader_args) -> object`** —
  resolve `*pathname` via `parse_sources` (globs, nested lists, in-memory `#!`-marked
  strings, streams, command URIs), parse each with its backend, and merge in order.
  `pathname` empty → loads one empty in-memory document. `flatten=True` flattens the
  final mapping-of-mappings or sequence-of-sequences by one level (error if the result
  is neither); an empty (`None`) member is skipped, and any other non-mergeable member
  raises `TypeError` naming it. `transform` is a Jinja2 expression evaluated per-document (as `value`)
  before merging; it is evaluated sandboxed when `sandbox=True` or
  `allow_commands=False` is in effect. `encoding` also applies to every
  `!include`/`!load` target that names none of its own, at every depth. With
  `interpolate`, the merged result is rendered once at the end of the call (never
  per source, and never inside an include). `default=` is returned only when **no**
  source loads (nothing matched, or every source failed under `ignore_error`); it is
  never merged into a loaded document. Dict results are
  wrapped in `DotAccessibleDict`. `merge_options` is a
  **per-call override only** — it is never written back onto `self.merge_options`.
- **`.load_as(model_cls, *pathname, **kwargs) -> T`** — `.load(...)` then hydrate
  `model_cls`: a Pydantic `BaseModel` (`model_validate`/`parse_obj`), else a
  `dataclasses` type, else `model_cls(**data)`. Raises `TypeError` if the loaded result
  isn't a dict. Pydantic is detected by probing `sys.modules`, never by importing it: a
  class can only subclass `BaseModel` if pydantic is already imported. Dataclass keys are
  filtered by the **class** signature, so `InitVar` parameters are passed while
  `field(init=False)` and a document key named `self` are not; a field annotated with a
  dataclass/Pydantic model (or `Optional` of one) is hydrated into an instance, while
  containers of models (`List[Model]`) stay as loaded. `**kwargs` goes to `.load()`, so
  constructor-only options belong on the constructor — or use top-level `load_as`.
- **`.load_all(*pathname, encoding=None, interpolate=None, sandbox=None,
  allow_commands=None, **reader_args) -> Iterator[object]`** — like `.load()` but yields
  each resolved source's document individually (optionally interpolated independently)
  instead of merging them — for a directory of unrelated config files rather than
  layered ones. `sandbox`/`allow_commands` override the instance settings for this call,
  including nested `!include` targets; the policy is not held while the consumer's loop
  body runs. `encoding` also applies to every `!include`/`!load` target that names none
  of its own, at every depth.
- **Include resolution** — a relative `!include`/`!load` path inside a YAML *file*
  resolves against that file's directory, at every depth; absolute paths and command
  URIs are used as written; globs expand next to the including file; a `.j2` template's
  includes resolve next to the template; includes inside non-file documents (`loads()`,
  `#!` strings, streams, command output) resolve against `base_dir`. An included
  source's `pathname` is absolute.
- **Include cycles** — a source that (directly or through `!include`) loads itself raises
  `ValueError("include cycle: a -> b -> a")`; with `ignore_error` it goes to the
  predicate like any load error.

### `DotAccessibleDict(dict)`

Wraps every dict result. `__getattr__`/`__setattr__` give `config.a.b.c` access;
`.get(key, default=None, dig=True)` additionally supports a dotted-string key
(`get("database.credentials.user")`) that traverses nested dicts, short-circuiting to
`default` on a `None` or missing intermediate. Nested dict values are lazily wrapped in
`DotAccessibleDict` on first access/get, not eagerly at construction.

### `DEFAULT_LOADER`

A module-level `ConfigLoader()` instance (construction-time singleton; not part of the
`load`/`loads` call path, which builds a fresh loader per call).

## Merge strategies (`utils/merge.py`)

`MergeMethod(IntEnum)`: `Simple` (scalars/lists replace; dicts update shallowly),
`Substitute` (dicts merge recursively; lists always replace), `Deep` (fully recursive;
dicts merge key-by-key, lists extend with unique items — pass `mergelists=True` to also
merge dict elements positionally when keys overlap). Call as `method(a, b, *,
memo=None, **options)`.

A **leaf** is any value that is neither a mapping nor an array: strings, numbers, dates,
datetimes, `Decimal`, sets, enum members, objects a backend produced. Leaves replace, and
a shape change (mapping ↔ leaf, string → list, list → mapping) replaces too — no strategy
raises for a type combination any more. The exception: a mapping overridden by a
**non-empty list of mappings** folds them in, in order; any other list, `[]` included,
replaces the mapping. `Deep` list extension keeps unique **non-mapping** items whatever
their type, appended in the new source's order; an item is a duplicate only when an
existing one has the **same type** and compares equal (so `True` is not a duplicate of
`1`). Mapping items are always appended, or merged with the mapping at the same index
when `mergelists=True` and the two share a key.

Strategies are copy-on-write: they **never modify their inputs**, so a document whose
keys share one mapping (a YAML anchor or a `<<:` merge key) can be overridden without
rewriting its siblings — use the return value. The result may share unchanged
sub-objects with the inputs; `copy.deepcopy` it before mutating if the sources must stay
intact. A mapping's own type is preserved (`OrderedDict`, `defaultdict` with its factory,
`DotAccessibleDict`); a Mapping whose constructor rejects the merged keys comes back as a
plain `dict`. `memo` is internal — a per-call map of the container pairs already merged,
so a node aliased in both inputs stays aliased in the result and a cyclic (self-
referencing) document merges instead of raising `RecursionError`. Callers leave it `None`.

`ConfigLoaderMergeMethod` (`loader.py`, extends `MergeMethod`) adds loader-specific
strategies: `Last` (each source replaces the running result), `List` (collect one entry
per source, in order), `Hash` (collect into a dict keyed by `configloaderkey`, i.e. each
source's merge key). These three require `configloaderkey=` on every call — only
`ConfigLoader.load()` supplies it; calling them directly needs it passed explicitly.
Members pickle (so a strategy can be handed to a worker process), and `Last`/`List`/`Hash`
keep working on an enum built with `ConfigLoaderMergeMethod.extend(...)`: the seed value
comes from a `_init_<name>` hook looked up by member name, not from an identity check.

`is_scalar(obj) -> bool` (one of `int`/`str`/`bool`/`float`/`None`/`bytes`; unchanged, and
**not** the merge leaf rule), `is_array(obj, mutable=False) -> bool` (sequence but not a
mapping/str/bytes; `mutable=True` also requires `MutableSequence`) — used throughout to
distinguish merge branches.

## Typed merge (`utils/typing_merge.py`)

- **`typed_merge(cls, *objects, init=True) -> T`** — recursively merge `*objects` into
  an instance of `cls`, guided by `cls`'s type hints. Mappings/dataclasses/
  `Namespace`-likes merge field-by-field (collecting a field's value across every
  object, then recursing per-field using its type hint); sequences take the last
  object's value, converting each element via the type arg if generic; scalars use the
  last object, coerced through `cls` if not already an instance — a **`bool`** hint
  parses `true/yes/on/1` and `false/no/off/0` (stripped, case-insensitive) and raises
  `ValueError` on any other string, where `bool('false')` would be `True`. `objects` empty →
  `None`. A **mapping** target is built positionally, `cls(merged)`, with a single
  `cls(**merged)` retry for a keyword-only `dict` subclass and a plain `dict` for an
  abstract origin; a `defaultdict` origin carries over the last source's
  `default_factory`. A **dataclass** target is built `cls(**merged)` with its declared
  `field(init=False)` names popped (unknown keys still pass through, which a custom
  `**kwargs` `__init__` relies on). A **`TypedNamespace`** target is always assembled
  without `__init__`. `init=False` builds via `cls.__new__` + attribute/item assignment
  instead of `cls(**merged)` — use when `__init__` has required positional-only args or
  side effects you want to skip.
  - **`None` sources are skipped at every level**, before any hint resolution or hook
    call: a later `None` never overrides an earlier value, a field whose every value is
    `None` stays `None`, and all-`None` (or no) objects give `None`. Pass an explicit
    empty value to clear a field.
  - **Parametrized generics** are honored: `Dict[str, int]` coerces each key to the
    mapping's key type and each value to its value type, `List[str]`/`Tuple[str, ...]` coerce each element, and an
    unparametrized `dict`/`list` leaves element types alone. `str`/`bytes` hints are
    never treated as element sequences.
  - **Sequence hints** require a sequence value: a `str`, a `Mapping`, or a non-iterable
    raises `TypeError` instead of being split into characters or keys (`bytes` stays
    allowed, so a `bytearray` hint consumes it). An **abstract** origin
    (`Sequence[str]`, `MutableSequence[int]`) is built as the value's own concrete type,
    else `list`. A **`NamedTuple`** is rebuilt as `origin(*items)` with each item taking
    its field's hint — never zipped against `_fields`, which would truncate a surplus
    item — so the constructor enforces arity. A **heterogeneous `Tuple[int, str]`**
    coerces by position and raises `TypeError` on a length mismatch; `Tuple[X, ...]`
    applies `X` to every element. **`range`** and any type whose constructor rejects a
    list of items (a `tuple` subclass with a fixed `__new__`) are returned unchanged.
  - A **`Union`/`Optional`** hint is resolved against the value being merged: `NoneType`
    members are dropped (so `Optional[Dict[str, int]]` behaves exactly like
    `Dict[str, int]`), then the first member the value is already an instance of wins —
    `Union[int, str]` keeps `"8080"` a string — and failing that the first member, which
    is the coercing case. Narrow the hint to force a coercion; reordering the union does
    nothing. `Annotated[X, ...]` is stripped and coerces through `X`. **`typing.Any`** —
    directly, as a type arg, or as any union member — means last object wins, uncoerced
    (`Any` is a class on 3.11+, so this is an explicit short-circuit, not the non-class
    path).
  - **Annotations are resolved per entry.** `typing.get_type_hints` is all-or-nothing, so
    when it raises the fallback walks the MRO base-first and resolves each class's own
    annotations one at a time: one unresolvable forward reference no longer leaves every
    sibling field uncoerced. On 3.14+ a class `__dict__` has no `__annotations__` (PEP
    649) — read them with `annotationlib.get_annotations(..., Format.FORWARDREF)`.
  - A field annotated with a **non-class hint** (e.g. a factory function like
    `netutils.IPNetwork`) is treated as opaque: last value wins, coerced through the
    callable when possible.
  - Two per-type hooks: a classmethod **`__merge__(cls, *objects, init=True)`**
    overrides merging entirely for that type; a per-field **`_parse_<field>(value)`**
    coerces that field's value as it's collected from each source object — but never on
    a source that is already a `TypedNamespace`, which parsed its fields at
    construction; for a `TypedNamespace` target, a raw source's values go through the
    target's own hooks instead, so every value is parsed exactly once and a parser need
    not be idempotent. `__merge__` is
    looked up on the hint's stripped origin, so it is found through `Optional[Zone]`,
    `MyGeneric[int]` and any other parameterized spelling — typing aliases do not forward
    dunders, so a lookup on the alias itself found nothing. It never receives `None`.
- **`OpaqueMerge`** — mixin: `__merge__` returns the last object unchanged (no
  field-by-field introspection or coercion), including under `Optional[...]`. Use for a
  fully-built config object whose `__init__` already normalized its fields. A field
  annotated by a factory function needs no mixin — it is already coerced through that
  callable per field.
- **`opaque(cls) -> cls`** — class decorator equivalent of `OpaqueMerge`, without
  altering `cls`'s base classes.
- **`TypedNamespace(argparse.Namespace)`** — applies `_parse_<field>` coercers at
  construction time, so a built instance is already normalized. Compose with
  `OpaqueMerge`/`opaque` when such an instance should also skip re-merging. A
  `typed_merge` into a `TypedNamespace` subclass does **not** call its `__init__` (that
  would re-parse already-parsed values): put any other `__init__` work in a
  `_parse_<field>` hook or `__merge__`.

## Source resolution (`utils/source.py`)

- **`parse_sources(sources, base_dir=None, encoding=None, memo=None, path_factory=None,
  recursive=None) -> Iterator[Path]`** — flattens `sources` (paths, glob patterns,
  command URIs, in-memory `"#!<name>\n<content>"` strings (`"#!\n<content>"` for an
  unnamed document, auto-named `mem-N.yaml`), open streams, or nested
  iterables) into concrete `Path`-like objects. Command URIs (`exec://`, `cmd://`,
  `sh://`, `+fmt` variants) pass through unresolved/unexpanded. In-memory content and
  streams materialize to a `pathlib_next` `MemPath` when available, else a tracked temp
  file (best-effort cleaned at interpreter exit). `memo` dedupes repeat sources across
  recursive calls (mutated in place; logs and skips a duplicate rather than erroring).
- **`has_glob_pattern(path) -> bool`** — whether *path* contains glob magic characters.

## Backends (`backends/`)

- **`ConfigBackend`** (`base.py`, `typing.Protocol`) — the pluggable-backend contract.
  Subclassing and importing the subclass is the entire registration mechanism (no
  registry call needed); lookup walks `__subclasses__(recursive=True)` in
  definition order.
  - Override **`load(self, path, **options) -> object`** (required). Unrecognized
    `**options` should generally be ignored, not raise — `ConfigLoader` forwards a
    shared option set to every backend it calls.
  - Optional **`load_all(self, path, **options) -> Iterable[object]`** (default: yields
    one `load()` result), **`dumps(self, data, **options) -> str`** (default: raises
    `NotImplementedError`). Call a backend instance's `dumps()` for a non-YAML
    format: the top-level `yaconfiglib.dump`/`dumps` always write YAML.
  - Class attrs: `PATHNAME_REGEX` (matched against the path's filename to auto-select
    this backend; `None` → name-only selection, e.g. `EnvVarBackend`), `NAME` (explicit
    `loader="name"` registry key; default derived from the class name, lowercased,
    trailing `Loader`/`Config` stripped), `DEFAULT_ENCODING` (`"utf-8"`),
    `DEFAULT_PATH_FACTORY`.
  - `get_class_by_name(name)`, `get_class_by_path(path)` (raises `NotImplementedError`
    if no backend claims the path), `can_load_path(path)`. When an **optional** backend's
    import failed, `backends/__init__.py` records its name, `PATHNAME_REGEX` pattern and
    the import error in `base._MISSING_BACKENDS`, and both that message and `_load`'s
    "Unknown configuration format/loader" append the extra to install (via the private
    `ConfigBackend._missing_backend_hint`). A missing backend stays **unregistered** —
    that keeps dispatch and dotenv's give-way rule unchanged — so the error text is the
    only thing that improves. The recorded patterns are duplicated from the classes
    (a missing module cannot be asked for its regex); a test pins them equal.
  - A backend instance is directly usable as a PyYAML tag constructor
    (`__call__` recognizes the `(loader, node)` call shape and routes to
    `_yaml_tag_constructor`, supporting scalar/sequence/mapping tag forms).
- **`YamlConfig`** (`.yaml`/`.yml`) — auto-registers `!include`/`!load` PyYAML tag
  constructors on a private yaconfiglib-owned subclass of the loader class (default: its
  own `SafeLoader` subclass; a `loader_cls=` or `master` class is wrapped the same way)
  the first time a `ConfigLoader` (`loader=`) parses YAML; idempotent per owned class.
  `yaml.SafeLoader` and caller-supplied classes are never modified, so a plain
  `yaml.safe_load` never resolves `!include`, and a parse with no driving `ConfigLoader`
  raises `yaml.constructor.ConstructorError` on the tags. Manual registration is
  unnecessary. `.load(path, encoding=None, master=None,
  loader_cls=None, path_factory=None, loader=None, origin=None, **options)`; `master`
  inherits anchors/aliases from an in-progress parse (used by the tag constructors
  themselves) and carries the call's include encoding. `origin` is the document that
  relative `!include`/`!load` paths resolve against (default: *path* itself; a rendered
  `.j2` passes its template's path).
  **Gotcha**: nested `!include`/`!load` route through the driving `ConfigLoader` stashed
  on the loader instance as `_yaconfiglib_config_loader` — not the loader captured when
  the tag was first registered — so each loader's own `allow_commands`/`merge` apply to
  its own nested includes, and `base_dir` applies to includes in non-file documents. The
  resolution anchor (`_yaconfiglib_include_origin`) and the call's encoding
  (`_yaconfiglib_include_encoding`, inherited through `master`) ride on the same
  instance.
Every file backend accepts a `str` as well as a `Path` (converted through
`path_factory=` or `DEFAULT_PATH_FACTORY`), takes `encoding=None` to mean
`DEFAULT_ENCODING`, and ignores one leading UTF-8 byte-order mark. That is what makes a
backend instance usable directly as a PyYAML tag constructor, which hands `load()` a
bare string and no encoding. Both rules live in one place — the protected
`ConfigBackend._coerce_path()` and `ConfigBackend._read_text()`; a new file backend
should call them rather than `path.read_text()`. `DEFAULT_ENCODING` stays `utf-8` and
must not become `utf-8-sig`: it is also used to *write* an in-memory source's `#!`
marker, and would add a BOM there.

- **`TomlConfig`** (`.toml`) — stdlib `tomllib` if available, else its `tomli`
  backport (`yaconfiglib[toml]`, installed only below 3.11).
  `.load(path, encoding=None, path_factory=None, **options)`. The third-party `toml`
  package is deliberately **not** a further fallback: it implements TOML 0.5, so a
  transitively installed copy would silently parse a TOML 1.0 document differently from
  3.11+ (heterogeneous arrays rejected, a lowercase `z` datetime left naive, offset
  datetimes unpicklable). `tomli` 2.4+ accepts some TOML 1.1 syntax that 3.11-3.14's
  `tomllib` rejects, so "identical on every Python" holds for TOML 1.0 only.
- **`JsonConfig`** (`.json`) —
  `.load(path, encoding=None, json_decoder_options=None, path_factory=None, **options)`.
- **`IniConfig`** (`.ini`/`.cfg`) — `IniConfig(interpolation="basic")`;
  `.load(path, encoding=None, path_factory=None, ini_default_section=None,
  ini_interpolation=<unset>, **options)`. `ini_interpolation` takes `"basic"`,
  `"extended"` (`${section:key}`), `"none"`/`None`, or a `configparser.Interpolation`;
  anything else raises `ValueError`. Its default is a module sentinel, not `None`,
  because `None` is itself a valid choice. Interpolation stays **on** by default —
  `%(here)s` references are deliberate in alembic-style files — so a logging formatter
  value needs `ini_interpolation=None`; through an `!include` that comes from
  `loader_factory`, since reader options are not in the include allowlist. `[DEFAULT]`
  keys are inherited by sections and not returned; a DEFAULT-only file logs a warning
  naming the `ini_default_section="<unused name>"` escape hatch (adding a `DEFAULT` key
  to the result instead would break section iteration for every file that uses it).
- **`DotenvBackend`** (`NAME="dotenv"`, `.env`, `*.env`, `.env.<stage>[.<more>]`) —
  `DotenvBackend(lowercase=True, strict=False)`;
  `.load(path, encoding=None, path_factory=None, lowercase=None, dotenv_strict=None,
  **_options) -> dict[str, str]`. Keys may contain `.`/`-`; double-quoted values may
  span newlines and decode `\n \r \t \" \\`; single-quoted values may span newlines
  and are raw (use them for a literal backslash); an unquoted value treats quotes as
  ordinary characters and is cut at a `#` preceded by whitespace. A quoted value ends at
  its first matching quote, and only whitespace or a `#` comment may follow. **Only a
  newline ends an entry** — a form feed, U+0085, U+2028 and friends are value characters,
  which is why the scanner splits on `"\n"` and never uses `str.splitlines()` or a bare
  `\s`. An unparseable line warns and is skipped, or raises under
  `strict`/`dotenv_strict` (which also rejects an assignment-free file); an unterminated
  quote always raises. It gives
  way when the name's final suffix belongs to another format (`app.env.yaml` → YAML,
  `.env.j2` → rendered) or when any other backend, including a custom one, claims the
  name: the regex excludes the known suffixes AND `can_load_path` defers to every
  non-dotenv backend, because neither test alone covers a custom format or a missing
  optional dependency. `loader="dotenv"` forces it.
- **`EnvVarBackend`** (`NAME="env"`, not file-based) —
  `EnvVarBackend(prefix="", lowercase=True, nested_delimiter=None, coerce=False)`;
  `.load(path=None, prefix=None, lowercase=None, nested_delimiter=None, coerce=None,
  **_options) -> dict`. `path` is ignored (present only to satisfy the generic `load`
  signature). `nested_delimiter="__"` splits keys after prefix-stripping into nested
  dicts (`APP_DB__PORT` with `prefix="APP_"` → `{"db": {"port": ...}}`). `coerce=True`
  converts each string value to `None`/`bool`/`int`/`float`/parsed-JSON where it matches
  a `[`/`{`-leading value, else leaves it a string.
- **`CommandBackend`** (`NAME="command"`) — runs `cmd://`/`exec://`/`sh://` (and `+fmt`
  variants, e.g. `cmd+json://...`) sources as a subprocess and parses stdout, routing by
  the `+fmt` suffix or a `#!fmt` shebang line in the output. The command runs with stdin
  closed. `.load(..., timeout=None)`: an opt-in number of seconds (reachable per call,
  e.g. `loader.load("cmd://...", timeout=30)`) after which the command and its child
  processes are killed and `subprocess.TimeoutExpired` is raised; no timeout by default.
  With no `format=`/`+fmt`/shebang it **sniffs**: json (any value), yaml **only for a
  mapping or list**, toml, dotenv **strict** (every non-comment line an assignment), ini,
  else the raw stdout string. The yaml and dotenv restrictions are what make the later
  candidates reachable — YAML turns any text into a scalar and lenient dotenv turns a
  word into a bare key — so a candidate's result must be *checked*, not just produced.
  Explicit `format=`, `+fmt` and shebang routes keep lenient dotenv and propagate a
  single candidate's parse error.
- **`PythonBackend`** (`NAME="python"`) — passes an in-memory Python object straight
  through as the parsed document.
- **`Jinja2ConfigLoader`** (`NAME="jinja2"`, `.j2`/`.jinja2`) — renders the file as a
  Jinja2 template, then parses the result with the backend matching the name minus the
  suffix (`settings.yaml.j2` → YAML). `.load(path, encoding=None, loader=None,
  environment=None, **kwargs)`. The render context is `pathname`, plus `env` (read-only
  `os.environ` snapshot) when the parent loader has `inject_env=True`. Follows the load's
  effective policy: sandboxed (`utils.jinja2.get_environment(strict, True)`) when
  `sandbox=True` or `allow_commands=False` is in effect, `StrictUndefined` when `strict`;
  a non-`SandboxedEnvironment` `environment=` under a hardened policy raises
  `ValueError`, and a rendered command source raises `CommandsDisabledError` when
  commands are disabled. The name must keep the format extension it renders to
  (`config.yaml.j2`); otherwise `NotImplementedError` names the template, raised before
  it is read or rendered.

## Jinja2 interpolation (`utils/jinja2.py`)

- **`interpolate(data, globals=None, environment=None) -> object`** — recursively
  renders Jinja2 templates through a dict/mapping/sequence/string structure, **one pass**:
  each string is rendered once against `globals` as given, and a rendered result is never
  re-rendered (ordering references between values is the caller's job —
  `ConfigLoader` does it per top-level key). Mutable mappings/sequences are rewritten
  **in place** and returned; immutable ones are copied. A string
  with no `{{`/`{%`/`{#` marker short-circuits (returned as-is). A bare
  `{{ expr }}` (nothing else but an optional trailing newline) is **evaluated as an expression** so the
  original Python type is preserved (e.g. stays an `int`, not stringified); anything
  else renders as a normal Jinja2 template (always a string). A container referenced
  more than once (YAML anchors/aliases) is walked once and every reference shares the
  result.
- **`references(code, environment=None) -> frozenset[str]`** — the names *code* reads
  from its context (cached like `compile`/`eval`); a template that does not parse yields
  no names. Used to order values by what they refer to.
- **`compile(code, environment=None, globals=None) -> Callable[..., str]`** /
  **`eval(code, environment=None, globals=None) -> Callable[..., object]`** — LRU-cached
  (1024 entries, keyed on `(code, id(env))`, weakref-guarded against an `id()` reuse
  collision when an `Environment` is GC'd) compiled-template / expression-evaluator
  factories. `eval` wraps `code` in a `{% do %}` statement to capture and return its
  value without stringifying. `globals=` applies to the returned callable only and is
  **not** part of the cache key: the template is cached without it and the values are
  merged under the render's keyword arguments, which still win. Both caches are guarded
  by a lock, so they are safe to use from several threads.
- **`load_template(source, name=None, filename=None, environment=None, globals=None) ->
  Template`** — one-shot compile, uncached.
- **`DEFAULT_ENV`** — module-level `Environment(extensions=["jinja2.ext.do"])` used when
  `environment=` is omitted.

- **`get_environment(strict, sandbox=False) -> Environment`** — the shared interpolation
  environment for `(strict, sandbox)` (a `SandboxedEnvironment` when `sandbox`,
  `StrictUndefined` when `strict`, both with `jinja2.ext.do`), created once per
  combination so `interpolate=True` calls don't reconstruct one per load.
