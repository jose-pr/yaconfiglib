# `yaconfiglib` — public API header

Header-file-style reference for the `yaconfiglib` package: every public export with its
signature, arguments, contract, and gotchas, so this module can be consumed without
reading its source. Kept current with the public API. For the library overview,
features, and code layout, see <https://github.com/jose-pr/yaconfiglib>.

## Top-level (`import yaconfiglib`)

- **`load(fp, **kwargs) -> Any`** / **`loads(s, **kwargs) -> Any`** —
  one-shot load from a file path, an open file object (anything with `read()` — parsed by
  the backend its file **name** selects, so `load(open("settings.toml"))` reads TOML; an
  unrecognized name such as `<stdin>` or `x.yaml.gz` is YAML, and `loader=` overrides), or
  an in-memory string/bytes; constructs a fresh `ConfigLoader` per call.
  `loads()` parses **YAML** unless `loader=` is given or the first line is `#!<name>`
  naming a format a backend claims (`"#!app.toml\n..."`); any other first line — a real
  shebang, a script name — is content. `bytes`/`bytearray` are read with `encoding=`
  (UTF-8 default, BOM ignored) and reach a byte-oriented `loader=` backend unchanged; any
  other type raises `TypeError`. `load(None)` raises `TypeError` and `load("")` raises
  `ValueError` — for optional layers pass them among `ConfigLoader().load(...)`'s sources,
  which skips a falsy one (logged at DEBUG). Routing rule: a keyword named in `ConfigLoader.__init__`
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
- **`dump(obj, fp, *, encoding=None, **kwargs) -> None`** /
  **`dumps(obj, **kwargs) -> str`** — serialize *obj* to YAML (delegates to
  `backends.yaml.YamlConfig.dumps`) and write it to *fp* or return it as a string. Always
  YAML, whatever *fp* is named. Every `dict` subclass — a loaded `DotAccessibleDict`
  included — is written as a plain mapping and a `tuple` as a plain sequence, so the
  output loads back; every other Python object keeps PyYAML's tag (a `Decimal` stays
  `!!python/object/apply:decimal.Decimal`). A caller-supplied `Dumper=`/`dumper_cls=`
  is used as given and restores PyYAML's representers.
  Two `yaml.dump` keywords default differently **with any dumper**: `sort_keys=False` and
  `allow_unicode=True`. Pass either explicitly for PyYAML's behaviour.
  `dumps(encoding=...)` raises `TypeError` — it returns `str`; encode the result, or use
  `dump(..., encoding=...)`.
  **`dump` targets**, in dispatch order: an object with `write` (a binary one is detected
  by the `TypeError` it raises before writing anything, then handed `content.encode(enc)`);
  a `str`/`bytes` path; an object with `write_text` (`pathlib.Path`, pathlib-next
  `LocalPath`/`MemPath`, remote paths — before the `os.PathLike` branch, because
  `os.fspath(MemPath(...))` raises); any other `os.PathLike`; else `TypeError`. `encoding`
  (default `utf-8`) covers everything `dump` opens or encodes itself, and is **ignored for
  a text stream**, whose own codec applies. Where the target codec is not a UTF codec
  (`open(p, "w")` on a Windows codepage), `allow_unicode` falls back to `False` rather
  than letting the write raise `UnicodeEncodeError`. A path target is opened in text mode,
  so it gets platform line endings; pass a file object opened with `newline=""` for LF.
  *obj* is serialized **before** the target is opened, so an unrepresentable value leaves
  an existing file untouched.
- **`ConfigLoader`** — the main orchestrator; see below.
- **`ConfigLoaderMergeMethod`** — `MergeMethod` extended with `Last`/`List`/`Hash`; see
  "Merge strategies".
- **`CommandsDisabledError(ConfigError, ValueError)`** — defined in `errors`,
  re-exported from `utils.trust`, `loader` and the package root (the same class through
  every path). Raised when a command source (`cmd://`, `exec://`,
  `sh://`, a `+fmt` variant, or a script-extension file) is reached while
  `allow_commands=False` is in effect, including via a nested `!include` (raised by
  `ConfigLoader._load()`, with a backstop in `CommandBackend.load()` during a load).
- **`ConfinementError(ConfigError, PermissionError)`** — defined in `errors`,
  re-exported from the package root. Raised when `confine_to=` is in effect and a file
  read resolves outside every allowed root, **before** the file is opened; see
  "Errors" below.
- **`ConfigBackend`** — the pluggable-backend **base class**; see `backends/base.py`
  below.
