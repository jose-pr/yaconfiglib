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
- `YamlConfig.load(origin=...)`: the document that relative `!include`/`!load` paths
  resolve against. Defaults to the file being parsed; a rendered `.j2` template passes
  its own path.
- `timeout=` for command sources, e.g. `loader.load("cmd://...", timeout=30)`: after
  that many seconds the command and its child processes are killed and
  `subprocess.TimeoutExpired` is raised. There is still no timeout by default.
- `ConfigLoader.load_all()` accepts `sandbox=` and `allow_commands=` per call; they
  also apply to nested `!include` targets.

### Changed
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

### Fixed
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
