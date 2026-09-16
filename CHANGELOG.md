# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- `!include`/`!load` are no longer registered on the shared `yaml.SafeLoader`.
  Previously, after any yaconfiglib YAML load, every `yaml.safe_load()` call in the
  same process resolved `!include`, so an untrusted document parsed by unrelated
  code could run `cmd://` commands or read local files. The tags are now registered
  on a private subclass, and only documents loaded through a `ConfigLoader` resolve
  them. Code that relied on `yaml.safe_load` resolving `!include` after a
  yaconfiglib load must load through a `ConfigLoader` instead.
- An untrusted document can no longer weaken the caller's settings through an
  `!include` mapping. Previously a mapping could set `allow_commands: true` or
  `sandbox: false` for the included file, run arbitrary Python through a
  `transform` or `%`-form `key_factory` expression, or call a method on the
  included path through a plain `key_factory` (`key_factory: unlink` deleted the
  file).
- A per-call `load(..., allow_commands=False)` or `load(..., sandbox=True)` now
  also applies to nested `!include` targets. Previously the included files were
  loaded with the instance settings.
- `.j2`/`.jinja2` sources now render in Jinja2's sandbox whenever `sandbox=True` or
  `allow_commands=False` is in effect, including ones reached through `!include`
  (an in-memory `#!name.yaml.j2` document included the same way). Previously
  they always rendered in the non-sandboxed default environment, so a template
  could run arbitrary Python even under both controls. A rendered document that
  is a command source is refused while `allow_commands=False`.

### Added
- `ini_interpolation` (per call) and `IniConfig(interpolation=...)` choose how `%` is
  handled in an INI file: `"basic"` (the default, unchanged), `"extended"` for
  `${section:key}` references, or `None` to read values verbatim. A logging or alembic
  formatter value such as `%(levelname)-5.5s [%(name)s] %(message)s` could not be loaded
  at all before — it raised `InterpolationSyntaxError` with no way to opt out.
- `DotenvBackend(strict=True)` and the per-call `dotenv_strict=True` make an unparseable
  `.env` line, or a file with no assignment at all, raise `ValueError`.
- `yaconfiglib.load_as(model_cls, *sources, **options)`: the top-level form the README
  and the model guide already showed. It takes several sources, and routes keywords like
  `yaconfiglib.load()`, so `strict`/`base_dir`/`merge` configure the loader.
- `YamlConfig.load(origin=...)`: the document that relative `!include`/`!load` paths
  resolve against. Defaults to the file being parsed; a rendered `.j2` template passes
  its own path.
- `timeout=` for command sources, e.g. `loader.load("cmd://...", timeout=30)`: after
  that many seconds the command and its child processes are killed and
  `subprocess.TimeoutExpired` is raised. There is still no timeout by default.
- `ConfigLoader.load_all()` accepts `sandbox=` and `allow_commands=` per call; they
  also apply to nested `!include` targets.

### Changed
- On Python 3.9/3.10 the `toml` extra now installs `tomli`, the backport of the standard
  library `tomllib`, instead of the unmaintained `toml` package — which is no longer used
  even when it is installed. TOML 1.0 files therefore parse on 3.9/3.10 exactly as they
  do on 3.11+: mixed-type arrays load instead of raising, a lowercase `z` datetime keeps
  its UTC offset instead of coming back naive, and offset datetimes can be pickled.
  Reinstall with `pip install "yaconfiglib[toml]"` on 3.9/3.10; on 3.11+ the extra
  installs nothing. Note that `tomli` 2.4+ accepts some TOML 1.1 syntax that 3.11-3.14's
  `tomllib` rejects, so a TOML 1.1-only file is not portable across interpreters.
- `.env` double-quoted values decode `\n`, `\r`, `\t`, `\"` and `\\`, so a value that
  needs a literal backslash (a Windows path) belongs in single quotes, which stay raw.
- An unterminated quoted `.env` value raises `ValueError` instead of silently swallowing
  the rest of the file.
- A quoted `.env` value ends at its first matching quote, and only whitespace or a `#`
  comment may follow it. `KEY='it's here'` and `KEY="say "hi""` used to load whole and
  are now skipped with a warning — write `KEY="it's here"` or escape the inner quotes.
- In an unquoted `.env` value quotes are ordinary characters, so `KEY=a "b # c"` gives
  `a "b`. Quote the whole value to keep the `#`.
- A `.env` line that cannot be parsed is skipped with a logged warning rather than in
  silence.