- **`ConfigError`** and subclasses, **`load_error_types()`** — see "Errors" below.
- **`ErrorFrame`** — one step in an error's `config_frames`; see "Errors" below.
- **`MergeMethod`**, **`typed_merge`**, **`OpaqueMerge`**, **`opaque`**,
  **`TypedNamespace`** — import them from **`yaconfiglib`** (or
  `yaconfiglib.utils.typing_merge`, where they are defined); `yaconfiglib.utils.merge`
  re-exports them explicitly through its `__all__`, so a checker accepts that path too.
  See "Merge strategies" / "Typed merge".

### Typing

- The package ships **`py.typed`**, so a consumer's checker reads these annotations.
- **Parsed results are `Any`**, not `object`: a document can be a mapping, a list or a
  scalar, and the documented `config.database.host` access has to type-check. That covers
  `load`/`loads`/`load_as`, `ConfigLoader.load`, `load_all -> Iterator[Any]`, and
  `DotAccessibleDict.__getattr__`/`.get`. (typeshed types `json.load` and
  `yaml.safe_load` the same way.)
- **Every public annotation resolves with `typing.get_type_hints` on Python 3.9**, the
  project's floor. Hints are spelled `typing.Union`/`typing.Optional` — never `X | Y`,
  which is evaluable only on 3.10+ — and `typing.Self` (3.11+) is replaced by a bound
  `TypeVar`, so `Backend.get_class_by_name` still narrows to that subclass. Every name
  used in a hint is importable at runtime, not only under `TYPE_CHECKING`.
- **Path parameters accept `os.PathLike`**, so a stdlib `pathlib.Path` works everywhere a
  pathlib-next path does; `path_factory` is a `(str) -> os.PathLike` callable.
- **`loader_factory` is a `(path) -> ConfigBackend` callable**, not `type[ConfigBackend]`:
  the class spelling type-checked but crashed, since backends take no constructor
  argument from the loader.
- **`ignore_error` predicates are checked against the keywords they are called with**
  (`phase`, `path`, `loader`, plus extras), so a predicate accepting only the error — which
  fails at runtime — is reported, while `(error, **context)` keeps checking.
- Every parameter defaulting to `None` is `Optional`/`Any`, and every `*args`/`**kwargs`
  is annotated, so a strict checker reports no partially-unknown types.

## `ConfigLoader` (`loader.py`)

`ConfigLoader(base_dir="", *, encoding=None, path_factory=None, loader_factory=None,
recursive=None, bound_loops=False, key_factory=None, log_level=None, interpolate=None,
merge=ConfigLoaderMergeMethod.Simple, merge_options=None, ignore_error=False,
inject_env=False, strict=False, allow_commands=True, sandbox=False, confine_to=None)`

All constructor args become instance defaults, overridable per-call — except
`confine_to`, which is a security setting and has **no** per-call form: passing it to
`.load()`/`.load_all()` raises `ConfigTypeError` instead of being silently forwarded to the
backend. Assigning `loader.confine_to = …` afterwards **is** honoured (it is a property
that re-resolves its roots). Notable ones:

- `base_dir` — a `str` or any `os.PathLike` (a stdlib `pathlib.Path` and a pathlib-next
  path both qualify): the directory relative file-path sources you pass to `.load()` resolve
  against; also the anchor for includes inside documents that are not files (`loads()`,
  `#!` strings, streams, command output). A relative `!include` inside a YAML **file**
  resolves against that file's own directory instead.
- `recursive` — whether glob sources (`**/*.yaml`) recurse into subdirectories.
  Default `False`. Forwarded to `parse_sources` by both `.load()` (which also honors a
  per-call `recursive=`) and `.load_all()` (instance setting only — it has no per-call
  `recursive` parameter).
- `bound_loops` — bound a `**` that crosses a **Windows junction loop** (a junction
  pointing at one of its own ancestors), which otherwise walks until the filesystem
  refuses the path and the load raises `OSError`. Default `False`; instance-wide, with
  **no** per-call override, since a loop is a property of the tree. Also drops a directory
  deliberately reachable under two names, and does nothing on POSIX (a directory symlink
  is never descended) — see `parse_sources` below.
- `key_factory` — `(path, value) -> str` merge/document key (default: filename stem).
  The callable receives what `parse_sources` yielded: a path object, or — for a command
  URI — the `CommandSource` carrying the command text. As a string it is a `Path`
  attribute name, or `"%<jinja-expr>"` for a template; **both string forms work at
  construction as well as per call**.
