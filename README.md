# yaconfiglib

[![PyPI version](https://badge.fury.io/py/yaconfiglib.svg)](https://badge.fury.io/py/yaconfiglib)
[![Python versions](https://img.shields.io/pypi/pyversions/yaconfiglib.svg)](https://pypi.org/project/yaconfiglib/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**yaconfiglib** is a modern, extensible configuration parser library for Python. It allows you to seamlessly load, merge, and interpolate multiple configurations in a variety of formats (TOML, YAML, JSON, INI).

## Features

- **Format Agnostic**: Built-in support for TOML, YAML, JSON, and INI, with an extensible architecture to easily add more backends.
- **Advanced Templating**: Interleave configurations with Jinja2. Generate configuration blocks dynamically or reference previously declared values using Jinja's `{% do %}` statements.
- **Chain Loading & Merging**: Intelligently merge multiple config files into one, utilizing customizable merge strategies (e.g., deep merge dictionaries, append lists).
- **Path Agnostic**: Integrates with [pathlib_next](https://github.com/jose-pr/pathlib_next) to allow configuration loading from any URI (e.g., local paths, HTTP, SFTP, etc.) exactly like standard `pathlib.Path` objects.

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
from yaconfiglib import ConfigLoader

# Initialize the loader
loader = ConfigLoader(interpolate=True)

# Load and merge configurations from multiple sources
config = loader.load(
    "base_config.yaml",
    "override_config.toml"
)

print(config)
```