- `typed_merge` coerces mapping **keys** through a `Dict[K, V]` hint's key type, so
  `Dict[int, str]` can be satisfied by JSON, TOML, INI or env sources, which only
  produce string keys. Keys that differ only by type (`"80"` and `80`) now merge into
  one entry.
- `typed_merge` into a `TypedNamespace` subclass assembles the result without calling
  `__init__`, to avoid re-parsing values. If a subclass's `__init__` did more than apply
  `_parse_<field>` hooks, move that work into a hook or into `__merge__`.
- `typed_merge` parses string booleans for a `bool` hint: `true/yes/on/1` and
  `false/no/off/0` (stripped, case-insensitive). Any other string now raises
  `ValueError`, where every non-empty string used to become `True` — including
  `"false"`. Use one of the listed words.
- `typed_merge` raises `TypeError` when a sequence hint is given a string, a mapping or
  a non-iterable, instead of splitting it into characters or keys. Pass a list.
- `typed_merge` raises `TypeError` when a `Tuple[...]` hint's length does not match the
  value's, instead of dropping the extra items.
- `typed_merge`: a `None` source no longer overrides a value from an earlier source. To
  clear a field, pass an explicit empty value (`""`, `[]`, `{}`) instead of `None`.
- `typed_merge`: in a multi-member union, a value that is already an instance of one of
  the members is kept as it is instead of being coerced through the first member —
  `Union[int, str]` fed `"8080"` now returns `"8080"`, and `Union[str, int]` fed `5`
  returns `5`. To force a coercion, narrow the hint to the one type you want (`int`, not
  `Union[int, str]`); reordering the union members does not restore the old result.
- `is_array()` returns `False` for `bytearray` and `memoryview`, matching `bytes`. Binary
  buffers are values, so merging replaces them instead of combining them byte-wise.
- `load_as` builds dataclass fields that are annotated as a dataclass or Pydantic model
  (including `Optional[...]` of one) as instances instead of leaving them as dicts. Code
  that indexed such a field (`cfg.db["host"]`) must use attributes (`cfg.db.host`).
  Containers of models (`List[Model]`) are still left as loaded.
- Options that `ConfigLoader` itself accepts (`merge`, `encoding`, `recursive`,
  `interpolate`, ...) passed to `yaconfiglib.load()`/`loads()` now configure the loader,
  so they also apply to files pulled in with `!include` — a per-call `merge="deep"` now
  deep-merges an included glob, as the same option on `ConfigLoader(...)` always did. A
  keyword that no backend reads is ignored rather than rejected, so check the spelling
  of an option that seems to have no effect.
- With `interpolate=True`, a bare `{{ expr }}` keeps the expression's type only when
  nothing but a trailing newline surrounds it; with leading or trailing spaces the value
  renders as a string. A YAML `|`/`>` block scalar still yields the expression's type.
- With `strict=True`, a reference cycle between two top-level keys raises
  `ValueError: interpolation reference cycle: a -> b -> a` instead of rendering each
  value once against the other's current text.
- An included file's templates now render in the merged document, so a reference to one
  of that file's own keys must go through the key it was included under
  (`{{ svc.name }}` rather than `{{ name }}`).
- A relative `!include`/`!load` path is now resolved relative to the including file —
  that file's own directory, at every depth, `.yaml.j2` templates included. Previously it
  resolved against the loader's `base_dir` (by default the working directory), so a
  file in a subdirectory could not name its neighbours, and an unrelated file with the
  same name could be loaded instead. Documents that are not files (`loads()`, `#!`
  strings, streams, command output) still resolve their includes against `base_dir`,
  and so do the sources you pass to `load()` yourself. An included source's `pathname`,
  as seen by `transform` and `key_factory`, is now absolute. Include paths written
  relative to `base_dir` from a file in a subdirectory must be rewritten relative to
  that file, or made absolute.
- The `!include` mapping form accepts only `pathname`, `encoding`, `transform`,
  `key_factory`, `default`, `flatten`, `merge`, `merge_options` and `recursive`;
  other keys are ignored and logged at `WARNING`. `key_factory` there must use the
  `"%<expr>"` form.
- `transform` and `%`-form `key_factory` expressions are evaluated in Jinja2's
  sandboxed environment whenever `sandbox=True` or `allow_commands=False` is in
  effect.
- `CommandsDisabledError` is now defined in `yaconfiglib.utils.trust`; importing it
  from `yaconfiglib` or `yaconfiglib.loader` keeps working.
