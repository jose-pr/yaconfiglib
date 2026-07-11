# yaconfiglib

[![PyPI version](https://badge.fury.io/py/yaconfiglib.svg)](https://badge.fury.io/py/yaconfiglib)
[![Python versions](https://img.shields.io/pypi/pyversions/yaconfiglib.svg)](https://pypi.org/project/yaconfiglib/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**yaconfiglib** is a modern, extensible configuration parser library for Python. It allows you to seamlessly load, merge, and interpolate multiple configurations in a variety of formats (YAML, TOML, JSON, INI, `.env`), or execute scripts/commands directly to retrieve configuration objects dynamically.

Zero required runtime dependencies (except standard pathlib, with optional packages to enable extra backends).

---

## Features

- **Standard Library API Parity**: Drop-in replacements for standard serialization modules using `yaconfiglib.load()`, `loads()`, `dump()`, and `dumps()`.
- **Backend Registry & Plugins**: Core loaders for TOML, YAML, JSON, INI, and `.env`. Easily extend the library by registering custom configuration loader classes.
- **Model Validation (`load_as`)**: Automatic hydration and verification of Pydantic models or standard Python `dataclasses` directly from loaded configuration files.
- **Dot-Notation Access**: Configuration results are wrapped in a `DotAccessibleDict` supporting deep traversal like `config.database.credentials.user` and toggleable `dig` options.
- **Shell & Command Execution**: Execute arbitrary commands or shell scripts dynamically (e.g. `cmd://aws...` or `./generate.sh`) and auto-parse their outputs, with support for shebang-based format routing (e.g. `#!json` header).
- **YAML Includes**: Out-of-the-box support for `!include` and `!load` YAML constructors to seamlessly and recursively import child configurations or commands.
- **Advanced Templating**: Interleave configurations with Jinja2. Generate configuration blocks dynamically, auto-inject `os.environ` via `env.VAR_NAME`, or reference previously declared values using Jinja's `{% do %}` statements.
- **Path Agnostic**: Compatible with both standard `pathlib.Path` and optionally [pathlib_next](https://github.com/jose-pr/pathlib_next) for URI loading (HTTP, SFTP, etc.) exactly like standard file paths.

---

## Installation

Install using `pip`:

```bash
pip install yaconfiglib
```

Optional dependencies:

| Extra | Adds | Needed for |
| --- | --- | --- |
| `yaconfiglib[yaml]` | `pyyaml` | Parsing YAML configuration files and `!include` tags |
| `yaconfiglib[toml]` | `toml` | Parsing TOML configuration files |
| `yaconfiglib[jinja2]` | `jinja2` | Jinja2 templating, environment variables injection, and transformations |

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
# Recursively include other files
database: !include "db_settings.toml"

# Dynamically execute commands to retrieve parameters (e.g. secrets)
secrets: !include 'cmd+json://python -c "import json; print(json.dumps({\"token\": \"super-secret\"}))"'
```

---

## API Overview

| Module | Purpose |
| --- | --- |
| `yaconfiglib.loader` | Core `ConfigLoader` orchestrator, `load()`, `loads()`, `dumps()`, and `DotAccessibleDict` |
| `yaconfiglib.backends` | `ConfigBackend` protocol & registry |
| `yaconfiglib.backends.yaml` | `YamlConfig` parsing and include tag construction |
| `yaconfiglib.backends.toml` | `TomlConfig` parsing (using stdlib `tomllib` or `toml` package fallback) |
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

Set up a virtual environment and run the test suite:

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev,yaml,toml,jinja2]"
.venv/Scripts/pytest
```

---

## Releasing

Pushing a version tag matching `v*` (e.g., `v2.0.0`) triggers the CI release pipeline to run tests, bundle the build (wheel and sdist), publish to PyPI, and compile+upload the documentation.

### Documentation Site
API reference docs are generated using MkDocs. To preview locally:

```bash
.venv/Scripts/pip install -e ".[docs]"
.venv/Scripts/mkdocs serve
```

---

## License

MIT — see [LICENSE](LICENSE).