- `merge` — a `ConfigLoaderMergeMethod` member, **its name as a case-insensitive
  string** (`merge="deep"`, resolved through the enum's `_missing_`), or any
  `Merge`-compatible callable; applied between successive sources, left-to-right.
  A type checker reads a restated enum declaration under `TYPE_CHECKING` — the runtime
  class comes from `MergeMethod.extend`, which is typed `type[IntEnum]` and therefore
  showed no members — and a test pins the two against each other. Note for mypy:
  `ConfigLoaderMergeMethod("hash")` (by value) is not accepted statically; use
  `merge="hash"` or `ConfigLoaderMergeMethod["Hash"]`.
- `ignore_error` — `bool` (ignore/re-raise every failure uniformly) or a predicate
  `(error, *, phase, path, loader, **extra) -> bool`. **Every offer passes those three
  keywords**, so one predicate works in every phase: `phase` is `"load"`, `"include"`,
  `"merge"`, `"interpolate"` or `"glob"` (a directory that could not be listed while
  expanding a pattern — `path` is that directory, and skipping it still loads the rest of
  the pattern); `path` is the source or `None`; extras are `key=` + `result=`
  (interpolate in `load`), `key=` + `value=` + `source=` (interpolate in `load_all`) and
  `value=` (a `load_all` load failure). An **interpolation** failure is offered once per
  failing value, with `key=` its path, and a skip leaves that one value as its template
  text — the document around it still renders. A failure inside an include is offered **once
  per level** — the included file as `"load"`, then each including file as `"include"`,
  the same exception object each time. Every offer logs at DEBUG; a skip under the **bool**
  form also logs at WARNING naming source, phase and error **type** (never the message
  text, which can quote config), while a predicate-approved skip stays at DEBUG with the
  traceback. A missing-Jinja2 `ImportError` is raised before the source loop and never
  offered (see below).
- `allow_commands=False` — a command source anywhere in the load, including through a
  nested `!include`, raises `CommandsDisabledError` instead of executing. Set this when
  loading configuration you don't fully trust. A per-call value reaches nested includes
  too. Does **not** restrict a `CommandBackend` constructed and called outside a load.
- `confine_to=` — every **local file** read must resolve inside one of the given roots,
  or `ConfinementError` is raised before the file is opened. This is what stops an
  untrusted document reading arbitrary files through `!include '/etc/shadow'` or
  `../../secret`. Forms: a sequence of paths (inside **any** is allowed), one string split
  on `os.pathsep` like `PATH`, `True` meaning `base_dir` (read at check time, so a later
  `base_dir =` is honoured), or `False`/`None` for off. `YACONFIGLIB_CONFINE_TO` is read
  **only** when the argument is `None` — an env var that could widen an in-code allowlist
  would be an escalation for whoever sets the environment. An **unset** variable means no
  confinement; an **empty** one, like `confine_to=[]`, is an empty allowlist and refuses
  every local read. The comparison is `os.path.commonpath` over
  `normcase(abspath(...))` with any `\\?\` prefix stripped — never a string prefix
  test, which would accept `/srv/confidential` under the root `/srv/conf` — and
  **symlinks are deliberately not resolved** (a link inside a root was placed there by
  whoever administers the root; the threat closed here is a hostile document, not a
  hostile root). Applies to **every** file source including a top-level one, so
  `confine_to=True` also refuses the caller's own absolute path outside `base_dir`. An
  open **stream** is checked by its `name` when that names a real file (a nameless one —
  `<stdin>`, a descriptor, `StringIO` — stays exempt, having no location), and an
  `!include` inside a **command's output** is checked as well: `CommandBackend` hands the
  resolved roots to the loader that parses that output, because `loads()` builds a fresh
  `ConfigLoader` and confinement — unlike the trust policy — does not ride a `ContextVar`.
  Command sources themselves (`allow_commands` governs those) and in-memory `#!` documents
  are exempt **by type**. It is an **instance** setting: a `confine_to=` passed to
  `.load()`/`.load_all()` raises `ConfigTypeError` (the one keyword this library refuses
  rather than ignoring — silence is the wrong answer for a security control), while
  assigning the attribute re-resolves the roots.
- **Jinja2 is required** by `interpolate=True`, `transform=` and a `%` `key_factory`.
  If it is missing or unimportable, each raises `ImportError` naming `yaconfiglib[jinja2]`
  and the original import error, **before** the source loop and therefore before
  `ignore_error` sees anything. (It used to be `AttributeError: 'NoneType' object has
  no attribute 'eval'`, or a silent `None` under `ignore_error=True`.) A call that uses
  none of the three never touches Jinja2.
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
  merge_options=None, allow_commands=None, sandbox=None, **reader_args) -> Any`** —
  resolve `*pathname` via `parse_sources` (globs, nested lists, in-memory `#!`-marked
  strings, open file objects — anything with `read()`, parsed by the backend their file
  name selects — command URIs), parse each with its backend, and merge in order.
  `pathname` empty → loads one empty in-memory document. `flatten=True` flattens the
  final mapping-of-mappings or sequence-of-sequences by one level (error if the result
  is neither); an empty (`None`) member is skipped, and any other non-mergeable member
  raises `TypeError` naming it. `transform` is a Jinja2 expression evaluated per-document (as `value`)
  before merging; it is evaluated sandboxed when `sandbox=True` or
  `allow_commands=False` is in effect. `encoding` also applies to every
  `!include`/`!load` target that names none of its own, at every depth, and to files,
  bytes documents and binary streams; a `str` document or text stream is already text,
  so it is stored in that codec (or UTF-8 when the codec cannot represent it) and read
  back accordingly. With
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
`.get(key: Hashable, default=None, dig=True)` additionally takes a **path**. Order:
exact key wins, then (with `dig`) a path, else `default`.
- A **dotted string** walks mappings by segment; a segment of ASCII digits indexes a
  `list`/`tuple` (`get("servers.0.host")`), but on a mapping it is always the *string*
  key, so `codes.0` never means `0`. Exact-key precedence is **top level only**, so a
  nested key containing a dot is unreachable this way.