- With `strict=True`, an undefined variable in a `.j2`/`.jinja2` source now raises
  `UndefinedError` instead of rendering as an empty string.
- Passing a non-sandboxed `environment=` for a `.j2`/`.jinja2` source while
  `sandbox=True` or `allow_commands=False` is in effect raises `ValueError`; pass a
  `jinja2.sandbox.SandboxedEnvironment` instead.
- With `inject_env=True`, templates receive a read-only snapshot of `os.environ`
  as `env`. Previously `env` was the live `os.environ`, so a template could change
  or delete process environment variables, including ones later command sources
  saw.

### Deprecated
- `ConfigLoader(log_level=...)` (and the same keyword through `yaconfiglib.load()`) has
  no effect and now warns. It has not changed logging since 0.11.0, when setting the
  level on a shared logger was removed. Configure the `yaconfiglib` logger with the
  `logging` module instead.

### Removed
- `yaconfiglib.utils.getLogger` and the `yaconfiglib.utils.Logger` re-export, neither
  documented nor used anywhere. Use `logging.getLogger` and `logging.Logger`.

### Fixed
- When a format's optional dependency is missing, the "Not reader for ..." and "Unknown
  configuration format/loader" errors name the extra to install and the underlying import
  error, instead of only reporting an unknown format.
- `.cfg` files are detected as INI, which the API reference already stated. A `.cfg`
  file previously raised `NotImplementedError`, so a glob loaded with
  `ignore_error=True` skipped it silently and now merges it in.
- An INI file containing only `[DEFAULT]` logs a warning explaining that those keys are
  inherited by other sections rather than returned, and how to load them as a section.
  It used to load as an empty mapping, indistinguishable from an empty file.
- Command output with no `format=`, `+fmt` or `#!fmt` shebang is sniffed as documented.
  INI output keeps its sections, where it used to be flattened as dotenv and lose keys;
  `KEY=value` output is parsed as dotenv, where it used to come back as one folded
  string; and output no format accepts comes back as the raw stdout string, where it used
  to be an empty mapping. While sniffing, YAML is accepted only for a mapping or a list,
  so output that only YAML read as a scalar — a bare date such as `2026-09-15`, `yes` or
  `~` — now comes back as the raw string; use `cmd+yaml://` to force a YAML scalar.
- A multi-line quoted `.env` value loads whole. A PEM key or any other value spanning
  several lines was truncated at the first line break.
- An apostrophe in an unquoted `.env` value no longer disables comment stripping, so
  `KEY=it's here # comment` gives `it's here`.
- `.env` keys containing `.` or `-` are kept instead of silently dropping the line.
- A `.env` value containing a form feed, vertical tab, `\x1c`-`\x1e`, U+0085, U+2028 or
  U+2029 is no longer cut at that character: only a newline ends an entry. A
  Windows-1252 file read with `encoding="latin-1"` therefore keeps such a value whole —
  though byte `0x85` then arrives as U+0085, so read those files with
  `encoding="cp1252"`.
- JSON, TOML, INI and `.env` files saved with a UTF-8 byte-order mark now load. The
  mark previously dropped the first `.env` variable, prefixed the first TOML key with an
  invisible character, and made JSON and INI raise.
- `TomlConfig`, `JsonConfig`, `IniConfig` and `Jinja2ConfigLoader` accept a `str` path
  and an omitted `encoding`, so a backend instance can be registered directly as a
  PyYAML tag constructor (`!toml`, `!json`, `!ini`), which is how the documentation says
  to use them. They also accept `path_factory=`, like the other file backends.
- Files whose name ends in another format's suffix after `.env` are parsed by that
  format's backend: `app.env.yaml` as YAML, `settings.env.json` as JSON, `x.env.toml` as
  TOML, `x.env.ini` as INI, and `.env.j2` or `config.env.yaml.j2` rendered as a template
  first. They previously loaded as dotenv, which meant an empty or flattened result.
  `.env`, `*.env` and staged names such as `.env.local` and `.env.development.local` are
  still dotenv, and `loader="dotenv"` reads any name as dotenv.
- `typed_merge` builds a mapping target positionally, so **non-string keys** no longer
  fail with "keywords must be strings", an abstract `Mapping[...]` hint no longer raises
  "Can't instantiate abstract class", and a `defaultdict` target keeps its
  `default_factory` instead of losing it.
- `typed_merge` leaves a dataclass's `field(init=False)` names out of the constructor
  call, where passing them raised "unexpected keyword argument".
