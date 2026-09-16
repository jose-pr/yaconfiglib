# API Reference

Generated from docstrings, organized by area:

- **[Loader](loader.md)** — `ConfigLoader`, `DotAccessibleDict`,
  `ConfigLoaderMergeMethod`, and the module-level `load`/`loads`/`dump`/`dumps`.
- **[Backends](backends.md)** — the `ConfigBackend` base contract and every
  built-in format backend (YAML, TOML, JSON, INI, dotenv, env, command,
  python, jinja2).
- **[Errors](errors.md)** — the `ConfigError` family (each class also the
  builtin exception it raised before) and `load_error_types()` for a single
  `except` clause.
- **[Utilities](utils.md)** — merge strategies, Jinja2 interpolation
  helpers, and source discovery.
