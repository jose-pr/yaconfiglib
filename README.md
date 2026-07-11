# yaconfiglib

[![PyPI version](https://badge.fury.io/py/yaconfiglib.svg)](https://badge.fury.io/py/yaconfiglib)
[![Python versions](https://img.shields.io/pypi/pyversions/yaconfiglib.svg)](https://pypi.org/project/yaconfiglib/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**yaconfiglib** is a modern, extensible configuration parser library for Python. It allows you to seamlessly load, merge, and interpolate multiple configurations in a variety of formats (YAML, TOML, JSON, INI, `.env`).

## Features

- **Standard Library API Parity**: Supports `yaconfiglib.load()`, `loads()`, `dump()`, and `dumps()` as a drop-in replacement for the `json` or `yaml` modules.
- **Model Validation**: Native hydration of Pydantic models or python dataclasses using `yaconfiglib.load_as(MyModel, "config.yaml")`.
- **Dot-Notation Access**: Configuration results are wrapped in a `DotAccessibleDict`, allowing seamless access like `config.database.port` instead of `config["database"]["port"]`.
- **Advanced Templating**: Interleave configurations with Jinja2. Generate configuration blocks dynamically, auto-inject `os.environ` via `env.VAR_NAME`, or reference previously declared values using Jinja's `{% do %}` statements.
- **YAML Includes**: Out-of-the-box support for `!include` and `!load` YAML constructors to seamlessly recursively load partial configuration files.
- **Format Agnostic**: Built-in support for TOML, YAML, JSON, INI, and Dotenv, with an extensible architecture to easily add your own backends.
- **Shell & Command Execution**: Execute arbitrary commands or shell scripts dynamically (e.g. `cmd://aws...` or `./generate.sh`) and auto-parse their outputs, with support for shebang-based format routing (e.g. `#!json` header).
- **Path Agnostic**: Compatible with both standard `pathlib.Path` and optionally [pathlib_next](https://github.com/jose-pr/pathlib_next) for URI loading (HTTP, SFTP, etc.).

## Installation

Install using `pip`:

```bash
pip install yaconfiglib
```

To enable parsing of specific formats, install the optional dependencies:

```bash
# For YAML support
pip install yaconfiglib[yaml]

# For TOML support
pip install yaconfiglib[toml]

# For Jinja2 templating and transformations
pip install yaconfiglib[jinja2]
```

## Quick Start

```python
import yaconfiglib

# 1. Standard API Parity
config = yaconfiglib.load("config.yaml", interpolate=True)

# 2. Dot-Notation Access
print(f"Connecting to: {config.database.host}:{config.database.port}")

# 3. Model Hydration (Pydantic / Dataclasses)
from pydantic import BaseModel

class DatabaseConfig(BaseModel):
    host: str
    port: int

typed_config = yaconfiglib.load_as(DatabaseConfig, "config.yaml")
print(typed_config.host)
```

## Advanced Loading (Merging Multiple Sources)

```python
from yaconfiglib import ConfigLoader

# Initialize the loader
loader = ConfigLoader(interpolate=True, inject_env=True)

# Load and merge configurations from multiple sources (YAML, TOML, .env, etc.)
config = loader.load(
    "base_config.yaml",
    "override_config.toml",
    ".env"
)

print(config)
```