- `typed_merge` applies a `_parse_<field>` hook exactly once per value. A
  `TypedNamespace` source was parsed again while being collected and a third time by the
  target's constructor, so any parser that is not idempotent — the documented
  comma-splitting example included — failed or corrupted the value.
- `typed_merge` handles the sequence hints that broke in 0.11.1, when the element
  coercion branch first became reachable: abstract `Sequence[...]`/`MutableSequence[...]`
  hints (which raised "Can't instantiate abstract class"), `NamedTuple` hints (rebuilt
  through their field types instead of being handed a generator), `range`, and lists
  holding either.
- `typed_merge` coerces a heterogeneous `Tuple[int, str]` hint by position instead of
  applying the first type argument to every element.
- `typed_merge` skips `None` sources at every level instead of merging them: an
  `Optional[...]` field set to `None` by a later source no longer crashes
  (`vars(None)`) or replaces the earlier value with `None`, `'None'` or `False`.
- `typed_merge` honours a `typing.Any` hint on Python 3.11+, where `Any` became a class
  and reached the coercion path (`isinstance() with typing.Any`). Directly, as a type
  argument, or as a union member, `Any` takes the last object unchanged.
- `typed_merge` strips `Annotated[X, ...]` and coerces through `X`; the annotation used
  to be returned uncoerced.
- `typed_merge` resolves type hints one field at a time when the whole set cannot be
  resolved, so a single unresolvable forward reference no longer leaves every sibling
  field uncoerced.
- `OpaqueMerge`, `opaque` and any `__merge__` classmethod are honoured through
  `Optional[...]`, `Union[...]` and parameterized generics, not only on a direct class
  hint.
- A `Union` hint whose first member is `None` (`Union[None, int]`) no longer merges
  through `NoneType` and fails.
- An error raised inside a custom merge strategy's `init()` propagates instead of being
  swallowed as "this strategy has no `init`", which silently skipped the hook.
- `List` and `Hash` work on an enum built with `ConfigLoaderMergeMethod.extend(...)`.
  The seed value is now chosen by member name, where an identity check against the
  built-in enum failed for every extension (`List` raised `AttributeError`, `Hash`
  returned an unkeyed document).
- `ConfigLoaderMergeMethod` members can be pickled — for example passed to a worker
  process. The generated enum now records the module it is bound in, so `pickle` can
  find it again.
- `load(default=...)` is documented as what the code does: it is returned only when no
  source loads, and is never merged into a loaded document.
- `Deep` list extension no longer treats `True`/`False` as duplicates of `1`/`0`, or
  `1.0` as a duplicate of `1`: an item is already present only if an existing one has the
  same type and compares equal. It also keeps the new source's order when that source
  mixes mappings with other items; mappings used to be moved to the end of the list.
- `Simple`, the default strategy, replaces a list with the later list when two top-level
  list documents (or two lists passed directly) merge, as the strategy table, the module
  docstring and the API header all said. It used to replace positionally and keep the
  earlier list's extra tail, so `[a, b, c]` overridden by `[x]` gave `[x, b, c]`. Lists
  under a mapping key already replaced and are unchanged. Write the full list in the
  override if you relied on the old result.
- `Deep` and `Substitute` no longer raise `TypeError` on ordinary configuration values
  they had no rule for — dates, datetimes, times, `Decimal`, sets, enum members, objects
  a backend produced. Two YAML files with an unquoted `release: 2024-01-01` used to fail
  the whole load, and with `ignore_error=True` the entire override file was discarded
  instead.
- `Deep` list extension keeps items of those types. `holidays: [2024-12-25]` overridden
  by `holidays: [2025-12-25]` now holds both dates; the override used to be dropped.
- A type-changing override replaces instead of raising: a mapping overridden by a number,
  a string by a list, a list by a mapping. A mapping overridden by a list that is not a
  non-empty list of mappings becomes that list, so `section: []` clears it (it used to
  keep the mapping, or raise for `[42]`). A non-empty list of mappings still folds into
  the mapping.
- Overriding a key under a YAML anchor or a `<<:` merge key no longer rewrites the
  sibling keys that share it. `Deep` and `Substitute` merged into their left-hand
  argument, so an override for one environment silently changed every environment that
  shared the mapping. The strategies are now copy-on-write and never modify their
  arguments — use the return value, as `ConfigLoader` always has.
- A self-referencing mapping now merges instead of raising `RecursionError`, and a node
  aliased in both inputs stays one object in the result.
