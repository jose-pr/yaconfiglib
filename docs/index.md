# yaconfiglib

**yaconfiglib** is a modern, extensible configuration parser library for Python.
It loads, merges, and interpolates configuration from YAML, TOML, JSON, INI,
`.env` files, environment variables, and even command/script output — then
hands you back a single object you can traverse with dot-notation or hydrate
straight into a Pydantic model or dataclass.

It has one runtime dependency, `pathlib-next`, which provides the path layer —
recursive `**` expansion, a glob's error hook, directory-loop bounding and URI
paths. Every format-specific integration (PyYAML, `tomllib`/`tomli`, Jinja2,
Pydantic) is strictly optional and only imported when you actually use it.

## Why yaconfiglib

- **One API, many formats.** `yaconfiglib.load()` reads YAML, TOML, JSON,
  INI, and `.env` files interchangeably — the backend is picked from the
  file extension (for an open file, its file name) or an explicit `loader=`
  argument. A string passed to `yaconfiglib.loads()` is YAML unless `loader=`
  or a `#!name.ext` first line says otherwise.
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

That brings `pathlib-next` with it. Add extras for the backends you need:

```bash
pip install "yaconfiglib[yaml,toml,jinja2]"
```

| Extra | Adds | Needed for |
| --- | --- | --- |
| `yaconfiglib[yaml]` | `pyyaml` 6.x | YAML parsing and `!include`/`!load` tags |
| `yaconfiglib[toml]` | `tomli` (Python < 3.11 only) | TOML parsing; 3.11+ uses stdlib `tomllib` |
| `yaconfiglib[jinja2]` | `jinja2` 3.x | Interpolation, `.j2` templated sources, `env.VAR` injection |
| `yaconfiglib[transform]` | `jinja2` 3.x | Alias of `[jinja2]`, for the `transform:` option of `!include`/`!load` |

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
