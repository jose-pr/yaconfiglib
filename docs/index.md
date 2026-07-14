# yaconfiglib

**yaconfiglib** is a modern, extensible configuration parser library for Python.
It loads, merges, and interpolates configuration from YAML, TOML, JSON, INI,
`.env` files, environment variables, and even command/script output — then
hands you back a single object you can traverse with dot-notation or hydrate
straight into a Pydantic model or dataclass.

It has zero required runtime dependencies beyond the standard library; every
format-specific integration (PyYAML, `toml`/`tomllib`, Jinja2, Pydantic) is
strictly optional and only imported when you actually use it.

## Why yaconfiglib

- **One API, many formats.** `yaconfiglib.load()` reads YAML, TOML, JSON,
  INI, and `.env` files interchangeably — the backend is picked from the
  file extension or an explicit `loader=` argument.
- **Hiera-style layering.** Pass multiple sources and yaconfiglib merges
  them in order — deep-merge dicts, extend lists, or replace outright,
  your choice per call.
- **Recursive includes.** `!include`/`!load` YAML tags and a `cmd://` URI
  scheme let one config pull in others, or the output of a shell command,
  without any extra glue code.
- **Templating built in.** Optional Jinja2 interpolation lets a config
  reference its own already-parsed values, environment variables, or
  arbitrary Python expressions.
- **Typed results.** `load_as()` hydrates a Pydantic model or dataclass
  directly from a loaded document, when you want validation instead of a
  loose dict.

## Installation

```bash
pip install yaconfiglib
```

Add extras for the backends you need:

```bash
pip install "yaconfiglib[yaml,toml,jinja2]"
```

| Extra | Adds | Needed for |
| --- | --- | --- |
| `yaconfiglib[yaml]` | `pyyaml` | YAML parsing and `!include`/`!load` tags |
| `yaconfiglib[toml]` | `toml` (fallback for Python < 3.11) | TOML parsing |
| `yaconfiglib[jinja2]` | `jinja2` | Interpolation, `.j2` templated sources, `env.VAR` injection |

## Quick start

```python
import yaconfiglib

# Load a single YAML file, with Jinja2 interpolation enabled.
config = yaconfiglib.load("config.yaml", interpolate=True)

# Traverse the result like nested attributes.
print(f"Server starting on: {config.server.host}:{config.server.port}")
```

Layer multiple sources — later sources override earlier ones:

```python
from yaconfiglib import ConfigLoader, ConfigLoaderMergeMethod

loader = ConfigLoader(base_dir="config/", merge=ConfigLoaderMergeMethod.Deep)
settings = loader.load("base.yaml", "production.yaml", "secrets.env")
```

## Where to go next

- **[Guide](guide/backends.md)** — task-oriented walkthroughs: backends,
  merging strategies, templating, includes, and model hydration, each with
  runnable examples.
- **[API Reference](api/reference.md)** — generated reference for every
  public class and function, organized by module.
- **[Changelog](changelog.md)** — release history.

The [project README](https://github.com/jose-pr/yaconfiglib#readme) has a
condensed version of this content for browsing on GitHub/PyPI.
