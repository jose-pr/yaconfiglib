from __future__ import annotations

import argparse
import io
import logging
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path as StdlibPath

# Add src to sys.path so the benchmark can run from a checkout.
sys.path.insert(0, str(StdlibPath(__file__).parent.parent / "src"))

from jinja2 import Environment

from yaconfiglib.loader import ConfigLoader, DotAccessibleDict
from yaconfiglib.utils import jinja2
from yaconfiglib.utils.merge import MergeMethod
from yaconfiglib.utils.source import has_glob_pattern, parse_sources


BenchmarkRows = list[tuple[str, object]]


def _measure(operation: Callable[[], object], *, repeat: int = 5, warmup: bool = True) -> float:
    if warmup:
        operation()
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


def _measure_status(
    operation: Callable[[], object],
    *,
    repeat: int = 3,
    warmup: bool = True,
) -> float | str:
    try:
        return _measure(operation, repeat=repeat, warmup=warmup)
    except Exception as error:
        return f"{type(error).__name__}: {error}"


def _fmt_metric(value: object) -> str:
    if isinstance(value, float):
        if value < 0.001:
            return f"{value * 1_000_000:.2f}us"
        if value < 1:
            return f"{value * 1000:.2f}ms"
        return f"{value:.4f}s"
    return str(value)


def _print_rows(title: str, rows: BenchmarkRows, *, markdown: bool) -> None:
    if markdown:
        print("| Suite | Benchmark Case | Time / Metric |")
        print("|---|---|---|")
        for name, metric in rows:
            print(f"| {title} | {name} | {_fmt_metric(metric)} |")
        return

    print(f"{title}:")
    for name, metric in rows:
        print(f"   - {name}: {_fmt_metric(metric)}")


def benchmark_sources() -> BenchmarkRows:
    rows: BenchmarkRows = []

    with tempfile.TemporaryDirectory() as tmpdir:
        root = StdlibPath(tmpdir)
        for name in ("a.yaml", "b.yaml", "c.yaml"):
            (root / name).write_text(f"name: {name}\n", encoding="utf-8")

        mixed_sources = [
            "a.yaml",
            ["b.yaml", "c.yaml"],
            "cmd+json://python -c \"print('{}')\"",
            "#!.yaml\ninline: true\n",
            io.StringIO("stream: true\n"),
        ]

        rows.append(
            (
                "parse mixed sources (500)",
                _measure(
                    lambda: [
                        list(parse_sources(mixed_sources, base_dir=root))
                        for _ in range(500)
                    ],
                    repeat=5,
                ),
            )
        )
        rows.append(
            (
                "glob expansion '*.yaml' (200)",
                _measure(
                    lambda: [
                        list(parse_sources(["*.yaml"], base_dir=root))
                        for _ in range(200)
                    ],
                    repeat=5,
                ),
            )
        )

        duplicate_count = len(list(parse_sources(["a.yaml", "a.yaml"], base_dir=root)))
        nested_count = len(list(parse_sources([["a.yaml", "b.yaml"], "c.yaml"], base_dir=root)))
        command_path = next(parse_sources(["cmd+json://echo {\"x\":[1]}"], base_dir=root))
        rows.append(("duplicate path behavior", f"{duplicate_count} yielded path(s)"))
        rows.append(("nested iterable behavior", f"{nested_count} yielded path(s)"))
        rows.append(("command glob metacharacter behavior", str(command_path)))

    normal = StdlibPath("normal/path/to/file.yaml")
    globbed = StdlibPath("glob/path/**/*.yaml")
    rows.append(
        (
            "has_glob_pattern pair (20k)",
            _measure(
                lambda: [
                    (has_glob_pattern(normal), has_glob_pattern(globbed))
                    for _ in range(20_000)
                ],
                repeat=5,
            ),
        )
    )
    return rows


