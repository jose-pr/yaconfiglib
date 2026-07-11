"""
Tests for ConfigLoader — loading, merging, and example file compatibility.
"""
import pathlib

import pytest

from yaconfiglib import ConfigLoader
from yaconfiglib.loader import ConfigLoaderMergeMethod

EXAMPLES = pathlib.Path(__file__).parent.parent / "examples"


# ---------------------------------------------------------------------------
# Basic loading
# ---------------------------------------------------------------------------

class TestBasicLoading:
    def test_load_yaml(self, tmp_path):
        f = tmp_path / "cfg.yaml"
        f.write_text("key: value\nnumber: 42\n")
        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load("cfg.yaml")
        assert result == {"key": "value", "number": 42}

    def test_load_json(self, tmp_path):
        f = tmp_path / "cfg.json"
        f.write_text('{"a": 1, "b": true}')
        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load("cfg.json")
        assert result == {"a": 1, "b": True}

    def test_load_toml(self, tmp_path):
        f = tmp_path / "cfg.toml"
        f.write_text("[section]\nkey = \"hello\"\n")
        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load("cfg.toml")
        assert result == {"section": {"key": "hello"}}

    def test_load_ini(self, tmp_path):
        f = tmp_path / "cfg.ini"
        f.write_text("[section]\nkey = val\n")
        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load("cfg.ini")
        assert "section" in result

    def test_default_on_missing(self, tmp_path):
        loader = ConfigLoader(base_dir=tmp_path, ignore_error=True)
        result = loader.load("nonexistent.yaml", default={"fallback": True})
        assert result == {"fallback": True}


# ---------------------------------------------------------------------------
# Merge methods
# ---------------------------------------------------------------------------

class TestMergeMethods:
    def test_simple_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("x: 1\ny: 2\n")
        (tmp_path / "b.yaml").write_text("y: 99\nz: 3\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Simple)
        result = loader.load("a.yaml", "b.yaml")
        assert result == {"x": 1, "y": 99, "z": 3}

    def test_deep_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("db:\n  host: localhost\n  port: 5432\n")
        (tmp_path / "b.yaml").write_text("db:\n  port: 5433\n  name: mydb\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Deep)
        result = loader.load("a.yaml", "b.yaml")
        assert result == {"db": {"host": "localhost", "port": 5433, "name": "mydb"}}

    def test_substitute_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("list: [1, 2, 3]\n")
        (tmp_path / "b.yaml").write_text("list: [4, 5]\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Substitute)
        result = loader.load("a.yaml", "b.yaml")
        # Substitute: lists always replace
        assert result["list"] == [4, 5]

    def test_last_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("val: first\n")
        (tmp_path / "b.yaml").write_text("val: second\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Last)
        result = loader.load("a.yaml", "b.yaml")
        assert result == {"val": "second"}

    def test_list_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("val: first\n")
        (tmp_path / "b.yaml").write_text("val: second\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.List)
        result = loader.load("a.yaml", "b.yaml")
        assert isinstance(result, list)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Examples directory
# ---------------------------------------------------------------------------

class TestExamples:
    def test_load_includeme_yaml(self):
        loader = ConfigLoader(base_dir=EXAMPLES)
        result = loader.load("includeme.yaml")
        assert result == {"include": {"me": True}}

    def test_load_hiera_yaml_raw(self):
        """hiera.yaml contains Jinja expressions — load raw (no interpolation)."""
        loader = ConfigLoader(base_dir=EXAMPLES)
        result = loader.load("hiera.yaml")
        # Raw load: values are template strings, not yet rendered
        assert isinstance(result, dict)

    def test_load_settings_json(self):
        loader = ConfigLoader(base_dir=EXAMPLES)
        result = loader.load("settings.json")
        assert isinstance(result, dict)
        assert "python.testing.pytestEnabled" in result

    def test_load_test_ini(self):
        loader = ConfigLoader(base_dir=EXAMPLES)
        result = loader.load("test.ini")
        assert isinstance(result, dict)

    def test_glob_loading(self, tmp_path):
        for name in ("a.yaml", "b.yaml", "c.yaml"):
            (tmp_path / name).write_text(f"file: {name}\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.List)
        result = loader.load("*.yaml")
        assert isinstance(result, list)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# load_all
# ---------------------------------------------------------------------------

class TestLoadAll:
    def test_load_all_yields_each(self, tmp_path):
        (tmp_path / "a.yaml").write_text("a: 1\n")
        (tmp_path / "b.yaml").write_text("b: 2\n")
        loader = ConfigLoader(base_dir=tmp_path)
        results = list(loader.load_all("a.yaml", "b.yaml"))
        assert results == [{"a": 1}, {"b": 2}]


# ---------------------------------------------------------------------------
# DX features (DotAccessibleDict, load_as, Top-level API)
# ---------------------------------------------------------------------------

class TestDXFeatures:
    def test_dot_accessible_dict(self):
        from yaconfiglib.backends.python_backend import PythonBackend
        loader = ConfigLoader()
        result = loader.load(loader=PythonBackend({"db": {"host": "localhost", "port": 3306}}))
        assert result.db.host == "localhost"
        assert result.db.port == 3306
        assert result.get("db.host") == "localhost"
        assert result.get("db.missing", "default") == "default"

    def test_dot_accessible_dict_dig_false(self):
        from yaconfiglib.backends.python_backend import PythonBackend
        loader = ConfigLoader()
        result = loader.load(loader=PythonBackend({"db": {"host": "localhost", "port": 3306}}))
        # dig=False prevents deep traversal
        assert result.get("db.host", "default", dig=False) == "default"

    def test_dot_accessible_dict_exact_match(self):
        from yaconfiglib.backends.python_backend import PythonBackend
        loader = ConfigLoader()
        # Dotted key matches exactly, outranking traversal
        data = {"db.host": "exact-value", "db": {"host": "traversed-value"}}
        result = loader.load(loader=PythonBackend(data))
        assert result.get("db.host") == "exact-value"
        assert result.get("db.host", dig=False) == "exact-value"

    def test_load_as_dataclass(self):
        from dataclasses import dataclass
        from yaconfiglib.backends.python_backend import PythonBackend

        @dataclass
        class MyConfig:
            host: str
            port: int

        loader = ConfigLoader()
        result = loader.load_as(MyConfig, loader=PythonBackend({"host": "localhost", "port": 80, "extra": "ignored"}))
        assert isinstance(result, MyConfig)
        assert result.host == "localhost"
        assert result.port == 80


class TestTopLevelAPI:
    def test_load_file(self, tmp_path):
        from yaconfiglib import load
        f = tmp_path / "conf.json"
        f.write_text('{"foo": "bar"}')
        result = load(str(f))
        assert result == {"foo": "bar"}

    def test_loads_string(self):
        from yaconfiglib import loads
        result = loads('{"hello": "world"}', loader="json")
        assert result == {"hello": "world"}

    def test_dumps_obj(self):
        from yaconfiglib import dumps
        data = {"foo": "bar"}
        result = dumps(data)
        assert "foo: bar" in result

    def test_dump_file_path(self, tmp_path):
        from yaconfiglib import dump, load
        data = {"key": "val"}
        f = tmp_path / "output.yaml"
        dump(data, str(f))
        
        # Load it back to verify
        loaded = load(str(f))
        assert loaded == {"key": "val"}

    def test_dump_file_pointer(self, tmp_path):
        import io
        from yaconfiglib import dump
        data = {"a": 1, "b": 2}
        fp = io.StringIO()
        dump(data, fp)
        content = fp.getvalue()
        assert "a: 1" in content
        assert "b: 2" in content

