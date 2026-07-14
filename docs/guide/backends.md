# Backends

A **backend** turns one source into a Python object. yaconfiglib picks a
backend automatically from the source's file extension (or URI scheme),
or you can request one explicitly with `loader="name"`. Every backend
implements the same [`ConfigBackend`](../api/backends.md) contract, so
custom backends work identically to the built-in ones.

## YAML

```python
import yaconfiglib

config = yaconfiglib.load("config.yaml")
```

Registers `!include` and `!load` tag constructors automatically — see
[Includes](includes.md) for details. Requires `yaconfiglib[yaml]`.

## TOML

```python
config = yaconfiglib.load("config.toml")
```

Uses the standard library `tomllib` on Python 3.11+, falling back to the
third-party `toml` package (`yaconfiglib[toml]`) on 3.9/3.10.

## JSON

```python
config = yaconfiglib.load("config.json")
```

No extra dependency — uses the standard library `json` module.

## INI

```python
config = yaconfiglib.load("config.ini")
# {"section": {"key": "value", ...}, ...}
```

Parsed with the standard library `configparser.ConfigParser`. All values
come back as strings, matching `configparser` semantics.

## `.env` files

```python
config = yaconfiglib.load(".env")
# {"database_url": "postgres://...", ...}
```

Supports `KEY=value` and `export KEY=value` syntax, single/double-quoted
values, and `#` comments (both full-line and inline, outside quotes).

## Environment variables

Unlike file-based backends, `EnvVarBackend` reads `os.environ` directly
and is typically used explicitly rather than auto-detected:

```python
from yaconfiglib import ConfigLoader
from yaconfiglib.backends.env import EnvVarBackend

# APP_DB__PORT=5432 -> {"db": {"port": 5432}}
config = ConfigLoader().load(
    loader=EnvVarBackend(prefix="APP_", nested_delimiter="__", coerce=True)
)
```

- `prefix` — only variables starting with this are included (and it's
  stripped from the resulting keys).
- `nested_delimiter` — split keys on this delimiter into nested dicts.
- `coerce` — convert string values to `None`/`bool`/`int`/`float`/parsed
  JSON where they look like one, instead of leaving everything as `str`.

## Commands and scripts

`cmd://`, `exec://`, and `sh://` URIs (plus `.sh`/`.bat`/`.ps1`/`.cmd`
files) run a shell command and parse its stdout:

```yaml
# Recursively execute a command to retrieve secrets, parsed as JSON.
secrets: !include 'cmd+json://python -c "import json; print(json.dumps({\"token\": \"super-secret\"}))"'
```

Format resolution order: an explicit `format=` argument, the `+fmt`
suffix on the scheme (`cmd+yaml://...`), a `#!fmt` shebang line in the
command's own output, then sniffing (json, yaml, toml, dotenv, ini in
turn). If nothing matches and no format was requested, the raw stdout
string is returned instead of raising.

## In-memory Python objects

`PythonBackend` wraps an already-parsed object so it can be spliced into a
loader chain without writing a file:

```python
from yaconfiglib.backends.python_backend import PythonBackend

loader.load("base.yaml", loader=PythonBackend({"override_key": "override_value"}))
```

## Jinja2-templated sources

Append `.j2`/`.jinja2` to any filename to render it as a Jinja2 template
first, then parse the rendered output with the backend matching the
underlying extension — see [Templating](templating.md).

## Writing a custom backend

Subclass `ConfigBackend` and override `load()` (and optionally `dumps()`):

```python
from yaconfiglib.backends.base import ConfigBackend
import re

class MyBackend(ConfigBackend):
    PATHNAME_REGEX = re.compile(r".*\.myfmt$", re.IGNORECASE)
    NAME = "myfmt"

    def load(self, path, encoding=None, **options):
        return parse_my_format(path.read_text(encoding=encoding or self.DEFAULT_ENCODING))
```

Subclasses are auto-discovered on import — no registry call needed. Once
imported, `yaconfiglib.load("config.myfmt")` and `loader="myfmt"` both
resolve to it.
