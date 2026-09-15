# Includes

YAML sources can pull in other configuration files — or the output of
commands — inline, using `!include`/`!load` tags. This is how
yaconfiglib supports hiera-like recursive composition from within a
single file, rather than only via multiple `load()` arguments.

## Basic include

```yaml
# config.yaml
database: !include "db_settings.toml"
```

```python
config = yaconfiglib.load("config.yaml")
config.database.host  # value from db_settings.toml
```

The included path is resolved relative to the including file. `!load` is
an alias for `!include` with identical behavior.

Registration is automatic: as soon as `YamlConfig.load()` runs with a
parent `ConfigLoader` in scope (the normal case when calling
`yaconfiglib.load(...)`), the `!include`/`!load` constructors are
registered on a private, yaconfiglib-owned subclass of the PyYAML loader
class. `yaml.SafeLoader` itself (and any loader class you pass as
`loader_cls=`) is never modified, so a plain `yaml.safe_load()` elsewhere in
the process never resolves `!include`. The tags only work in documents
loaded through a `ConfigLoader`.

!!! note "You do not need `yaml.add_constructor` yourself"
    Registering `!include`/`!load` by hand
    (`yaml.add_constructor("!include", ...)`) is unnecessary — yaconfiglib
    does it automatically on the first load. A manual registration on your
    own loader class is left alone; yaconfiglib parses with its own subclass.

## Passing extra arguments

The tag accepts a sequence node to forward positional args, or a mapping
node for keyword args, to the nested `load()` call:

```yaml
# Sequence form: [pathname, *args]
overlay: !include ["overrides.yaml"]

# Mapping form: keyword args, must include `pathname`
overlay: !include
  pathname: "overrides.yaml"
  encoding: "utf-8"
```

## Including command output

Combine `!include` with a `cmd://`/`exec://` URI (see
[Backends → Commands and scripts](backends.md#commands-and-scripts)) to
pull in dynamically generated configuration, such as secrets fetched at
load time:

```yaml
secrets: !include 'cmd+json://python -c "import json; print(json.dumps({\"token\": \"super-secret\"}))"'
```

The command's stdout is parsed according to the `+fmt` suffix (`json`
here), a `#!fmt` shebang in its own output, or format sniffing if neither
is given.

## How it differs from multi-source `load()`

Passing multiple sources to `ConfigLoader.load("a.yaml", "b.yaml")` merges
independently-loaded top-level documents (see [Merging](merging.md)).
`!include` instead **nests** a document at a specific key within its
parent — use it when a value, not the whole document, should come from
another file.
