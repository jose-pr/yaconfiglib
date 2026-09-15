# Security & the trust model

yaconfiglib is powerful because a configuration document can *do* things:
pull in other files, run commands, and interpolate expressions. That power
means **loading a configuration file is equivalent to trusting whoever wrote
it**. This page explains the trust model and the two controls that harden it
for untrusted input.

## Configs are code (by default)

Two features execute code as a side effect of loading:

- **Command sources.** A source matching `cmd://`, `exec://`, `sh://`, a
  `*+fmt://` variant, or a `.sh`/`.bat`/`.ps1`/`.cmd` file runs through the
  shell (see [Backends → Commands](backends.md#commands-and-scripts)). Crucially
  this composes with [`!include`](includes.md): any YAML you load may contain
  `key: !include 'cmd://<anything>'`.
- **Interpolation.** With `interpolate=True`, every string value is rendered as
  a Jinja2 template. In a normal Jinja environment a hostile string can reach
  Python internals via attribute traversal (server-side template injection).
- **Templated sources.** A `.j2`/`.jinja2` source is rendered as a Jinja2
  template before it is parsed, regardless of `interpolate`.
- **Backend selection by name.** An `!include` target is always auto-detected
  from its name, and an in-memory `#!<name>` document picks its backend from
  that name, so a document can choose which backend reads what it includes.

For **trusted, local configuration** — the common case — the defaults are fine.
For **configuration from an untrusted source**, use the controls below.

## `allow_commands=False` — block command execution

```python
import yaconfiglib

# Parse the file, but never run a command (even one hidden behind !include).
config = yaconfiglib.load("untrusted.yaml", allow_commands=False)
```

A command source loaded while `allow_commands=False` raises
`yaconfiglib.CommandsDisabledError` naming the offending source, instead of
executing it. Also settable on `ConfigLoader(...)` and overridable per
`load()` call; a per-call value applies to everything that call loads,
including nested `!include` targets.

Scope: `allow_commands` gates the **command** backend on every route (scheme,
file extension, `loader="command"`, and `!include`). It does **not** restrict a
`CommandBackend` you construct and call yourself outside a load (that is
explicit use, not config-driven). Note the `python` backend also executes
Python — do not feed it untrusted input.

## A document can only tighten trust

`allow_commands` and `sandbox` cannot be relaxed from inside a document. The
`!include` mapping form accepts only `pathname`, `encoding`, `transform`,
`key_factory` (in its `"%<expr>"` form), `default`, `flatten`, `merge`,
`merge_options` and `recursive`; any other key (for example `allow_commands`,
`sandbox`, `interpolate` or `loader`) is ignored with a warning. Nested loads
inherit the enclosing call's settings and can only make them stricter.

`transform` and `%`-form `key_factory` expressions are evaluated in the
sandboxed environment whenever `sandbox=True` or `allow_commands=False` is in
effect, because an included document can supply them.

## `sandbox=True` — sandbox interpolation

```python
config = yaconfiglib.load(
    "untrusted.yaml",
    interpolate=True,
    sandbox=True,      # Jinja2 SandboxedEnvironment
)
```

Interpolation then runs in Jinja2's `SandboxedEnvironment`, which blocks the
attribute traversal used for template-injection attacks. This is Jinja's
sandbox — it is not an OS-level sandbox and does not limit CPU/time.

`.j2`/`.jinja2` sources render in the sandbox whenever `sandbox=True` **or**
`allow_commands=False` is in effect, including ones reached through
`!include`. A rendered document that turns out to be a command source is
refused while `allow_commands=False`. Passing a non-sandboxed
`environment=` for a `.j2` source under those settings raises `ValueError`.

## Loading third-party configuration — checklist

```python
config = yaconfiglib.load(
    source,
    allow_commands=False,   # no shell execution
    interpolate=True,
    sandbox=True,           # SSTI-hardened templating
)
```

- Prefer a fixed `loader="yaml"` (or the specific format) over auto-detection so
  a filename can't select an unexpected backend. This applies to the top-level
  sources only: `!include` targets are still auto-detected from their names.
- Do not pass untrusted data to the `python` backend.
- Both controls default to the permissive setting so existing trusted-config
  workflows are unchanged; opt in for untrusted input.
