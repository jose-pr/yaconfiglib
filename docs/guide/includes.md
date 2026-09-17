# Includes

A glob in an `!include`/`!load` expands next to the including file, and
follows the rules in [Merging → Glob sources](merging.md#glob-sources).

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

## How include paths resolve

- A **relative path in a YAML file** resolves against **that file's**
  directory, at every depth. A file included from a subdirectory writes its
  own includes relative to itself, so a config tree can be moved as a whole.
- **Absolute paths** and **command URIs** (`cmd://`, `exec://`, `sh://`) are
  used as they are written.
- **Globs** expand next to the including file (`!include "parts/*.yaml"`).
- **Documents that are not files** — `loads()`, `#!`-marked strings, streams
  and command output — have no directory of their own, so their includes
  resolve against `base_dir` (default: the working directory).
- A **`.j2`/`.jinja2` template's** includes resolve next to the template, not
  next to the rendered copy.
- An included source's `pathname` (what `transform` and `key_factory` see) is
  absolute.

`base_dir` still applies to the sources you pass to `load()` yourself.

## Passing extra arguments

The tag accepts a sequence node of sources, or a mapping node of keyword
arguments, for the nested `load()` call:

```yaml
# Sequence form: [pathname, ...] — every item is a source, merged in order
overlay: !include ["overrides.yaml"]

# Mapping form: keyword args, must include `pathname`
overlay: !include
  pathname: "overrides.yaml"
  encoding: "utf-8"
```

The mapping form accepts only these keys: `pathname`, `encoding`,
`transform`, `key_factory`, `default`, `flatten`, `merge`, `merge_options`
and `recursive`. `key_factory` must use the `"%<jinja-expr>"` form (for
example `"%pathname.as_posix()"`). Any other key is ignored and logged at
`WARNING`, so a document cannot change trust settings such as
`allow_commands` or `sandbox` for the files it includes. See
[Security](security.md).

The sequence items are **sources merged in order**, not positional
arguments, and the mapping form's `pathname` may itself be a list of
sources.

## Include encoding

An include that does not name an `encoding` — or sets it to `null` — is read
with the encoding of the `load()` call: the per-call `encoding=`, else the
loader's. That applies at every depth, and to files, globs, `.j2` templates,
command output, and includes inside included command output.

```yaml
# read with the call's encoding
plain: !include "sub.yaml"

# read as UTF-16; what *it* includes still uses the call's encoding
special: !include {pathname: "utf16.yaml", encoding: "utf-16"}
```

A mapping-form `encoding:` applies to that target only. An include whose
real encoding differs from the call's therefore needs its own `encoding:`.

A **command source** is the one place where that target is not a file: the
codec its output is decoded with is also the codec the output itself, and
anything the output includes, are read with. So an `encoding:` on a command
include reaches deeper than it would on a file include — deliberately, since
there is no separate "file on disk" for it to stop at.

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

## When an include fails

An error inside an included file names that file **and** the file that included
it, with the line the `!include` is written on — at every depth, and whatever
the failure is (a parse error, a missing file, a bad command). The exception
type never changes: a broken JSON include still raises
`json.JSONDecodeError`, so `except` clauses and `ignore_error` predicates keep
working, and the details are also readable as `error.config_source` and
`error.config_frames`.

The mapping form needs a non-empty `pathname`, and the sequence form needs a
first item; either malformed form raises `yaml.constructor.ConstructorError`
naming the file and line, where a missing `pathname` used to surface as
`KeyError: 'pathname'`.