def benchmark_merge() -> BenchmarkRows:
    def make_pair() -> tuple[dict, dict]:
        return (
            {
                "a": 1,
                "b": [1, 2, {"x": "y"}],
                "c": {"d": {"e": "f"}, "g": [1, 2, 3]},
                "h": "hello",
            },
            {
                "b": [3, 4, {"x": "z", "y": "w"}],
                "c": {"d": {"e": "g", "h": "i"}},
                "i": "world",
            },
        )

    def deep_merge_many(mergelists: bool) -> None:
        for _ in range(1_000):
            base, overlay = make_pair()
            MergeMethod.Deep(base, overlay, mergelists=mergelists)

    result = MergeMethod.Deep(
        {"items": [{"name": "a", "enabled": False}]},
        {"items": [{"name": "a", "enabled": True}]},
        mergelists=True,
    )
    return [
        ("deep merge, append list dicts (1k)", _measure(lambda: deep_merge_many(False), repeat=5)),
        ("deep merge, positional list dicts (1k)", _measure(lambda: deep_merge_many(True), repeat=5)),
        ("positional merge correctness", result),
    ]


def benchmark_jinja() -> BenchmarkRows:
    env = Environment(extensions=["jinja2.ext.do"])
    data = {
        "user": "{{ env.USER }}",
        "db": {
            "host": "{{ host }}",
            "port": "{{ port }}",
            "url": "postgresql://{{ user }}@{{ host }}:{{ port }}/db",
        },
        "flags": ["{{ flag1 }}", "{{ flag2 }}", "static"],
    }
    context = {
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "flag1": "active",
        "flag2": "debug",
        "env": {"USER": "postgres"},
    }

    docs = [
        f"#!.yaml\nkey_{i}: value_{i}\ninterpolation: '{{{{ key_{i} }}}}'"
        for i in range(50)
    ]
    loader = ConfigLoader(interpolate=True)

    def interpolate_many() -> None:
        for _ in range(100):
            jinja2.interpolate(data, globals=context, environment=env)

    return [
        ("interpolate nested structure (100)", _measure(interpolate_many, repeat=5)),
        (
            "load_all inline docs with interpolate (5 x 50 docs)",
            _measure(lambda: [list(loader.load_all(docs)) for _ in range(5)], repeat=3),
        ),
    ]


def benchmark_dot_access() -> BenchmarkRows:
    data = DotAccessibleDict(
        {
            "database": {"credentials": {"user": "postgres", "password": "secret"}},
            "database.credentials.literal": "literal",
        }
    )

    def dotted_hit() -> None:
        for _ in range(50_000):
            data.get("database.credentials.user")

    def exact_hit() -> None:
        for _ in range(50_000):
            data.get("database.credentials.literal")

    def miss() -> None:
        for _ in range(50_000):
            data.get("database.credentials.missing", "fallback")

    return [
        ("dotted hit (50k)", _measure(dotted_hit, repeat=5)),
        ("exact dotted-key hit (50k)", _measure(exact_hit, repeat=5)),
        ("dotted miss (50k)", _measure(miss, repeat=5)),
        ("exact key outranks traversal", data.get("database.credentials.literal")),
    ]


def collect_rows(command: str) -> list[tuple[str, BenchmarkRows]]:
    suites = {
        "sources": benchmark_sources,
        "merge": benchmark_merge,
        "jinja": benchmark_jinja,
        "dot": benchmark_dot_access,
    }
    if command == "all":
        return [(name, benchmark()) for name, benchmark in suites.items()]
    return [(command, suites[command]())]


def cli(argv: list[str] | None = None) -> None:
    logging.getLogger("yaconfiglib.utils.source").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description="Run yaconfiglib benchmarks.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["all", "sources", "merge", "jinja", "dot"],
        default="all",
        help="benchmark suite to run",
    )
    parser.add_argument("--markdown", action="store_true", help="print markdown tables")
    args = parser.parse_args(argv)

    for title, rows in collect_rows(args.command):
        _print_rows(title, rows, markdown=args.markdown)


if __name__ == "__main__":
    cli()
