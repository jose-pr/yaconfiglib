import sys
import timeit
from pathlib import Path as StdlibPath

# Add src to sys.path
sys.path.insert(0, str(StdlibPath(__file__).parent.parent / "src"))

from yaconfiglib.utils.source import parse_sources
from yaconfiglib.utils.merge import MergeMethod
from yaconfiglib.loader import ConfigLoader, DotAccessibleDict
from yaconfiglib.utils import jinja2

def benchmark_parse_sources():
    sources = [
        "config.yaml",
        "nested/config.json",
        "exec://echo '{}'",
        "cmd+yaml://echo 'key: value'",
        "another.ini"
    ]
    base_dir = StdlibPath("/tmp")
    
    t = timeit.timeit(
        lambda: list(parse_sources(sources, base_dir=base_dir)),
        number=5000
    )
    return t

def benchmark_has_glob_pattern():
    from yaconfiglib.utils.source import has_glob_pattern
    p1 = StdlibPath("normal/path/to/file.yaml")
    p2 = StdlibPath("glob/path/**/*.yaml")
    
    t = timeit.timeit(
        lambda: (has_glob_pattern(p1), has_glob_pattern(p2)),
        number=20000
    )
    return t

def benchmark_deep_merge():
    a = {
        "a": 1,
        "b": [1, 2, {"x": "y"}],
        "c": {"d": {"e": "f"}, "g": [1, 2, 3]},
        "h": "hello"
    }
    b = {
        "b": [3, 4, {"x": "z", "y": "w"}],
        "c": {"d": {"e": "g", "h": "i"}},
        "i": "world"
    }
    
    t = timeit.timeit(
        lambda: MergeMethod.Deep(a, b, mergelists=True),
        number=2000
    )
    return t

def benchmark_jinja_interpolate():
    data = {
        "user": "{{ env.USER }}",
        "db": {
            "host": "{{ host }}",
            "port": "{{ port }}",
            "url": "postgresql://{{ user }}@{{ host }}:{{ port }}/db"
        },
        "flags": ["{{ flag1 }}", "{{ flag2 }}", "static"]
    }
    context = {
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "flag1": "active",
        "flag2": "debug",
        "env": {"USER": "postgres"}
    }
    
    from jinja2 import Environment
    env = Environment(extensions=["jinja2.ext.do"])
    
    t = timeit.timeit(
        lambda: jinja2.interpolate(data, globals=context, environment=env),
        number=500
    )
    return t

def benchmark_dot_accessible_dict():
    d = DotAccessibleDict({
        "database": {
            "credentials": {
                "user": "postgres"
            }
        }
    })
    
    t = timeit.timeit(
        lambda: d.get("database.credentials.user"),
        number=100000
    )
    return t

def benchmark_load_all_interpolate():
    loader = ConfigLoader(interpolate=True)
    docs = []
    for i in range(50):
        docs.append(f"#!.yaml\nkey_{i}: value_{i}\ninterpolation: '{{{{ key_{i} }}}}'")
        
    t = timeit.timeit(
        lambda: list(loader.load_all(docs)),
        number=20
    )
    return t

def main():
    print("Running benchmarks...")
    
    t_parse = benchmark_parse_sources()
    print(f"1. parse_sources (5k runs): {t_parse:.4f}s")
    
    t_glob = benchmark_has_glob_pattern()
    print(f"2. has_glob_pattern (20k runs): {t_glob:.4f}s")
    
    t_merge = benchmark_deep_merge()
    print(f"3. Deep Merge (2k runs): {t_merge:.4f}s")
    
    t_jinja = benchmark_jinja_interpolate()
    print(f"4. Jinja Interpolate (500 runs): {t_jinja:.4f}s")
    
    t_dot = benchmark_dot_accessible_dict()
    print(f"5. DotAccessibleDict Get (100k runs): {t_dot:.4f}s")
    
    t_load_all = benchmark_load_all_interpolate()
    print(f"6. Loader load_all with interpolate (20 runs x 50 docs): {t_load_all:.4f}s")
    
    # Print Markdown table format for copy-pasting
    print("\n| Benchmark Case | Time |")
    print("|---|---|")
    print(f"| parse_sources (5k) | {t_parse:.4f}s |")
    print(f"| has_glob_pattern (20k) | {t_glob:.4f}s |")
    print(f"| Deep Merge (2k) | {t_merge:.4f}s |")
    print(f"| Jinja Interpolate (500) | {t_jinja:.4f}s |")
    print(f"| DotAccessibleDict Get (100k) | {t_dot:.4f}s |")
    print(f"| Loader load_all (20 runs) | {t_load_all:.4f}s |")

if __name__ == "__main__":
    main()