- `Simple` no longer raises `TypeError` when merging into a read-only mapping whose keys
  overlap the override.
- `ConfigLoader(log_level=<any int>)` no longer raises `ValueError` for a value that is
  not one of the `LogLevel` members.
- `flatten=True` skips empty documents instead of failing inside a comprehension — a
  `conf.d` glob may hold an empty file — and reports any other member it cannot flatten
  with a `TypeError` naming that member.
- `yaconfiglib.dumps()`/`dump()` write a loaded configuration as a plain YAML mapping
  that `yaconfiglib.load()` and other YAML tools read back. Previously the result
  carried a `!!python/object/new:...DotAccessibleDict` tag that the library's own safe
  loader refused. Passing your own `Dumper=`/`dumper_cls=` keeps PyYAML's default
  handling.
- `load_as` no longer fails on a document key named `self`, keeps `InitVar` values, and
  no longer imports pydantic when it is not already imported (it probes `sys.modules`
  instead, which is exact: a class can only subclass `BaseModel` if pydantic is loaded).
- `yaconfiglib.load()` and `loads()` pass backend options through instead of raising
  `TypeError`: `json_decoder_options`, `ini_default_section`, `environment=` and any
  other option a backend reads now reach it. They used to be rejected because the
  helpers matched keywords against a hand-written list.
- `env` is available when rendering `.j2`/`.jinja2` sources with `inject_env=True`,
  as the templating guide's example shows. Previously it raised
  `UndefinedError: 'env' is undefined`.
- `utils.jinja2.compile()` and `eval()` honour `globals=` on every call. The first
  call's globals were baked into the cached template, so a later call with different
  globals got the first call's values.
- The compiled-template caches are safe to use from several threads: a lookup can no
  longer fail with `KeyError` when another thread evicts an entry at the same moment.
- A `.j2`/`.jinja2` file with no format extension in front of the suffix (`config.j2`)
  now raises `NotImplementedError` naming the template and the expected
  `name.<format>.j2` form, before the file is read. The error used to name a stripped
  path that does not exist, or an unrelated temporary file.
- Values pulled in with `!include`/`!load` are now rendered together with the document
  that includes them when `interpolate=True`: an included file's templates can refer to
  the including document's keys, and an escaped literal such as `{{ '{{ x }}' }}` in an
  included file is no longer rendered a second time. References between templated values
  resolve fully in any order (`logs: "{{ base }}/logs"`, `err: "{{ logs }}/err"`);
  previously a literal `{{ base }}` could survive into the result.
- An `encoding=` passed to `ConfigLoader.load()`, `ConfigLoader.load_all()` or
  `yaconfiglib.load()` now applies, at every depth, to the files, templates and
  commands pulled in with `!include`/`!load` that do not set their own `encoding`.
  Previously those targets were read with the loader's default, which raised
  `UnicodeDecodeError` or silently garbled non-ASCII text; the same happened to
  includes inside an included command's output under `ConfigLoader(encoding=...)`.
  An include whose real encoding differs from the one passed needs its own
  `encoding:` in the mapping form.
- Interpolating YAML that reuses anchors (`&name`/`*name`) no longer slows down
  exponentially with nesting depth: each shared node is interpolated once, and
  every alias refers to the same result. A seven-level document that took about
  4 seconds now loads in well under a tenth of a second.
- Command sources run with stdin closed. Previously a command that read stdin hung
  the load or consumed the calling process's input.
- An include cycle (`a.yaml` including `b.yaml` including `a.yaml`) now raises
  `ValueError: include cycle: ...` naming the chain. Previously it recursed until
  `RecursionError`, or with `ignore_error=True` returned a deeply nested partial
  result.

### Documentation
- The security guide now states that `!include` can read any local file the
  process can (absolute paths and `..` are not confined to `base_dir`), even with
  `allow_commands=False` and `sandbox=True`: those controls stop code execution and
  template injection, not file disclosure. Do not return or log an untrusted
  config verbatim.
- The security guide no longer claims the `python` backend executes Python; it
  passes a caller-supplied object through unchanged.

## [0.11.2] - 2026-08-16

### Changed
- Bounded the `pathlib-next` dependency to `>=0.9.0,<0.10` (previously
  unversioned). It carried no bounds at all, so a resolver was free to install a
  release predating the `Pathname`/`MemPath` surface that `backends/` and
  `utils/source.py` import, or a future minor that changes it. Nothing in the
  package uses API added after 0.9.0, so the floor sits at the start of the
  series rather than at its newest patch.

