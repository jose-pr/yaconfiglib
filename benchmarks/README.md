# Benchmarks

Micro-benchmarks for the paths that carry most of a load: source parsing, the
merge strategies, Jinja2 interpolation, dot access, and the environment
backend. They are a **sanity check for development**, not evidence — see
"Local runs are not evidence" below.

## Running

```bash
pip install -e ".[yaml,jinja2]"     # the harness imports both
python benchmarks/bench.py          # every suite
python benchmarks/bench.py merge    # one suite: sources, merge, jinja, dot, env
python benchmarks/bench.py --markdown env
```

The harness puts `../src` first on `sys.path`, so it always measures the tree
it sits in rather than an installed copy.

## Saving results

```bash
python benchmarks/bench.py all --save              # default name, see below
python benchmarks/bench.py all --save some/where.json
```

With no path, the file lands in `benchmarks/results/` as

```
<version>-<implementation><major>.<minor>-<os.name>-<arch>.json
```

for example `0.11.2-cpython3.14-nt-arm64.json`. One file per (version,
interpreter, platform); results are committed, so a before/after is
recoverable later.

## Schema

| Key | Meaning |
| --- | --- |
| `name` | the file's own stem |
| `version` | read from the `pyproject.toml` beside the benchmarked `src/`, not from installed metadata |
| `commit` | short `HEAD` of the benchmarked tree, or `null` (an export has no git history) |
| `python`, `implementation` | e.g. `3.14.7`, `CPython` |
| `processor`, `platform` | CPU model and OS string. Never a host name, user name or path — these files are committed |
| `source` | `"ci"` when the `CI` environment variable is set, else `"local"` |
| `created` | UTC, seconds precision |
| `metrics` | `<suite>.<snake_case_case>` → `{min_ms, median_ms, max_ms, calls, samples}`, **milliseconds per call** |
| `checks` | the same key shape → a correctness value rather than a timing |

Each timing row loops `calls` times per sample and takes `samples` samples;
the JSON divides by `calls`, so `median_ms` is per call however the loop is
sized. **Compare on `median_ms`** — `min_ms` is the least noisy figure on a
busy machine but flatters a cold cache, and `max_ms` is mostly scheduler
noise.

`checks` exists so a correctness assertion can live beside the timing it
belongs to without ending up in a numeric comparison.

## Local runs are not evidence

A number measured on a developer machine says little: turbo states, other
processes and (here) x64 emulation versus native ARM64 all move it by more
than the changes usually being measured. Treat a local run as a smoke test
that nothing became *dramatically* slower.

**A performance claim in a release, changelog or plan comes from a CI run**,
or says plainly that it does not. There is deliberately no per-push benchmark
job: these are run on demand.