- A **tuple** path (`get(("metadata", "labels", "app.kubernetes.io/name"))`) uses each
  segment as given: any hashable mapping key, and on a sequence only a real `int`
  (`bool` excluded, since `True == 1` would index silently). An empty tuple, or
  `dig=False`, returns `default`.
- A `None` or other non-traversable value reached **with segments left** gives `default`;
  the final segment is returned as stored, `None` included, so an explicit `null` stays
  distinguishable from an absent key.
- A missing non-string key returns `default` rather than raising.
The string walk is inlined in `get()` rather than sharing `_dig` with the tuple form:
it is the benchmarked read path and a helper call showed up in it.

`__setattr__`/`__delattr__` **refuse** a name the class defines (`items`, `get`,
`copy`, ...): attribute *reads* of such a name find the method, so allowing the write
would let reads and writes disagree. Use `cfg[name]` for the key, and
`object.__setattr__` for a real instance attribute in a subclass. A missing attribute
raises `AttributeError` naming the actual class, `from None`. `copy()`, `|` and `|=`
keep the class (values stored as given — conversion is construction-only), `|`/`|=`
return `NotImplemented` for a non-dict operand, and `dict(cfg)` gives a plain dict.
`DotAccessibleDict` is exported from `yaconfiglib` and `yaconfiglib.loader`.

Nested mappings are converted **once, at construction** (and once per `load()`, via the
private `_to_dot_access`), never lazily on read. That is what makes item access,
attribute access and object identity agree regardless of read order: `cfg["db"] is
cfg.db`, and a YAML anchor used twice is still one object afterwards. The converter
copies rather than mutates (a backend may own its result), keys an `id()`-memo so shared
structure stays shared and a self-referencing document terminates, and converts every
`dict` subclass plus exact `list`/`tuple` members — a non-dict `Mapping` is left alone,
since materializing something possibly lazy is not this library's call.

**Reads never write.** `get()` follows `dict.get`: a miss returns the default itself and
stores nothing, so `cfg.get("x", {})` no longer inserts `{}` and a `defaultdict` stored
later does not grow keys through a read. Values assigned **after** construction are
stored exactly as given, so a plain dict written with `cfg["x"] = {...}` stays plain.

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
source's merge key; a repeated key replaces the earlier document and logs a WARNING,
  and since the default key is the filename stem, a directory or recursive glob needs a
  `key_factory`). These three require `configloaderkey=` on every call — only
`ConfigLoader.load()` supplies it; calling them directly needs it passed explicitly.
Members pickle (so a strategy can be handed to a worker process), and `Last`/`List`/`Hash`
keep working on an enum built with `ConfigLoaderMergeMethod.extend(...)`: the seed value
comes from a `_init_<name>` hook looked up by member name, not from an identity check.

`is_scalar(obj) -> bool` (one of `int`/`str`/`bool`/`float`/`None`/`bytes`; unchanged, and
**not** the merge leaf rule), `is_array(obj, mutable=False) -> bool` (sequence but not a
mapping/str/bytes; `mutable=True` also requires `MutableSequence`) — used throughout to
distinguish merge branches.

## Typed merge (`utils/typing_merge.py`)

