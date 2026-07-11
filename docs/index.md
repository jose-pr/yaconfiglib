# yaconfiglib

**yaconfiglib** is a modern, extensible configuration parser library for Python.

For the full feature list, installation extras, quick-start examples for every
integration, and known limitations, see the
[project README](https://github.com/jose-pr/yaconfiglib#readme). This site adds the
generated [API reference](api/reference.md) and the [changelog](changelog.md).

## Installation

```bash
pip install yaconfiglib
```

## Quick start

```python
import yaconfiglib

# Load a YAML file
config = yaconfiglib.load("config.yaml", interpolate=True)

# Traverse configuration like attributes
print(f"Server starting on: {config.server.host}:{config.server.port}")
```