## [0.11.1] - 2026-08-16

### Fixed
- **`sandbox=True` no longer breaks bare `{{ expr }}` values.** The
  type-preserving expression path captured its result with
  `_meta.__setitem__(...)`, an underscore attribute access that Jinja2's
  `SandboxedEnvironment` refuses — so every pure-expression config value raised
  `SecurityError` under the 0.11.0 `sandbox` control, while mixed-text templates
  (`"hello {{ name }}"`) worked. The capture is now a plain callable bound as a
  render name; SSTI payloads still raise `SecurityError` as before.
- **The `recursive` option now actually recurses.** `ConfigLoader(recursive=True)`
  and the per-call `load(recursive=...)` override were documented but never
  forwarded to `parse_sources`, making both silent no-ops; `load_all()` now
  honors the instance setting too. Default (`False`) glob behavior is unchanged.
- **Deep merge with `mergelists=True` no longer drops non-overlapping dicts.** A
  positionally-matched dict from the right-hand list that shared no key with its
  counterpart was removed from the pending set before the overlap check, so it
  was neither merged nor appended — `Deep([{"k": 1}], [{"z": 9}], mergelists=True)`
  returned `[{'k': 1}]` instead of `[{'k': 1}, {'z': 9}]`. Silent data loss.
- **`typed_merge` now actually reads parametrized-generic arguments.** Three
  defects shared one root cause — type args were taken from the original `cls`
  instead of the union-unwrapped origin, and the sequence branch tested a class
  object with instance checks so it could never run. `Dict[str, int]` coerced
  nothing (`{"a": "1"}` stayed a string), `List[str]` returned its elements
  uncoerced, and `Optional[Dict[str, int]]` picked `NoneType` as the value type
  and raised `TypeError: NoneType takes no arguments`. Unparametrized `dict`/
  `list` hints and `str`/`bytes` are unaffected.
- **The Jinja2 backend no longer crashes when `pathlib_next` is absent.** Its
  ImportError fallback set `MemPath = None` and `load()` called it anyway, so
  every `.j2`/`.jinja2` source raised `TypeError: 'NoneType' object is not
  callable` — despite the class docstring promising "a real temp file when
  `pathlib_next` is unavailable". It now materializes a tracked temp file,
  reusing the mechanism `utils/source.py` already used for in-memory sources,
  and keeps the rendered basename so backend auto-detection still resolves
  `settings.yaml.j2` to the YAML backend.

## [0.11.0] - 2026-07-20

### Changed
- Narrowed `typed_merge`'s `get_type_hints` fallback to
  `(TypeError, NameError, AttributeError)`; the loader/command broad error
  boundaries that feed the `ignore_error` predicate now log the swallowed error
  at DEBUG and are documented as deliberate.
- **Faster interpolation.** A string with no Jinja delimiter (`{{`/`{%`/`{#`) —
  the common case for config values — now returns unchanged without a cache
  lookup or `Template.render`. The template/expression caches also switched from
  clear-everything-at-capacity to LRU eviction, and their env keys are
  weakref-guarded so a recycled `id(env)` can't return a render bound to a dead
  environment. `parse_sources` duplicate detection is O(1) per source (set)
  instead of O(n²) (list).

### Added
- **`allow_commands` control.** `ConfigLoader(allow_commands=False)` (or per
  `load()` call) refuses to execute a command source — `cmd://`, `exec://`,
  `sh://`, `*+fmt://`, script-extension files, and any reached via `!include` —
  raising `CommandsDisabledError` instead. Use it when loading configuration you
  do not fully trust. Defaults to permissive (commands allowed).
- **`sandbox` control.** `ConfigLoader(sandbox=True)` runs interpolation in
  Jinja2's `SandboxedEnvironment`, blocking attribute-traversal (SSTI) attacks
  from untrusted config values. Defaults off.
- New `docs/guide/security.md` documenting the trust model and both controls.

### Fixed
- **`!include` now routes through the loader driving the current parse.** The
  include constructor is registered once per YAML loader class, capturing the
  first `ConfigLoader`; nested `!include`/`!load` previously inherited that first
  loader's settings (base_dir, merge, and — critically — `allow_commands`),
  leaking state across loaders. Includes now use the loader actually performing
  the load.
- `ConfigLoader.load(merge_options=...)` no longer permanently overwrites the
  instance's `merge_options` — the documented per-call override was leaking into
  every subsequent `load()`.