- **`typed_merge(cls, *objects, init=True)`** — three overloads, because `cls` is not
  always a class: a **class** hint returns `T`, a call with **no objects** returns `None`,
  and any other typing form (`Optional[...]`, `Dict[str, int]`, a union) returns `Any`.
  Note the runtime rule the middle overload cannot express: an **empty unpacked
  sequence**, or sources that are all `None`, also return `None`, so a caller with
  possibly-empty layers must check the result.
  Recursively merges `*objects` into
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
  recursive=None, on_error=None, bound_loops=False) -> Iterator[Path]`** — flattens `sources` (paths — a `str`, a
  pathlib-next path, or any other `os.PathLike` such as `pathlib.Path`; glob patterns,
  command URIs — yielded as a `CommandSource`, a `str` subclass carrying the text
  **verbatim** (a path factory would rewrite `/` on Windows and collapse `//`, `/./`
  and trailing slashes on POSIX), with `scheme`/`format`/`command` properties and
  `name`/`stem`/`as_posix()` all answering the full text; only a **string** source can
  be a command; in-memory `"#!<name>\n<content>"` strings (`"#!\n<content>"` for an
  unnamed document, auto-named `mem-N.yaml`), open file objects, or nested
  iterables) into concrete `Path`-like objects.
  **A file object is anything with a callable `read`** — not just an `io` class, so
  `codecs.open()`, a pre-3.11 `SpooledTemporaryFile` and a custom reader qualify (they
  used to be *iterated*, one source per line). It is read once, and its backend follows
  the basename of its `name` when a backend other than `CommandBackend` claims that name;
  otherwise YAML. It materializes to `stream-<n>/<basename>` (so `.name`/`.stem` match a
  by-path load, and so does a `merge="hash"` key) or `stream-<n>.yaml` when unnamed. Its
  content is data: never a list of source paths, never a script however the file is named,
  and a leading `#!` line is content, not a marker. `read()` must return `str` or `bytes`
  (anything else raises `TypeError` naming `read()`). Being materialized, an `!include`
  inside it resolves against `base_dir`. Command URIs (`exec://`, `cmd://`,
  `sh://`, `+fmt` variants) pass through unresolved/unexpanded. In-memory content and
  streams materialize to a `pathlib_next` `MemPath` when available, else a tracked temp
  file (best-effort cleaned at interpreter exit). `memo` dedupes repeat sources across
  recursive calls (mutated in place; logs and skips a duplicate rather than erroring).
  Its keys are **lexical**: `os.path.normcase(os.path.abspath(...))` for a
  `pathlib.PurePath` (so `./x`, `x/../x` and, on Windows, `X` are one file),
  `str(path)` for a `MemPath` or remote path. Never `resolve()` — that would stat
  every source and merge two symlinked names a caller may have meant to keep apart.
  Sources are **classified before any of them loads** (so an unsupported type raises
  up front), which is also what lets a file named explicitly anywhere in the call
  beat a glob match for it while keeping its own position.
  **`encoding` is the codec bytes documents are read with and in-memory text is stored
  in** (UTF-8 by default, on every platform — never the locale codec). In-memory text is
  stored as **bytes**, so a document keeps its own line endings (a `\r\n` block scalar
  loads as it would from a file) and text the codec cannot represent raises
  `UnicodeEncodeError` here (`ConfigLoader.load` stores it as UTF-8 instead and reads it
  back that way). A marker name is whitespace-stripped, so `"#!x.json\r\n..."` names
  `x.json`. A **bytes** document whose codec does not spell `#!` in ASCII (UTF-16/32,
  `utf-8-sig`) is decoded to find its marker; any other bytes document is stored
  byte-for-byte, so a byte-oriented backend reads exactly what was passed. A `bytes`
  source that is not an in-memory document raises `TypeError` — a path belongs in a
  `str` or a path object.
  **Glob expansion is pathlib-next's**, not reimplemented here: a relative pattern is
  expanded by `base_dir.glob(pattern, recursive=...)` so the base stays literal (a
  `proj [v2]` base_dir needs no escaping), and an absolute one by `path.glob(None, ...)`
  (0.9.6+). **`on_error(error, directory) -> bool`** is called when a directory cannot be
  listed: return `True` to skip it (logged at DEBUG) and keep expanding, anything falsy to
  let the `OSError` propagate. Without it the directory is skipped silently, as pathlib
  does. It is asked **once per directory** — pathlib-next reports the same one twice for a
  `**` pattern — and `FileNotFoundError`/`NotADirectoryError` are never offered, since a
  missing or non-directory parent simply matches nothing. It rides on
  `glob(on_error=)`, added in pathlib-next **0.9.7**, which is why the floor is `>=0.9.7`;
  the stdlib fallback has no such hook. What this package adds on top: a source is classified as a pattern by its
  **own** components only (anchor excluded, so `\\?\C:\...` is a literal path); a
  pattern that names an **existing** path loads literally; **directory** matches are
  dropped (no backend reads a directory); and matches are **sorted** by component, since
  `load()` merges in the order it receives and glob promises no order. Dotfiles are
  matched, per pathlib. **`bound_loops=False`** descends any one directory at most once
  per `**` when set, keyed on its `(st_dev, st_ino)` identity: that is what bounds a
  **Windows junction loop** (a junction pointing at one of its own ancestors), which
  otherwise walks until the filesystem refuses the path and the load raises `OSError`. The
  cost, and why it is off by default: identity cannot tell a loop from a directory
  deliberately reachable under two names, so the second name then yields nothing. It is
  forwarded to `glob(bound_loops=)` (pathlib-next 0.9.7+); the stdlib fallback ignores it,
  having no `**` to bound. **Platform split:** only a junction is descended at all —
  Windows reports one as *not* a symlink — while a POSIX directory **symlink** is never
  entered by `**` (`recurse_symlinks=False` upstream, and `True` raises
  `NotImplementedError`), so a symlinked loop cannot occur and a symlinked layer must be
  named as its own source.
