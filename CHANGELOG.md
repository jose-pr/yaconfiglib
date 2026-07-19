# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.6...HEAD
[0.9.6]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.5
[0.9.4]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.4
[0.9.3]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.3
[0.9.2]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.2
[0.9.1]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.1
[0.9.0]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.0