- Backend resolution is now deterministic: recursive backend discovery returns a
  definition-ordered list instead of a `set`, so when two backends' patterns both
  match a path, the first-registered one reliably wins (previously hash-order
  dependent).
- Constructing a `ConfigLoader` (including the import-time default instance) no
  longer calls `setLevel()` on the module logger — library code no longer mutates
  global logging state.
- Stream and unnamed in-memory sources each get a unique virtual path; previously
  every stream materialized to the same `MemPath("stream")`, so resolving sources
  up front left all of them holding the last stream's content.
- `CommandBackend` decodes command output as UTF-8 (or an explicit `encoding=`)
  instead of the locale codec, which mangled UTF-8 output on Windows; temp-file
  fallback sources are cleaned up at exit and their suffixes sanitized.

## [0.10.0] - 2026-07-18

### Added
- `OpaqueMerge` mixin, `opaque` class decorator, and `TypedNamespace` base for
  customizing `typed_merge`, all exported from the package root.
  `OpaqueMerge`/`opaque` mark a type opaque (last object wins, no field
  introspection — for a fully-built config object or one with factory-function
  field hints); `TypedNamespace` applies `_parse_<field>` coercers at
  construction. The `__merge__` and `_parse_<field>` extension hooks are now
  documented in the merging guide.

### Changed
- YAML `!include`/`!load` auto-registration now logs a `WARNING` when it
  overrides a constructor already registered on the loader class, so a
  redundant manual `yaml.add_constructor("!include", ...)` is visible rather
  than silently replaced.

## [0.9.8] - 2026-07-18

### Added
- `typed_merge` is now exported from the top-level `yaconfiglib` package
  (`from yaconfiglib import typed_merge`), alongside `MergeMethod` /
  `ConfigLoaderMergeMethod`. It was previously only reachable via the internal
  `yaconfiglib.utils.merge` path.

### Fixed
- `typed_merge` no longer raises `TypeError: issubclass() arg 1 must be a class`
  when a field's resolved type hint is a **non-class callable** (e.g. an
  `ipaddress`-style factory function such as `netutils.IPNetwork`). Such a hint is
  now treated as an opaque coercer: the last value wins, coerced through the
  callable when it accepts the value, otherwise returned unchanged. Class hints are
  unaffected.

## [0.9.7] - 2026-07-14

### Documentation
- Added docstrings across `ConfigBackend` and every built-in backend (YAML, TOML, JSON, INI, dotenv, env, command, python, jinja2), and filled in `ConfigLoader`'s constructor, `load`, `load_all`, and `parse_sources`.
- Rebuilt the docs site: `docs/index.md` is now a full landing page instead of a README redirect stub; added task-oriented guide pages (`backends`, `merging`, `templating`, `includes`, `models`) under `docs/guide/`; split the flat API reference into per-module pages (`api/loader.md`, `api/backends.md`, `api/utils.md`).

## [0.9.6] - 2026-07-14

### Added
- Added optional nested environment variable loading and scalar coercion to `EnvVarBackend` via `nested_delimiter` and `coerce`.
- Expanded benchmarks with focused suites for source parsing, merge behavior, Jinja/load interpolation, dot-access lookup, and env backend loading.

### Changed
- Optimized `DotAccessibleDict.get()` dotted traversal to avoid exception-converting attribute lookup on hot paths.

### Fixed
- Fixed `.env` parsing so whitespace-delimited inline comments are stripped while hashes inside quoted or unquoted values are preserved.
- Defined the missing `ConfigLoader.load_as` type variable so runtime type-hint introspection succeeds.
- Fixed `Jinja2ConfigLoader` so `environment=` is honored, the backend is discoverable as `jinja2`, and rendered in-memory configs are parsed without rejoining the parent base directory.
- Fixed `ConfigLoader.load()` so `merge_options` are passed to merge implementations, including `mergelists=True`.
- Added an explicit `TypeError` for `flatten=True` on scalar merged results instead of leaving an unbound local failure path.
- Fixed duplicate path tracking, nested iterable option propagation, and stdlib `base_dir` glob fallback behavior in `parse_sources()`.

## [0.9.5] - 2026-07-13

### Changed
- **Documentation**: Replaced AGENTS-specific development and release references in `README.md` with concrete local install/test commands and a concise release workflow summary for contributors.

## [0.9.4] - 2026-07-11

