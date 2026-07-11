# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Comprehensive test suite in `tests/` directory ensuring coverage across merge methods, Jinja interpolation, and configuration loading.
- Automated CI and Release workflows (`.github/workflows/ci.yml`, `release.yml`) testing on multiple OS environments (Ubuntu, Windows, macOS) and Python versions, plus OIDC Trusted Publishing.

### Changed
- **License**: Changed the license from GNU GPLv3 to MIT License.
- **Refactoring**: Completely rewrote `utils/merge.py` and `utils/jinja2.py` in a clean-room implementation, removing all legacy code dependencies (GPL3 taint from `hiyapyco` and `yamlinclude`).
- **Error Handling**: Improved error handling across `loader.py` and `utils/source.py`, replacing bare `except Exception:` blocks with targeted exception capturing.
- **Logging**: Adopted idiomatic module-level loggers instead of passing logger instances down the call stack.
- **Typing**: Adopted modern PEP 604 union types and improved static analysis compatibility in merge functions.

### Removed
- Legacy references and code blocks tied to `hiyapyco`.
