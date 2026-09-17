# yaconfiglib

[![PyPI version](https://img.shields.io/pypi/v/yaconfiglib.svg)](https://pypi.org/project/yaconfiglib/)
[![Python versions](https://img.shields.io/pypi/pyversions/yaconfiglib.svg)](https://pypi.org/project/yaconfiglib/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/yaconfiglib/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/yaconfiglib/test.yml)](https://github.com/jose-pr/yaconfiglib/actions/workflows/test.yml)

**yaconfiglib** is a modern, extensible configuration parser library for Python. It allows you to seamlessly load, merge, and interpolate multiple configurations in a variety of formats (YAML, TOML, JSON, INI, `.env`), or execute scripts/commands directly to retrieve configuration objects dynamically.

One runtime dependency — [pathlib-next](https://github.com/jose-pr/pathlib-next),
which provides the path layer: recursive `**` glob expansion, the error hook a
glob needs to report an unreadable directory, loop bounding, and URI paths.
Every *format* integration is optional: PyYAML, `tomli`, Jinja2 and Pydantic are
extras, imported only when you use them.

---

## Features

- **Standard-library-style API**: `yaconfiglib.load()`, `loads()`, `dump()` and `dumps()`. `loads()` reads YAML unless `loader=` or a `#!name.ext` first line picks another format.
- **Backend Registry & Plugins**: Core loaders for TOML, YAML, JSON, INI, and `.env`. Easily extend the library by registering custom configuration loader classes.
- **Model Validation (`load_as`)**: Automatic hydration and verification of Pydantic models or standard Python `dataclasses` directly from loaded configuration files.
- **Dot-Notation Access**: Configuration results are wrapped in a `DotAccessibleDict` supporting deep traversal like `config.database.credentials.user` and toggleable `dig` options. Paths also index lists (`config.get("servers.0.host")`), and a tuple path (`config.get(("labels", "app.kubernetes.io/name"))`) reaches keys that themselves contain dots. Nested mappings are converted once when the result is built, so item and attribute access always return the same object, and reads never modify the configuration.
- **Shell & Command Execution**: Execute arbitrary commands or shell scripts dynamically (e.g. `cmd://aws...` or `./generate.sh`) and auto-parse their outputs, with support for shebang-based format routing (e.g. `#!json` header).
- **YAML Includes**: Out-of-the-box support for `!include` and `!load` YAML constructors to seamlessly and recursively import child configurations or commands.
- **Advanced Templating**: Interleave configurations with Jinja2. Generate configuration blocks dynamically, auto-inject `os.environ` via `env.VAR_NAME`, or reference other configuration values by name in any order.
- **Environment Overlays**: Load prefixed environment variables as flat or nested configuration, with optional scalar coercion for booleans, numbers, nulls, arrays, and objects.
- **Path Agnostic**: Sources may be a `str`, a standard `pathlib.Path`, or any
  `os.PathLike`. Paths are handled by [pathlib_next](https://github.com/jose-pr/pathlib-next)
  (a required dependency), so a URI path — HTTP, SFTP — loads exactly like a local file
  once you install the scheme support it needs.

---

## Installation

Install using `pip`:

```bash
pip install yaconfiglib
```

`pathlib-next` is installed with it. Optional extras, per format:

| Extra | Adds | Needed for |
| --- | --- | --- |
| `yaconfiglib[yaml]` | `pyyaml` 6.x | Parsing YAML configuration files and `!include` tags |
| `yaconfiglib[toml]` | `tomli` (Python < 3.11 only) | Parsing TOML configuration files; 3.11+ uses the standard library `tomllib` |
| `yaconfiglib[jinja2]` | `jinja2` 3.x | Jinja2 templating, environment variables injection, and transformations |
| `yaconfiglib[transform]` | `jinja2` 3.x | Alias of `[jinja2]`, named after the `transform:` option of `!include`/`!load` and `ConfigLoader.load()` |

---

## Quick Start

### 1. Simple Parsing and Dot-Notation Access

```python
import yaconfiglib

# Load a YAML file
config = yaconfiglib.load("config.yaml", interpolate=True)

# Traverse configuration like attributes
print(f"Server starting on: {config.server.host}:{config.server.port}")
```

### 2. Hydrate Model (Pydantic / Dataclasses)

```python
from pydantic import BaseModel
import yaconfiglib

class DBConfig(BaseModel):
    host: str
    port: int

# Hydrate the Pydantic model directly
db_settings = yaconfiglib.load_as(DBConfig, "config.yaml", loader="yaml")
print(db_settings.host)
```

### 3. Dynamic Includes & Shell Commands

In your `config.yaml`:
```yaml
# Recursively include other files (paths resolve next to this file)
database: !include "db_settings.toml"

# Dynamically execute commands to retrieve parameters (e.g. secrets)
secrets: !include 'cmd+json://python -c "import json; print(json.dumps({\"token\": \"super-secret\"}))"'
```

### 4. Environment Variable Overlays

```python
from yaconfiglib import ConfigLoader
from yaconfiglib.backends.env import EnvVarBackend

# APP_DB__PORT=5432 -> {"db": {"port": 5432}}
config = ConfigLoader().load(
    loader=EnvVarBackend(prefix="APP_", nested_delimiter="__", coerce=True)
)
```

---

## API Overview

| Module | Purpose |
| --- | --- |
| `yaconfiglib.loader` | Core `ConfigLoader` orchestrator, `load()`, `loads()`, `load_as()`, `dump()`, `dumps()`, and `DotAccessibleDict` |
| `yaconfiglib.backends` | `ConfigBackend` base class & registry |
| `yaconfiglib.backends.yaml` | `YamlConfig` parsing and include tag construction |
| `yaconfiglib.backends.toml` | `TomlConfig` parsing (stdlib `tomllib`, or its `tomli` backport below 3.11) |
| `yaconfiglib.backends.json` | `JsonConfig` parsing |
| `yaconfiglib.backends.ini` | `IniConfig` parsing |
| `yaconfiglib.backends.dotenv` | `DotenvBackend` for parsing `.env` files |
| `yaconfiglib.backends.env` | `EnvVarBackend` for loading `os.environ` variables with prefix matching |
| `yaconfiglib.backends.command` | `CommandBackend` for running processes and auto-routing outputs |
| `yaconfiglib.backends.python_backend` | `PythonBackend` for in-memory python dict injection |
| `yaconfiglib.utils.merge` | Deep dict/list merge algorithms and custom merge methods |
| `yaconfiglib.utils.jinja2` | Custom Jinja2 interpolation environments and render utilities |

---

## Development

```bash
# One venv per interpreter, named <version>-<os>-<arch>:
py -3.9 -m venv .venv/3.9-nt-amd64

# Install the tooling AND every extra that has tests, or those tests skip:
.venv/3.9-nt-amd64/Scripts/pip install -e ".[dev,yaml,toml,jinja2]"

# Put the venv's Scripts (bin on POSIX) directory first on PATH, or activate
# it: the command-backend tests start `python` as a subprocess.
export PATH="$PWD/.venv/3.9-nt-amd64/Scripts:$PATH"

python -m pytest -q -rs
```

[`AGENTS.md`](AGENTS.md) is the orientation file for a checkout: layout,
environments, CI and the release procedure.

Micro-benchmarks live in `benchmarks/`; see
[`benchmarks/README.md`](benchmarks/README.md) for how to run them, save a
result file and compare two runs.

Installing only `.[dev]` is supported — the tests that need PyYAML, Jinja2 or a
TOML parser skip, and `-rs` lists them — but then the suite covers much less.

### Releasing

This project follows [Semantic Versioning](https://semver.org/) and keeps a
[`CHANGELOG.md`](CHANGELOG.md). Pushing a tag matching `v*` triggers the release
workflow: test gate → build → GitHub release → PyPI publish. A strict docs
build runs alongside as a gate — it can fail the run, but it never blocks the
publish, and it does not deploy. The Docs workflow owns the site and is
dispatched once the release exists.

### Documentation site

MkDocs builds the API reference from `docs/`. The site is deployed by the
Docs workflow: on pushes to `main` that touch `docs/`, `mkdocs.yml`, `src/` or
the changelog, for each published release, and on manual dispatch. Between
releases it therefore describes `main`, which may be ahead of PyPI.

To preview locally, from a latest-Python venv (`bin/` instead of `Scripts/` on
Unix):

```bash
.venv/3.14-nt-arm64/Scripts/pip install -e ".[docs,yaml,toml,jinja2]"
.venv/3.14-nt-arm64/Scripts/mkdocs serve
```

---

## License

MIT — see [LICENSE](LICENSE).