### Changed
- **Performance**: Optimized `utils/source.py` by precompiling command matching regex at the module level and avoiding path splitting allocations in `has_glob_pattern` (yielding a ~58% speedup in glob checks).
- **Performance**: Optimized `loader.py` and `utils/jinja2.py` by caching compiled templates/expressions and reusing the Jinja Environment in `load_all` (up to 30x faster template interpolation and 8x faster `load_all` sequential loads).
- **Performance**: Optimized `utils/merge.py` by fast-pathing standard collection types in `is_array` and bypassing positional dictionary comprehensions in `_deep_lists` when positional merging is disabled (yielding a ~19-35% speedup in deep merges).

### Fixed
- Fixed `UnboundLocalError` inside `load_all` error handling path when a load failure occurred before the local variable `value` was bound.

## [0.9.3] - 2026-07-11

### Fixed
- Fixed false-positive glob pattern detection for `cmd://` and `exec://` sources whose command arguments contained glob metacharacters (e.g. `[`, `]`). Paths identified as command sources now bypass the glob check and are yielded directly.

## [0.9.2] - 2026-07-11

### Fixed
- Replaced Python 3.10+ `match`/`case` statement in `loader.py` with `if`/`elif` for Python 3.9 compatibility.
- Replaced runtime `|` pipe union syntax in `utils/source.py` (`SourceLike`) with `typing.Union` for Python 3.9 compatibility.

## [0.9.1] - 2026-07-11

### Fixed
- Corrected dependency declaration from `pathlib_next` to canonical PyPI name `pathlib-next`.

## [0.9.0] - 2026-07-11

### Added
- Pluggable backend registry with custom backends: `DotenvBackend` (parsing `.env` files), `EnvVarBackend` (querying `os.environ` with prefix filtering), and `PythonBackend` (direct dict injection).
- New `CommandBackend` supporting command and shell script execution (`cmd://`, `exec://`, `.sh`, `.bat`, etc.) with format overrides and shebang-based routing (`#!json` / `#!yaml`).
- Top-level standard library API parity (`load`, `loads`, `dump`, `dumps`) with the newly implemented `dump` function.
- Optional Pydantic model validation (`load_as`) with fallback support to dataclasses.
- Dot-notation dictionary access wrapper (`DotAccessibleDict`) for deep configuration values.
- Jinja2 environment features: environment auto-injection (`env.KEY`) and strict interpolation mode (`strict=True`).
- Comprehensive unit tests in `tests/test_v2_features.py`.

### Changed
- **API Modernization**: Renamed `configloader` argument to `loader` across the public API surface (`load`, `loads`, and backends).
- **YAML Inclusion**: Idempotent auto-registration of `!include` and `!load` constructor tags in SafeLoader, now dynamically delegating to the parent `ConfigLoader` instance to support nested TOML/JSON/command loads.
- **Compatibility**: Replaced Python 3.10+ specific features to support Python 3.9+, and updated pyproject.toml / CI workflows.
- **pathlib_next**: Made `pathlib_next` an optional dependency with standard library pathlib, tempfile, and glob fallback behaviors.
- **Code Organization**: Decoupled `typed_merge` logic into its own `utils/typing_merge.py` module.
- **License**: Changed the license from GNU GPLv3 to MIT License.
- **Refactoring**: Completely rewrote `utils/merge.py` and `utils/jinja2.py` in a clean-room implementation, removing all legacy code dependencies (GPL3 taint from `hiyapyco` and `yamlinclude`).
- **Error Handling**: Improved error handling across `loader.py` and `utils/source.py`, replacing bare `except Exception:` blocks with targeted exception capturing.
- **Logging**: Adopted idiomatic module-level loggers instead of passing logger instances down the call stack.
- **Typing**: Adopted modern PEP 604 union types and improved static analysis compatibility in merge functions.

### Removed
- Legacy references and code blocks tied to `hiyapyco`.

[Unreleased]: https://github.com/jose-pr/yaconfiglib/compare/v0.11.2...HEAD
[0.11.2]: https://github.com/jose-pr/yaconfiglib/compare/v0.11.1...v0.11.2
[0.11.1]: https://github.com/jose-pr/yaconfiglib/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/jose-pr/yaconfiglib/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.8...v0.10.0
[0.9.8]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.7...v0.9.8
[0.9.7]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.6...v0.9.7
[0.9.6]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.5
[0.9.4]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.4
[0.9.3]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.3
[0.9.2]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.2
[0.9.1]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.1
[0.9.0]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.0
