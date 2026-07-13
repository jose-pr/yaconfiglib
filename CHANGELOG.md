# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.5...HEAD
[0.9.5]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.5
[0.9.4]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.4
[0.9.3]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.3
[0.9.2]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.2
[0.9.1]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.1
[0.9.0]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.0
