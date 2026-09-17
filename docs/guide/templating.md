# Templating

Interpolation, `transform=` and a `%`-prefixed `key_factory` all need Jinja2
(`pip install "yaconfiglib[jinja2]"`). If it is missing — or installed but
unimportable, which a Jinja2 2.x beside MarkupSafe 2.1+ is — those options
raise `ImportError` naming the extra and the underlying import error. That
happens before `ignore_error` is consulted, so an ignoring load fails loudly
rather than returning an unrendered or empty result.

yaconfiglib integrates Jinja2 in two distinct ways: **interpolating**
already-loaded configuration values, and **rendering whole files** as
templates before parsing them. Both require `yaconfiglib[jinja2]`.

## Interpolating loaded values

Pass `interpolate=True` to render every string value in the loaded result
as a Jinja2 template, with the rest of the loaded document available as
template globals. Rendering happens **once, after every source has been
merged**, so values pulled in with `!include` see the including document's
keys too — and nothing is rendered twice:

```yaml
# config.yaml
host: "localhost"
url: "http://{{ host }}:{{ port }}"
port: 8080
```

```python
import yaconfiglib

config = yaconfiglib.load("config.yaml", interpolate=True)
print(config.url)  # "http://localhost:8080"
```

A bare `{{ expr }}` (nothing else but an optional trailing newline) is
evaluated as a Python expression rather than rendered to a string, so the
result keeps its original type:

```yaml
port: "{{ 8000 + 80 }}"   # -> 8080 (int), not "8080" (str)
enabled: "{{ true }}"      # -> True (bool)
```

## Injecting environment variables

```python
config = yaconfiglib.load("config.yaml", interpolate=True, inject_env=True)
```

```yaml
database_url: "{{ env.DATABASE_URL }}"
```

`env` is `os.environ` — any environment variable is reachable as
`env.VAR_NAME`.

## Referencing other values

A value may refer to any other top-level key, in any order. Keys are
rendered after the keys they refer to, so chains resolve completely:

```yaml
base_path: "/srv/app"
log_path: "{{ base_path }}/logs"
error_log: "{{ log_path }}/error.log"   # -> "/srv/app/logs/error.log"
```

Inside a mapping, a reference to that same mapping (`db.url` using
`{{ db.host }}`) sees the values written before it, in document order.

A cycle between two keys (`a` referring to `b` and `b` to `a`) cannot
resolve. With `strict=True` it raises `ValueError`; otherwise each value is
rendered once against what the other holds at that moment.

An included file's templates are rendered in the **merged** document, so a
reference to the included file's own key goes through the key it was
included under — `{{ svc.name }}`, not `{{ name }}`.

## Strict mode

```python
config = yaconfiglib.load("config.yaml", interpolate=True, strict=True)
```

With `strict=True`, referencing an undefined variable raises instead of
silently rendering as an empty string — useful for catching typos in
config keys early. The error names the key it happened at
(`... [at database.url]`), and list indexes appear as `servers[0].host`.

## Errors during interpolation

A template that cannot render — a syntax error, an undefined name under
`strict`, or a Python error inside an expression such as `{{ 1/0 }}` — raises
the parser's or Python's own exception type, with the key path attached (also
readable as `error.config_key`).

With `ignore_error`, the failure costs **only that value**: it keeps its
template text as written and the rest of the document still renders.

```python
# database.password fails to render; host, port and log_path are unaffected.
config = yaconfiglib.load("app.yaml", interpolate=True, ignore_error=True)
```

The predicate form is offered each failure once, where it happens, with
`phase="interpolate"` and `key=` the path — so you can skip a known-bad value
and still fail on everything else:

```python
def only_skip_optional_banner(error, *, phase, key=None, **context):
    return phase == "interpolate" and key == ("ui", "banner")
```

## Templated source files (`.j2`)

Append `.j2` or `.jinja2` to any filename to render the *entire file* as a
Jinja2 template before it's parsed by its underlying format. Keep the format
extension in front of the suffix — `config.yaml.j2`, not `config.j2` — since
that is what selects the backend the rendered text is parsed with; a template
without one raises `NotImplementedError` naming the file:

```
config.yaml.j2  ->  rendered as Jinja2  ->  parsed as YAML
```

This is a separate mechanism from `interpolate=True`: it runs before
parsing (so you can template YAML/TOML/JSON structure itself, not just
string values after the fact), and it's driven by the `.j2` extension
rather than a loader option.

```yaml
# settings.yaml.j2
replicas: {{ 2 if env.ENVIRONMENT == "production" else 1 }}
```

```python
config = yaconfiglib.load("settings.yaml.j2", inject_env=True)
```

The template sees `pathname` (the source path) and, with `inject_env=True`,
`env` (a read-only snapshot of `os.environ`).

`.j2` rendering follows the load's trust settings: it runs in Jinja2's
`SandboxedEnvironment` when `sandbox=True` or `allow_commands=False` is in
effect, and undefined variables raise when `strict=True`.

```python
config = yaconfiglib.load("settings.yaml.j2", environment=my_env)
```

Pass a custom `jinja2.Environment` with `environment=` if you need custom
filters, extensions, or undefined-handling beyond the default. Under
`sandbox=True` or `allow_commands=False` it must be a
`jinja2.sandbox.SandboxedEnvironment`, otherwise loading raises `ValueError`.
