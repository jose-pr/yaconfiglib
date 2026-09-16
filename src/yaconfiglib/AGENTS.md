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
  per source, and never inside an include). Dict results are
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
  last object, coerced through `cls` if not already an instance. `objects` empty →
  `None`. `init=False` builds via `cls.__new__` + attribute/item assignment instead of
  `cls(**merged)` — use when `__init__` has required positional-only args or side
  effects you want to skip.
  - **Parametrized generics** are honored: `Dict[str, int]` coerces each value to the
    mapping's value type, `List[str]`/`Tuple[str, ...]` coerce each element, and an
    unparametrized `dict`/`list` leaves element types alone. A `Union`/`Optional`
    wrapper is unwrapped to its first concrete member first, so
    `Optional[Dict[str, int]]` behaves exactly like `Dict[str, int]`. `str`/`bytes`
    hints are never treated as element sequences.
  - A field annotated with a **non-class hint** (e.g. a factory function like
    `netutils.IPNetwork`) is treated as opaque: last value wins, coerced through the
    callable when possible.
  - Two per-type hooks: a classmethod **`__merge__(cls, *objects, init=True)`**
    overrides merging entirely for that type; a per-field **`_parse_<field>(value)`**
    coerces that field's value as it's collected from each source object.
- **`OpaqueMerge`** — mixin: `__merge__` returns the last object unchanged (no
  field-by-field introspection). Use for a fully-built config object, or one whose
  annotations aren't classes.
- **`opaque(cls) -> cls`** — class decorator equivalent of `OpaqueMerge`, without
  altering `cls`'s base classes.
- **`TypedNamespace(argparse.Namespace)`** — applies `_parse_<field>` coercers at
  construction time, so a built instance is already normalized. Compose with
  `OpaqueMerge`/`opaque` when such an instance should also skip re-merging.

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
    if no backend claims the path), `can_load_path(path)`.
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
- **`TomlConfig`** (`.toml`) — uses stdlib `tomllib` if available, else the `toml`
  package (`yaconfiglib[toml]`).
- **`JsonConfig`** (`.json`).
- **`IniConfig`** (`.ini`/`.cfg`).
- **`DotenvBackend`** (`NAME="dotenv"`, `.env`) — strips inline `#` comments outside
  quoted values; preserves `#` inside quotes.
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
