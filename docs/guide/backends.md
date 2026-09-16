# Backends

A **backend** turns one source into a Python object. yaconfiglib picks a
backend automatically from the source's file extension (or URI scheme),
or you can request one explicitly with `loader="name"`. Every backend
implements the same [`ConfigBackend`](../api/backends.md) contract, so
custom backends work identically to the built-in ones.

Files saved with a UTF-8 byte-order mark load normally — the mark is
ignored rather than becoming part of the first key.

When a format's optional dependency is not installed, the error names the
extra to install rather than only reporting an unknown format.

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

Uses the standard library `tomllib` on Python 3.11+, and its `tomli`
backport (`yaconfiglib[toml]`) on 3.9/3.10, so a TOML 1.0 document means the
same thing on every supported interpreter. (`tomli` 2.4+ also accepts some
TOML 1.1 syntax that 3.11-3.14's `tomllib` rejects, so a TOML 1.1-only file is
not portable.)

## JSON

```python
config = yaconfiglib.load("config.json")
```

No extra dependency — uses the standard library `json` module.

## INI

```python
config = yaconfiglib.load("config.ini")   # or config.cfg
# {"section": {"key": "value", ...}, ...}
```

Parsed with the standard library `configparser.ConfigParser`. Both `.ini`
and `.cfg` are detected. All values come back as strings, matching
`configparser` semantics.

`%` interpolation is on by default, as `configparser` does it: `%(name)s`
refers to another key and a literal percent is written `%%`. A logging or
alembic formatter line is not a reference, so reading such a file needs the
interpolation turned off:

```python
config = yaconfiglib.load("logging.ini", ini_interpolation=None)
# format: "%(levelname)-5.5s [%(name)s] %(message)s", verbatim
```

`ini_interpolation` accepts `"basic"` (the default), `"extended"` for
`${section:key}` references, `None`/`"none"` for raw values, or a
`configparser.Interpolation` instance. For `!include`d INI files, set the
default on the backend instead — reader options are not passed through an
include:

```python
from yaconfiglib.backends.ini import IniConfig

loader = ConfigLoader(loader_factory=lambda path: IniConfig(interpolation=None))
```

Keys in `[DEFAULT]` are inherited by every section and are not returned as a
section of their own, so a file containing nothing else loads as `{}` (with a
warning). To read those keys as a section, point `default_section` at a name
the file does not use:

```python
config = yaconfiglib.load("defaults.ini", ini_default_section="__none__")
# {"DEFAULT": {"timeout": "30"}}
```

## `.env` files

```python
config = yaconfiglib.load(".env")
# {"database_url": "postgres://...", ...}
```

Supports `KEY=value` and `export KEY=value` syntax, single/double-quoted
values, and `#` comments (both full-line and inline, outside quotes).

- A key may contain `.` and `-` after its first character.
- A **double-quoted** value may span newlines, and `\n`, `\r`, `\t`, `\"`
  and `\\` are decoded inside it. Any other backslash pair is kept as it is.
- A **single-quoted** value may span newlines and is kept raw — use it for a
  literal backslash, such as a Windows path (`DIR='C:\new'`).
- A quoted value ends at its first matching quote, and only whitespace or a
  `#` comment may follow it on that line.
- In an **unquoted** value, quote characters are ordinary: `KEY=it's here # c`
  gives `it's here`.
- Only a newline ends an entry, so a form feed, vertical tab, `\x1c`-`\x1e`,
  U+0085, U+2028 or U+2029 inside a value is kept rather than cutting it short.
- A line that cannot be parsed is skipped with a logged warning. Pass
  `DotenvBackend(strict=True)` or `dotenv_strict=True` to raise `ValueError`
  instead — that also rejects a file holding no assignment at all. An
  unterminated quoted value always raises, in either mode, because it would
  otherwise swallow the rest of the file.

Dotenv claims `.env`, `*.env` and staged names such as `.env.local` and
`.env.development.local`. A name whose **final** suffix belongs to another
format goes to that format's backend instead: `app.env.yaml` is YAML,
`settings.env.json` is JSON, and `.env.j2` or `config.env.yaml.j2` is rendered
as a template first. Pass `loader="dotenv"` to read any name as dotenv.

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
command's own output, then sniffing.

Sniffing tries, in turn: **json** (any JSON value, so `42` stays the
number), **yaml** but only when the result is a mapping or a list,
**toml**, **dotenv** but only when every non-comment line is a `KEY=value`
assignment, and **ini**. If none of them accepts the output, the raw stdout
string is returned instead of raising.

The yaml restriction matters because YAML reads arbitrary text as a string:
without it, INI output was accepted as a single scalar and never reached the
INI parser. The cost is that output only YAML would read as a scalar — a bare
date such as `2026-09-15`, or `yes`, or `~` — now comes back as the raw
string. Use `cmd+yaml://` to force a YAML scalar.

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

Subclass `ConfigBackend` and override `load()` (and optionally `dumps()`,
which you call on the backend instance — `yaconfiglib.dump()`/`dumps()`
always write YAML):

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
