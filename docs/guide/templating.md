# Templating

yaconfiglib integrates Jinja2 in two distinct ways: **interpolating**
already-loaded configuration values, and **rendering whole files** as
templates before parsing them. Both require `yaconfiglib[jinja2]`.

## Interpolating loaded values

Pass `interpolate=True` to render every string value in the loaded result
as a Jinja2 template, with the rest of the loaded document available as
template globals:

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

A bare `{{ expr }}` (no surrounding text) is evaluated as a Python
expression rather than rendered to a string, so the result keeps its
original type:

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

## Referencing earlier values with `{% do %}`

Jinja2's `do` extension (enabled by default) lets you build up values
across a template:

```yaml
base_path: "/srv/app"
log_path: "{{ base_path }}/logs"
```

## Strict mode

```python
config = yaconfiglib.load("config.yaml", interpolate=True, strict=True)
```

With `strict=True`, referencing an undefined variable raises instead of
silently rendering as an empty string — useful for catching typos in
config keys early.

## Templated source files (`.j2`)

Append `.j2` or `.jinja2` to any filename to render the *entire file* as a
Jinja2 template before it's parsed by its underlying format:

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
config = yaconfiglib.load("settings.yaml.j2")
```

Pass a custom `jinja2.Environment` with `environment=` if you need custom
filters, extensions, or undefined-handling beyond the default.
