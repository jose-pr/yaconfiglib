# Security & the trust model

yaconfiglib is powerful because a configuration document can *do* things:
pull in other files, run commands, and interpolate expressions. That power
means **loading a configuration file is equivalent to trusting whoever wrote
it**. This page explains the trust model and the two controls that harden it
for untrusted input.

## Configs are code (by default)

With the default `allow_commands=True`, that includes **every** `.sh`, `.bat`,
`.ps1` or `.cmd` file a glob matches — a directory of configs with one script
in it runs that script. The script's own *path* is handled by the interpreter
rather than parsed by a shell, so a name containing `&`, `;` or a space cannot
inject a second command; that narrows the blast radius but does not change the
rule. Set `allow_commands=False` for untrusted trees.

These features execute code, or let a document decide what gets read, as a
side effect of loading:

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
explicit use, not config-driven).

Code runs only through command sources, non-sandboxed interpolation,
`transform`/`%`-form `key_factory` expressions and `.j2` rendering. The
`python` backend runs no code: `PythonBackend` passes a Python object you
supply through unchanged.

Command sources run with stdin closed, and accept an opt-in `timeout=` (in
seconds) after which the command and its child processes are killed:

```python
config = loader.load("cmd+json://vault read -format=json secret/app", timeout=30)
```

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

With `inject_env=True`, templates see `env` as a read-only snapshot of
`os.environ`: they can read environment variables (including secrets you may
not want a third-party template to see) but cannot change them.

## Reading local files

`allow_commands=False` and `sandbox=True` stop **code execution** and
**template injection**. Neither stops a document from **reading files**.

**By default a document can read any file the process can.** `!include` and
`!load` are not confined to `base_dir`: an absolute path or `..` traversal
reaches anywhere, and a hostile document can pull in any local file whose
name matches a backend (`.yaml`, `.yml`, `.json`, `.toml`, `.ini`, `.env`,
...):

```yaml
stolen: !include '/home/app/.config/service/credentials.yaml'
```

### Confining reads to allowed roots

`confine_to=` closes this. With it set, a local file read that resolves
outside every allowed root raises `ConfinementError` **before the file is
opened**:

```python
config = yaconfiglib.load(source, base_dir="conf", confine_to=True)
```

It accepts, in one option:

- a sequence of roots — a target inside **any** of them is allowed;
- one string split on `os.pathsep`, the `PATH` spelling, so the value can come
  from an environment variable (a string without a separator is one root);
- `True`, meaning *base_dir*;
- `False` or `None`, meaning off, which is the default.

When — and only when — the argument is `None`, the `YACONFIGLIB_CONFINE_TO`
environment variable is read the same way. An explicit argument ignores it, so
a variable cannot widen an allowlist your code set. An **unset** variable
means no confinement, while an **empty** one, like `confine_to=[]`, is an
empty allowlist and refuses every local file read: "nothing is allowed" is
taken literally rather than treated as "off".

Every file source is checked, a top-level one included — so
`confine_to=True` also refuses a path *you* pass from outside `base_dir`,
which is what confining to `base_dir` means. That covers an open file object
you hand to `load()`, by the file its `name` points at, and an `!include`
inside a command's output. Exempt, because neither has a location to confine:
a command source itself (`allow_commands` governs those), an in-memory `#!`
document, and a stream with no real name such as `sys.stdin`.

`confine_to=` is a loader setting with no per-call form. Passing it to an
existing loader's `.load()` raises `ConfigTypeError`, rather than being taken
for a backend option and ignored — every other unknown keyword is ignored by
design, and this one is deliberately not.

**Symlinks are deliberately not resolved.** The check is on the logical path,
so a symlink inside a root may point at a file outside it — that link was put
there by whoever administers the root, precisely so a configuration could
reach the target. **The trust boundary is therefore write access to a
configuration root, not the filesystem:** anyone who can create entries in a
root can point a configuration outside it, but they could equally drop the
configuration itself there. What `confine_to=` closes is a hostile
*document*.

### Either way

Do not return, echo or log a loaded result verbatim, and run the process with
only the file permissions it needs.

## Resource use

Interpolation time grows with the number of distinct nodes in the document:
YAML anchors and aliases are interpolated once per shared node, not once per
reference. A deliberately huge document still costs time and memory, and the
Jinja sandbox does not limit CPU or time. An include cycle
(`a.yaml` → `b.yaml` → `a.yaml`) raises `ValueError` instead of recursing.

## Logging and secrets

A template such as `{{ env.DB_PASSWORD }}` exists to fetch a secret, so the
**rendered value** is the secret and the template text is not. yaconfiglib
never logs a rendered value: its DEBUG records name the key and the template
(`interpolated database.password from template '{{ env.DB_PASSWORD }}'`).

What DEBUG records *can* contain, if you enable them: source paths, template
text, and full error messages — and a parser's error message often quotes the
line that failed, which may be a configuration value. Treat DEBUG logs from
this library as sensitive as the configuration itself.

The WARNING line that `ignore_error=True` emits for a skipped source carries
only the source, the phase and the error's type — never the message text — so
it is safe in a shipped log.

**A failing command's stderr tail is part of its exception message**, so it
reaches any traceback or error report. A tool that prints credentials to
stderr when it fails will expose them there. Its stdout — the payload — is
never in the message, only in the exception's `output` attribute.

## Loading third-party configuration — checklist

```python
config = yaconfiglib.load(
    source,
    base_dir="conf",
    allow_commands=False,   # no shell execution
    interpolate=True,
    sandbox=True,           # SSTI-hardened templating
    confine_to=True,        # no reads outside base_dir
)
```

With those controls set, the following are covered, including through nested
`!include` targets and per-call overrides:

- command sources, including a command produced by rendering a `.j2` source;
- template injection in interpolated values, in `transform`/`%`-form
  `key_factory` expressions, and in `.j2` sources;
- `!include` mapping keys, which can no longer re-enable commands or disable
  the sandbox;
- **file reads**, which must resolve inside `conf` — an absolute path or `..`
  traversal raises `ConfinementError` before anything is opened.

What confinement does **not** cover, by design:

- a symlink inside `conf` pointing outside it is followed, so anyone who can
  write into `conf` can still point a configuration elsewhere — see
  [reading local files](#reading-local-files);
- in-memory `#!` documents and command sources are exempt (a command is
  governed by `allow_commands`), and a remote URI source is refused rather
  than checked.

Still up to you:
- Prefer a fixed `loader="yaml"` (or the specific format) over auto-detection so
  a filename can't select an unexpected backend. This applies to the top-level
  sources only: `!include` targets are still auto-detected from their names.
- Only pass objects you built yourself to the `python` backend.
- Set `timeout=` on command sources you allow, so a slow command cannot stall
  the load indefinitely.
- Both controls default to the permissive setting so existing trusted-config
  workflows are unchanged; opt in for untrusted input.