- **`has_glob_pattern(path) -> bool`** — whether *path* contains glob magic characters.

## Errors (`errors.py`)

A leaf module (stdlib imports only, like `utils.trust`), so backends import it without a
cycle. **Nothing wraps a parser's error**: a bad YAML file still raises `yaml.YAMLError`
and a missing file still raises `FileNotFoundError` — that is what makes `ignore_error`
predicates and `except` clauses on those types work. Each class below is a `ConfigError`
**and** the builtin exception the same condition raised before, so existing
`except ValueError`/`except NotImplementedError` code keeps matching.

- **`ConfigError(Exception)`** — base for every condition the library itself reports.
- **`ConfigValueError(ConfigError, ValueError)`** — a value or structure it cannot accept:
  an include cycle, a strict interpolation reference cycle, a dotenv strict parse failure,
  an env scalar/nested collision.
- **`ConfigTypeError(ConfigError, TypeError)`** — a value whose type the operation cannot
  use: a `flatten=True` member that is not a mapping/sequence, an unsupported YAML node in
  an `!include` tag.
- **`UnsupportedFormatError(ConfigError, NotImplementedError)`** — no registered backend
  reads this source (`ConfigBackend.get_class_by_path`), or a `.j2` name with no inner
  format extension. Message: `No backend reads <path>`, plus the missing-extra hint, plus
  `; pass loader=<name> (registered: ...)`.
- **`UnknownLoaderError(ConfigError, ValueError)`** — `loader=` named an unregistered
  backend. Message keeps the `Unknown configuration format/loader: <name>` prefix, then
  the hint, then `; registered: ...`.
- **`CommandsDisabledError(ConfigError, ValueError)`** — a command source under
  `allow_commands=False`.
- **`ConfinementError(ConfigError, PermissionError)`** — a local file read resolved
  outside every `confine_to=` root, or the source is not a local file at all while
  confinement is on. Being a `PermissionError` it is an `OSError`, so a CLI catching that
  around a load already handles it, and `load_error_types()` covers it twice over. The
  message names the **resolved** target (absolute, `..`-free) and every root it was
  checked against — a refusal nobody can diagnose gets switched off — while the WARNING
  line an `ignore_error=True` skip emits still carries only source, phase and error type.
  Raised before the file is opened, so a skip is safe: nothing was read.
- **`CommandError(ConfigError, subprocess.CalledProcessError)`** — a command source
  exited non-zero. Stdlib constructor and attributes; its `__str__` is the stdlib text,
  then `stderr: <last 20 non-empty lines, capped at 2000 chars>`, then the context
  suffix. **stdout is never in the message** (it is the payload, often the secret) — it
  stays on `.output`.
- **`CommandTimeoutError(ConfigError, subprocess.TimeoutExpired)`** — `timeout=` elapsed;
  the process tree was killed. Both classes pickle.
- **`load_error_types() -> Tuple[Type[BaseException], ...]`** — one tuple for
  `except yaconfiglib.load_error_types() as error:`. Always `ConfigError`, `OSError`,
  `UnicodeError`, `json.JSONDecodeError`, `configparser.Error`,
  `subprocess.SubprocessError`; adds `yaml.YAMLError`,
  `jinja2.exceptions.TemplateError` and `tomllib`/`tomli` `TOMLDecodeError` for each
  module **already in `sys.modules`** (a parser that was never imported cannot have
  raised, so nothing is imported to find out). Evaluated per call, never frozen at import.
  Bare `ValueError`/`TypeError`/`KeyError` are deliberately excluded, so a library bug
  still crashes.
