# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.1...HEAD
[0.9.1]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.1
[0.9.0]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.0