- **Context, recorded in place.** Every error re-raised through `ConfigLoader` carries
  three attributes, and the rendered text is written into whichever field that error's own
  `__str__` reads — so **the type and identity are unchanged** (a predicate still receives
  the very object raised, of the parser's own type):
  - `config_source: str` — the innermost source. Set once; never overwritten.
  - `config_frames: Tuple[ErrorFrame, ...]` — innermost first.
    **`ErrorFrame(kind, source, line=None)`** is a NamedTuple whose `kind` is
    `"include"`, `"render"`, `"command"` or `"merge"`.
  - `config_key: Tuple[Union[str, int], ...]` — a key or field path. Built from the
    inside out, so it reads relative to the document or the model asked for.
  - `config_model: str` — the model a `typed_merge`/`load_as` failure was building. The
    **outermost** model wins, for the same reason.
  The suffix reads
  `[in <source>; included from <src>, line <n>; rendered from <src>; output of command
  <src!r>; while merging <src>; at <a.b[0].c> (<Model>)]` — the model alone as
  `(<Model>)` when there is no key — and is **re-rendered from the record**
  each time, never appended twice. `in <source>` is omitted when the error's own message
  already names it (a PyYAML mark, an INI source, an `OSError` whose `filename` matches);
  a `render` frame naming the source itself renders as bare `rendered`. An unknown error
  shape gets a PEP 678 note instead (set directly below 3.11, which `add_note` predates).
- Raises left as plain builtins on purpose: a wrong **argument** (`load(None)`,
  `loads(42)`, an unsupported source type, an invalid `ini_interpolation`) and a missing
  dependency (`ImportError` naming `yaconfiglib[jinja2]`) are neither configuration
  content nor a source.

## Backends (`backends/`)

- **`ConfigBackend`** (`base.py`) — the pluggable-backend contract, a **plain base
  class**. It was a `typing.Protocol`, which made `issubclass()` against it raise
  `TypeError` on every interpreter and made type checkers report every backend without
  a `dumps` — including `ConfigLoader` itself — as abstract. Registration was always
  nominal (`type.__subclasses__`), so nothing about dispatch changed; what did change
  is that an object which merely *looks* like a backend is no longer an `isinstance`
  of it. `issubclass`/`isinstance` now work.
  Subclassing and importing the subclass is the entire registration mechanism (no
  registry call needed); lookup walks `__subclasses__(recursive=True)` in
  definition order.
  - Override **`load(self, path, **options) -> Any`** (required). Unrecognized
    `**options` should generally be ignored, not raise — `ConfigLoader` forwards a
    shared option set to every backend it calls.
  - For content it cannot accept, raise the parser's own error or a `ConfigError`
    subclass, so `load_error_types()` covers it.
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
  The parser instance is **named after the file** (`loader_instance.name = str(path)`),
  so every mark reads `in "app.yaml"` instead of `in "<unicode string>"`; a `ReaderError`
  raised inside the constructor (a non-printable byte, before any instance exists) has its
  `name` set and is re-raised. An `!include` form is read by
  `backends/base.py::_include_call`, shared with `ConfigBackend._yaml_tag_constructor`: a
  mapping with no non-empty `pathname`, or an empty sequence, raises PyYAML's
  `ConstructorError` (whose mark names file and line) rather than a bare `KeyError`.
  **Gotcha**: nested `!include`/`!load` route through the driving `ConfigLoader` stashed
  on the loader instance as `_yaconfiglib_config_loader` — not the loader captured when
  the tag was first registered — so each loader's own `allow_commands`/`merge` apply to
  its own nested includes, and `base_dir` applies to includes in non-file documents. The
  resolution anchor (`_yaconfiglib_include_origin`) and the call's encoding
  (`_yaconfiglib_include_encoding`, inherited through `master`) ride on the same
  instance.
  `.dumps(data, dumper_cls=None, **options)` defaults `Dumper` to
  `DEFAULT_DUMPER_CLS` (the private `_PlainMappingDumper`: a `dict` multi-representer plus
  an exact-`tuple` list representer), and defaults `sort_keys=False` and
  `allow_unicode=True` whatever dumper is used. `encoding=` raises `TypeError`.
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
  a `[`/`{`-leading value, else leaves it a string. With `nested_delimiter`, a variable
  that is both a value and a parent of nested keys (`APP_DB` plus `APP_DB__PORT`) raises
  `ValueError` naming both variables — it used to depend on `os.environ` order, giving a
  different document for the same environment. Leaf-ness is decided by **key path**, not
  value type, so a `coerce=True` JSON object is still a leaf. A variable equal to the
  prefix is skipped instead of producing a `""` key. Prefix matching is
  case-insensitive on Windows, switched by the module flag `_ENV_KEYS_CASE_INSENSITIVE`
  (`os.name == "nt"`) so tests can exercise both modes anywhere. Collision bookkeeping is
  skipped entirely when `nested_delimiter` is unset — every variable is then a leaf, and
  that is the hot path.
- **`CommandBackend`** (`NAME="command"`, `PATHNAME_REGEX` = script extensions
  `.sh`/`.bat`/`.ps1`/`.cmd` **only**) — claims a `CommandSource` by type, and a bare
  string by scheme for a direct caller; a *path* is claimed by extension alone, so a
  file named `sh:hosts.json` is never run. Runs `cmd://`/`exec://`/`sh://` (and `+fmt`
  variants, e.g. `cmd+json://...`) sources as a subprocess and parses stdout, routing by
  the `+fmt` suffix or a `#!fmt` shebang line in the output. The command runs with stdin
  closed. A **script file** is launched with `shell=False` through its interpreter,
  so its own name is never shell syntax: `.bat`/`.cmd` via `%COMSPEC% /d /v:off /s /c`
  (Windows only; a `%` in the path is refused, since cmd expands `%VAR%` inside
  quotes), `.ps1` via `pwsh`/`powershell -NoProfile -NonInteractive -File` (execution
  policy honoured, never overridden), `.sh` via `sh` on Windows and directly on POSIX
  when executable with a `#!` line else under `/bin/sh`. The path is made absolute
  first (no `PATH` search, and immune to `NoDefaultCurrentDirectoryInExePath`). An
  **in-memory** source (a `#!name` document or a rendered `.j2`) must carry a script
  extension and is written to a private temp directory, run, and removed — so the
  promised body runs rather than a same-named file on disk. `.load(..., timeout=None)`: an opt-in number of seconds (reachable per call,
  e.g. `loader.load("cmd://...", timeout=30)`) after which the command and its child
  processes are killed and `CommandTimeoutError` (a `subprocess.TimeoutExpired`) is
  raised; no timeout by default.
  Stdout is captured as **bytes** and decoded with `encoding=` (default `utf-8`)
  **strictly**: an undecodable byte raises `ConfigValueError` (a `ValueError`) naming
  `encoding=`, instead of substituting U+FFFD (`Popen(errors="replace")` would swallow it
  before this layer could object). CR/CRLF are then normalized. A **non-zero exit wins**:
  its output is decoded with `errors="replace"` and attached to `CommandError` (a
  `CalledProcessError`), since that text is a diagnostic rather than configuration; the
  message carries the **stderr tail**, never stdout. stderr from a *successful* run is
  logged at DEBUG. The codec used to decode stdout is **also** the codec the output
  document and that document's own `!include` targets are parsed with: the decoded text is
  handed to `loads()` with `encoding=` set, from where plan-2's carrier takes it to every
  depth. `master` is stripped alongside `origin` — it is the *including* document's
  parser, so passing it on would share that document's anchors and let its inherited
  encoding override the codec this output was decoded with.
  Every `ValueError` this backend raises is a `ConfigValueError`; the
  missing-interpreter `FileNotFoundError` stays a plain `OSError`, since it is about the
  host rather than the configuration.
  With no `format=`/`+fmt`/shebang it **sniffs**: json (any value), yaml **only for a
  mapping or list**, toml, dotenv **strict** (every non-comment line an assignment), ini,
  else the raw stdout string. The yaml and dotenv restrictions are what make the later
  candidates reachable — YAML turns any text into a scalar and lenient dotenv turns a
  word into a bare key — so a candidate's result must be *checked*, not just produced.
  Explicit `format=`, `+fmt` and shebang routes keep lenient dotenv and propagate a
  single candidate's parse error, with an `ErrorFrame("command", ...)` added.
  **Only a parse failure means "try the next format"**: the loop catches
  `(ValueError, configparser.Error, RecursionError)` plus `yaml.YAMLError` when PyYAML is
  imported — the measured failure set — and within that re-raises a
  `CommandsDisabledError`, anything carrying an `include` frame (raised by a document the
  output included, not by parsing it) and, outside sniffing, an `UnknownLoaderError`.
  Everything else never enters the handler. Sniffing used to swallow every exception, so
  a missing `!include` in the output became an empty mapping. Several requested formats
  that all fail raise `ConfigValueError` naming the command and each format's first line
  (capped at 200 characters), chained `from` the last parser error.
- **`PythonBackend`** (`NAME="python"`) — passes an in-memory Python object straight
  through as the parsed document. Use it **on its own** —
  `loader.load(loader=PythonBackend(data))`, no pathname — and merge the result with the
  file loads afterwards. A backend instance is not a valid *source*, and `loader=` applies
  to every source in a call, so pairing it with a file either raises or silently discards
  the file.
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
  (`config.yaml.j2`); otherwise `UnsupportedFormatError` (a `NotImplementedError`) names
  the template, raised before it is read or rendered.
  Errors name the template: the compile call passes `name=path.name` and
  `filename=str(path)`, so a template error carries `.filename` and its traceback frame
  names the file (it said `File "<unknown>"`), and a parse error in the **rendered**
  document — whose line numbers refer to the rendered text — gains a `render` frame
  naming the template.

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
  **A raised error removes nothing**: each container is rendered into a staging list and
  written back only once every member succeeded, so the failing container keeps all its
  original entries (it used to `pop` each key before rendering, losing that key and, one
  frame up, its whole section). The error carries `config_key` — the path to the value
  that failed, innermost wins — which is also rendered into its message. DEBUG records
  name the key and the **template**, never the rendered value: a template holding
  `{{ env.DB_PASSWORD }}` is not the secret, its result is.
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
