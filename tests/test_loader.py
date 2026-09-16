"""
Tests for ConfigLoader — loading, merging, and example file compatibility.
"""

import pathlib
import subprocess
import sys
import typing

import pytest

from yaconfiglib import ConfigLoader
from yaconfiglib.loader import ConfigLoaderMergeMethod
from yaconfiglib.utils.source import parse_sources


# ---------------------------------------------------------------------------
# Basic loading
# ---------------------------------------------------------------------------


class TestBasicLoading:
    @pytest.mark.usefixtures("needs_yaml")
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
        f.write_text('[section]\nkey = "hello"\n')
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
    @pytest.mark.usefixtures("needs_yaml")
    def test_simple_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("x: 1\ny: 2\n")
        (tmp_path / "b.yaml").write_text("y: 99\nz: 3\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Simple)
        result = loader.load("a.yaml", "b.yaml")
        assert result == {"x": 1, "y": 99, "z": 3}

    @pytest.mark.usefixtures("needs_yaml")
    def test_deep_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("db:\n  host: localhost\n  port: 5432\n")
        (tmp_path / "b.yaml").write_text("db:\n  port: 5433\n  name: mydb\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Deep)
        result = loader.load("a.yaml", "b.yaml")
        assert result == {"db": {"host": "localhost", "port": 5433, "name": "mydb"}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_deep_merge_options_reach_loader_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("items:\n  - name: api\n    enabled: false\n")
        (tmp_path / "b.yaml").write_text("items:\n  - name: api\n    enabled: true\n")
        loader = ConfigLoader(
            base_dir=tmp_path,
            merge=ConfigLoaderMergeMethod.Deep,
            merge_options={"mergelists": True},
        )
        result = loader.load("a.yaml", "b.yaml")
        assert result == {"items": [{"name": "api", "enabled": True}]}

    @pytest.mark.usefixtures("needs_yaml")
    def test_substitute_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("list: [1, 2, 3]\n")
        (tmp_path / "b.yaml").write_text("list: [4, 5]\n")
        loader = ConfigLoader(
            base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Substitute
        )
        result = loader.load("a.yaml", "b.yaml")
        # Substitute: lists always replace
        assert result["list"] == [4, 5]

    @pytest.mark.usefixtures("needs_yaml")
    def test_last_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("val: first\n")
        (tmp_path / "b.yaml").write_text("val: second\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Last)
        result = loader.load("a.yaml", "b.yaml")
        assert result == {"val": "second"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_list_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("val: first\n")
        (tmp_path / "b.yaml").write_text("val: second\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.List)
        result = loader.load("a.yaml", "b.yaml")
        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.usefixtures("needs_yaml")
    def test_simple_top_level_list_documents_replace(self, tmp_path):
        (tmp_path / "a.yaml").write_text("- a\n- b\n- c\n", encoding="utf-8")
        (tmp_path / "b.yaml").write_text("- x\n", encoding="utf-8")

        result = ConfigLoader(base_dir=tmp_path).load("a.yaml", "b.yaml")

        assert result == ["x"]

    def test_flatten_scalar_result_raises_clear_error(self):
        from yaconfiglib.backends.python_backend import PythonBackend

        loader = ConfigLoader()
        with pytest.raises(TypeError, match="flatten=True"):
            loader.load(loader=PythonBackend("scalar"), flatten=True)


# ---------------------------------------------------------------------------
# Examples directory
# ---------------------------------------------------------------------------


class TestExamples:
    @pytest.mark.usefixtures("needs_yaml")
    def test_load_includeme_yaml(self, examples_dir):
        loader = ConfigLoader(base_dir=examples_dir)
        result = loader.load("includeme.yaml")
        assert result == {"include": {"me": True}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_hiera_yaml_raw(self, examples_dir):
        """hiera.yaml contains Jinja expressions — load raw (no interpolation)."""
        loader = ConfigLoader(base_dir=examples_dir)
        result = loader.load("hiera.yaml")
        # Raw load: the values are template strings, not yet rendered.
        assert result == {
            "{{ pathname.stem }}": "{{ 10 }}",
            "root2": "{{ dict(d=1) }}",
        }

    def test_load_settings_json(self, examples_dir):
        loader = ConfigLoader(base_dir=examples_dir)
        result = loader.load("settings.json")
        assert isinstance(result, dict)
        assert "python.testing.pytestEnabled" in result

    def test_load_test_ini(self, examples_dir):
        loader = ConfigLoader(base_dir=examples_dir)
        result = loader.load("test.ini")
        assert result["Section"] == {"propa": "b", "propb": "10"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_advanced_example_loads_from_any_cwd(
        self, tmp_path, monkeypatch, examples_dir
    ):
        """The example's includes are relative to advanced.yaml, not to the CWD."""
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader().load(str(examples_dir / "advanced.yaml"))

        assert result["database_config"]["python.testing.pytestEnabled"] is True
        assert result["dynamic_includes"] == {"includeme.yaml": {"me": True}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_glob_loading(self, tmp_path):
        for name in ("a.yaml", "b.yaml", "c.yaml"):
            (tmp_path / name).write_text(f"file: {name}\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.List)
        result = loader.load("*.yaml")
        assert isinstance(result, list)
        assert len(result) == 3

    def test_parse_sources_glob_with_stdlib_base_dir(self, tmp_path):
        for name in ("a.yaml", "b.yaml"):
            (tmp_path / name).write_text(f"file: {name}\n")
        paths = list(parse_sources(["*.yaml"], base_dir=tmp_path))
        assert sorted(path.name for path in paths) == ["a.yaml", "b.yaml"]

    @pytest.mark.usefixtures("needs_yaml")
    def test_duplicate_path_source_loads_once(self, tmp_path):
        (tmp_path / "a.yaml").write_text("file: a.yaml\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.List)
        result = loader.load("a.yaml", "a.yaml")
        assert result == [{"file": "a.yaml"}]

    @pytest.mark.usefixtures("needs_yaml")
    def test_nested_source_iterables_are_flattened(self, tmp_path):
        for name in ("a.yaml", "b.yaml", "c.yaml"):
            (tmp_path / name).write_text(f"file: {name}\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.List)
        result = loader.load(["a.yaml", "b.yaml"], "c.yaml")
        assert [item["file"] for item in result] == ["a.yaml", "b.yaml", "c.yaml"]

    def test_command_source_with_glob_metacharacters_is_not_globbed(self, tmp_path):
        command = "cmd+json://python -c \"print({'items': [1, 2]})\""
        paths = list(parse_sources([command], base_dir=tmp_path))
        assert len(paths) == 1
        assert "cmd+json:" in str(paths[0])
        assert "[1, 2]" in str(paths[0])
        assert str(tmp_path) not in str(paths[0])


# ---------------------------------------------------------------------------
# load_all
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("needs_yaml")
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
        result = loader.load(
            loader=PythonBackend({"db": {"host": "localhost", "port": 3306}})
        )
        assert result.db.host == "localhost"
        assert result.db.port == 3306
        assert result.get("db.host") == "localhost"
        assert result.get("db.missing", "default") == "default"

    def test_dot_accessible_dict_dig_false(self):
        from yaconfiglib.backends.python_backend import PythonBackend

        loader = ConfigLoader()
        result = loader.load(
            loader=PythonBackend({"db": {"host": "localhost", "port": 3306}})
        )
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

    def test_dot_accessible_dict_dotted_get_wraps_nested_dicts(self):
        from yaconfiglib.backends.python_backend import PythonBackend
        from yaconfiglib.loader import DotAccessibleDict

        loader = ConfigLoader()
        result = loader.load(
            loader=PythonBackend({"db": {"credentials": {"user": "postgres"}}})
        )
        assert result.get("db.credentials.user") == "postgres"
        assert isinstance(result["db"], DotAccessibleDict)
        assert isinstance(result["db"]["credentials"], DotAccessibleDict)

    def test_load_as_dataclass(self):
        from dataclasses import dataclass
        from yaconfiglib.backends.python_backend import PythonBackend

        @dataclass
        class MyConfig:
            host: str
            port: int

        loader = ConfigLoader()
        result = loader.load_as(
            MyConfig,
            loader=PythonBackend({"host": "localhost", "port": 80, "extra": "ignored"}),
        )
        assert isinstance(result, MyConfig)
        assert result.host == "localhost"
        assert result.port == 80

    def test_load_as_type_hints_resolve(self):
        hints = typing.get_type_hints(ConfigLoader.load_as)
        assert "model_cls" in hints
        assert "return" in hints


class TestTopLevelAPI:
    def test_loads_forwards_json_decoder_options(self):
        import decimal

        import yaconfiglib

        result = yaconfiglib.loads(
            '{"rate": 1.5}',
            loader="json",
            json_decoder_options={"parse_float": decimal.Decimal},
        )

        assert result["rate"] == decimal.Decimal("1.5")

    def test_load_forwards_ini_default_section(self, tmp_path):
        import yaconfiglib

        config = tmp_path / "cfg.ini"
        config.write_text("[common]\nx = 1\n\n[app]\ny = 2\n", encoding="utf-8")

        result = yaconfiglib.load(str(config), ini_default_section="common")

        assert result["app"]["y"] == "2"

    @pytest.mark.usefixtures("needs_jinja2")
    def test_load_forwards_j2_environment(self, tmp_path):
        from jinja2 import Environment

        import yaconfiglib

        template = tmp_path / "cfg.yaml.j2"
        template.write_text("value: {{ injected }}\n", encoding="utf-8")
        environment = Environment()
        environment.globals["injected"] = "from-env"

        result = yaconfiglib.load(str(template), environment=environment)

        assert result == {"value": "from-env"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_module_load_allow_commands_false_blocks_include(self, tmp_path):
        import yaconfiglib
        from yaconfiglib import CommandsDisabledError

        doc = tmp_path / "main.yaml"
        doc.write_text(
            "x: !include 'cmd+json://python -c \"print(1)\"'\n", encoding="utf-8"
        )

        with pytest.raises(CommandsDisabledError):
            yaconfiglib.load(str(doc), allow_commands=False)

    @pytest.mark.usefixtures("needs_jinja2")
    def test_loads_constructor_only_strict_option(self):
        from jinja2.exceptions import UndefinedError

        import yaconfiglib

        with pytest.raises(UndefinedError):
            yaconfiglib.loads("a: '{{ missing }}'", interpolate=True, strict=True)

    @pytest.mark.usefixtures("needs_yaml")
    def test_module_load_merge_applies_to_include_glob(self, tmp_path):
        import yaconfiglib

        conf_d = tmp_path / "conf.d"
        conf_d.mkdir()
        (conf_d / "a.yaml").write_text("db:\n  host: a\n  port: 1\n", encoding="utf-8")
        (conf_d / "b.yaml").write_text("db:\n  port: 2\n", encoding="utf-8")
        app = tmp_path / "app.yaml"
        app.write_text('conf: !include "conf.d/*.yaml"\n', encoding="utf-8")

        result = yaconfiglib.load(str(app), base_dir=str(tmp_path), merge="deep")

        assert result["conf"]["db"] == {"host": "a", "port": 2}

    @pytest.mark.usefixtures("needs_yaml")
    def test_loads_merge_none_uses_default(self):
        import yaconfiglib

        assert yaconfiglib.loads("a: 1", merge=None) == {"a": 1}

    @pytest.mark.usefixtures("needs_yaml")
    def test_dumps_loaded_config_round_trips(self):
        import yaconfiglib

        original = "a: 1\nb:\n  c: 2\n"
        config = yaconfiglib.loads(original)
        config.b  # wraps the child lazily, which used to get tagged too

        out = yaconfiglib.dumps(config)

        assert "python/" not in out
        assert yaconfiglib.loads(out) == yaconfiglib.loads(original)

    @pytest.mark.usefixtures("needs_yaml")
    def test_dump_file_round_trip_of_loaded_config(self, tmp_path):
        import yaconfiglib

        source = tmp_path / "in.yaml"
        source.write_text("a: 1\nb:\n  c: 2\n", encoding="utf-8")
        target = tmp_path / "out.yaml"

        yaconfiglib.dump(yaconfiglib.load(str(source)), str(target))

        assert yaconfiglib.load(str(target)) == {"a": 1, "b": {"c": 2}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_dumps_keeps_exact_type_representers(self):
        import yaconfiglib

        assert "!!python/tuple" in yaconfiglib.dumps({"t": (1, 2)})

    @pytest.mark.usefixtures("needs_yaml")
    def test_dumps_does_not_modify_global_yaml_dumper(self):
        import yaml

        import yaconfiglib

        yaconfiglib.dumps({"a": 1})

        assert dict not in yaml.Dumper.yaml_multi_representers
        assert dict not in yaml.SafeDumper.yaml_multi_representers

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

    @pytest.mark.usefixtures("needs_yaml")
    def test_dumps_obj(self):
        from yaconfiglib import dumps

        data = {"foo": "bar"}
        result = dumps(data)
        assert "foo: bar" in result

    @pytest.mark.usefixtures("needs_yaml")
    def test_dump_file_path(self, tmp_path):
        from yaconfiglib import dump, load

        data = {"key": "val"}
        f = tmp_path / "output.yaml"
        dump(data, str(f))

        # Load it back to verify
        loaded = load(str(f))
        assert loaded == {"key": "val"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_dump_file_pointer(self, tmp_path):
        import io
        from yaconfiglib import dump

        data = {"a": 1, "b": 2}
        fp = io.StringIO()
        dump(data, fp)
        content = fp.getvalue()
        assert "a: 1" in content
        assert "b: 2" in content


# ---------------------------------------------------------------------------
# Per-call override isolation & global-state hygiene (loader_state fixes)
# ---------------------------------------------------------------------------


class TestLoaderStateHygiene:
    @pytest.mark.usefixtures("needs_yaml")
    def test_merge_options_override_does_not_leak(self, tmp_path):
        (tmp_path / "a.yaml").write_text("items:\n  - 1\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Deep)
        original = loader.merge_options
        loader.load("a.yaml", merge_options={"mergelists": True})
        # The documented "for this call" override must not rewrite instance state.
        assert loader.merge_options is original
        assert loader.merge_options == {}

    def test_construction_does_not_mutate_module_logger(self):
        import logging

        mod_logger = logging.getLogger("yaconfiglib.loader")
        before = mod_logger.level
        with pytest.warns(DeprecationWarning):
            ConfigLoader(log_level=logging.DEBUG)
        ConfigLoader()
        assert mod_logger.level == before

    def test_log_level_is_deprecated_and_accepts_any_int(self):
        # 25 is not a LogLevel member; it used to raise ValueError.
        with pytest.warns(DeprecationWarning, match="no effect"):
            ConfigLoader(log_level=25)

    def test_default_construction_emits_no_deprecation_warning(self):
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ConfigLoader()
        assert [w for w in caught if issubclass(w.category, DeprecationWarning)] == []

    def test_utils_no_longer_exports_get_logger(self):
        from yaconfiglib import utils
        from yaconfiglib.utils import log

        assert not hasattr(utils, "getLogger")
        assert not hasattr(log, "getLogger")

    @pytest.mark.usefixtures("needs_yaml")
    def test_flatten_skips_empty_hash_member(self, tmp_path):
        (tmp_path / "one.yaml").write_text("a: 1\n", encoding="utf-8")
        (tmp_path / "empty.yaml").write_text("", encoding="utf-8")

        result = ConfigLoader(base_dir=tmp_path, merge="hash").load(
            "one.yaml", "empty.yaml", flatten=True
        )

        assert result == {"a": 1}

    @pytest.mark.usefixtures("needs_yaml")
    def test_flatten_skips_empty_list_member(self, tmp_path):
        (tmp_path / "one.yaml").write_text("- 1\n", encoding="utf-8")
        (tmp_path / "empty.yaml").write_text("", encoding="utf-8")

        result = ConfigLoader(base_dir=tmp_path, merge="list").load(
            "one.yaml", "empty.yaml", flatten=True
        )

        assert result == [1]

    @pytest.mark.usefixtures("needs_yaml")
    def test_flatten_non_mapping_member_raises_type_error(self, tmp_path):
        (tmp_path / "one.yaml").write_text("a: 1\n", encoding="utf-8")
        (tmp_path / "scalar.yaml").write_text("42\n", encoding="utf-8")

        with pytest.raises(TypeError, match="scalar"):
            ConfigLoader(base_dir=tmp_path, merge="hash").load(
                "one.yaml", "scalar.yaml", flatten=True
            )


class TestSecurityControls:
    def test_commands_blocked_top_level(self):
        import pytest

        from yaconfiglib import CommandsDisabledError, ConfigLoader

        loader = ConfigLoader(allow_commands=False)
        with pytest.raises(CommandsDisabledError):
            loader.load("cmd://python -c \"print({'a': 1})\"")

    @pytest.mark.usefixtures("needs_yaml")
    def test_commands_blocked_via_include(self, tmp_path):
        import pytest

        from yaconfiglib import CommandsDisabledError, ConfigLoader

        (tmp_path / "main.yaml").write_text(
            "app: !include 'cmd+json://python -c \"import json;"
            " print(json.dumps({}))\"'\n"
        )
        loader = ConfigLoader(base_dir=tmp_path, allow_commands=False)
        with pytest.raises(CommandsDisabledError):
            loader.load("main.yaml")

    @pytest.mark.usefixtures("needs_yaml")
    def test_commands_allowed_by_default(self):
        from yaconfiglib import ConfigLoader

        loader = ConfigLoader()
        assert loader.load("cmd://python -c \"print({'a': 1})\"") == {"a": 1}

    def test_per_call_override_blocks(self):
        import pytest

        from yaconfiglib import CommandsDisabledError, ConfigLoader

        loader = ConfigLoader()  # allowed at instance level
        with pytest.raises(CommandsDisabledError):
            loader.load('cmd://python -c "print(1)"', allow_commands=False)

    @pytest.mark.usefixtures("needs_jinja2")
    def test_sandbox_blocks_ssti(self):
        import pytest
        from jinja2.exceptions import SecurityError

        from yaconfiglib import ConfigLoader

        payload = "#!\nx: \"{{ ''.__class__.__mro__ }}\"\n"
        # The sandbox refuses attribute traversal into Python internals with a
        # Jinja SecurityError specifically (not just any error).
        with pytest.raises(SecurityError):
            ConfigLoader(interpolate=True, sandbox=True).load(payload)

    @pytest.mark.usefixtures("needs_yaml")
    def test_sandbox_allows_ordinary_interpolation(self):
        from yaconfiglib import ConfigLoader

        payload = '#!\ngreeting: "hello {{ name }}"\nname: world\n'
        result = ConfigLoader(interpolate=True, sandbox=True).load(payload)
        assert result["greeting"] == "hello world"

    @pytest.mark.usefixtures("needs_yaml")
    def test_sandbox_allows_pure_expression_interpolation(self):
        # Regression: a BARE `{{ expr }}` value routes through the
        # type-preserving eval() path, whose result capture used to be
        # `_meta.__setitem__(...)`. The sandbox rejects underscore ATTRIBUTES,
        # so the capture — not the user's expression — raised SecurityError,
        # breaking every pure-expression value under sandbox=True. The
        # mixed-text test above only covers the compile() path, which is why a
        # green suite hid this.
        from yaconfiglib import ConfigLoader

        payload = '#!\nname: world\ngreeting: "{{ name }}"\n'
        result = ConfigLoader(interpolate=True, sandbox=True).load(payload)
        assert result["greeting"] == "world"

    @pytest.mark.usefixtures("needs_yaml")
    def test_sandbox_pure_expression_preserves_type(self):
        # The eval() path exists to keep non-string types; verify it still does
        # so inside the sandbox rather than degrading to a rendered string.
        from yaconfiglib import ConfigLoader

        payload = '#!\nbase: 21\ndoubled: "{{ base * 2 }}"\n'
        result = ConfigLoader(interpolate=True, sandbox=True).load(payload)
        assert result["doubled"] == 42
        assert isinstance(result["doubled"], int)


# ---------------------------------------------------------------------------
# Coverage gaps: ignore_error predicate, Hash merge, override isolation
# ---------------------------------------------------------------------------


class TestIncludeTrustPolicy:
    """Documents and nested includes can only narrow the caller's trust settings."""

    SSTI_TEXT = "{{ ''.__class__.__mro__ }}"
    SSTI_EXPR = "''.__class__.__mro__"

    @staticmethod
    def _command(tmp_path):
        """Return (cmd+json source that touches a marker file, marker path)."""
        marker = tmp_path / "ran.marker"
        script = tmp_path / "mk.py"
        script.write_text(
            f"import pathlib\npathlib.Path({marker.as_posix()!r}).touch()\nprint('{{}}')\n",
            encoding="utf-8",
        )
        return f"cmd+json://python {script.as_posix()}", marker

    @staticmethod
    def _write(path, text):
        path.write_text(text, encoding="utf-8")
        return path

    @pytest.mark.usefixtures("needs_yaml")
    def test_mapping_cannot_reenable_commands_under_checklist(self, tmp_path):
        import yaconfiglib
        from yaconfiglib import CommandsDisabledError

        cmd, marker = self._command(tmp_path)
        doc = self._write(
            tmp_path / "u.yaml",
            f'x: !include {{pathname: "{cmd}", allow_commands: true}}\n',
        )
        with pytest.raises(CommandsDisabledError):
            yaconfiglib.load(
                str(doc),
                allow_commands=False,
                interpolate=True,
                sandbox=True,
                loader="yaml",
            )
        assert not marker.exists()

    @pytest.mark.usefixtures("needs_jinja2")
    def test_mapping_cannot_disable_sandbox(self, tmp_path):
        from jinja2.exceptions import SecurityError

        child = self._write(tmp_path / "child.yaml", f'y: "{self.SSTI_TEXT}"\n')
        doc = self._write(
            tmp_path / "u.yaml",
            f'x: !include {{pathname: "{child.as_posix()}", sandbox: false,'
            " interpolate: true}\n",
        )
        with pytest.raises(SecurityError):
            ConfigLoader(interpolate=True, sandbox=True).load(str(doc))

    @pytest.mark.usefixtures("needs_jinja2")
    def test_mapping_transform_is_sandboxed(self, tmp_path):
        from jinja2.exceptions import SecurityError

        child = self._write(tmp_path / "child.yaml", "a: 1\n")
        doc = self._write(
            tmp_path / "u.yaml",
            f'x: !include {{pathname: "{child.as_posix()}",'
            f' transform: "{self.SSTI_EXPR}"}}\n',
        )
        with pytest.raises(SecurityError):
            ConfigLoader(sandbox=True).load(str(doc))

    @pytest.mark.usefixtures("needs_jinja2")
    def test_mapping_key_factory_expression_is_sandboxed(self, tmp_path):
        from jinja2.exceptions import SecurityError

        child = self._write(tmp_path / "child.yaml", "a: 1\n")
        doc = self._write(
            tmp_path / "u.yaml",
            f'x: !include {{pathname: "{child.as_posix()}",'
            f' key_factory: "%{self.SSTI_EXPR}"}}\n',
        )
        with pytest.raises(SecurityError):
            ConfigLoader(sandbox=True).load(str(doc))

    @pytest.mark.usefixtures("needs_jinja2")
    def test_mapping_transform_sandboxed_when_commands_disabled(self, tmp_path):
        from jinja2.exceptions import SecurityError

        child = self._write(tmp_path / "child.yaml", "a: 1\n")
        doc = self._write(
            tmp_path / "u.yaml",
            f'x: !include {{pathname: "{child.as_posix()}",'
            f' transform: "{self.SSTI_EXPR}"}}\n',
        )
        with pytest.raises(SecurityError):
            ConfigLoader(allow_commands=False).load(str(doc))

    @pytest.mark.usefixtures("needs_yaml")
    def test_mapping_plain_key_factory_is_dropped(self, tmp_path, caplog):
        import logging

        import yaconfiglib

        victim = self._write(tmp_path / "victim.yaml", "a: 1\n")
        doc = self._write(
            tmp_path / "u.yaml",
            f'x: !include {{pathname: "{victim.as_posix()}", key_factory: unlink}}\n',
        )
        with caplog.at_level(logging.WARNING):
            result = yaconfiglib.load(
                str(doc),
                allow_commands=False,
                interpolate=True,
                sandbox=True,
                loader="yaml",
            )
        assert victim.exists()
        assert result == {"x": {"a": 1}}
        assert any("key_factory" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.usefixtures("needs_yaml")
    def test_per_call_allow_commands_reaches_scalar_include(self, tmp_path):
        from yaconfiglib import CommandsDisabledError

        cmd, marker = self._command(tmp_path)
        doc = self._write(tmp_path / "u.yaml", f"x: !include '{cmd}'\n")
        with pytest.raises(CommandsDisabledError):
            ConfigLoader().load(str(doc), allow_commands=False)
        assert not marker.exists()

    @pytest.mark.usefixtures("needs_jinja2")
    def test_per_call_sandbox_reaches_scalar_include(self, tmp_path):
        from jinja2.exceptions import SecurityError

        child = self._write(tmp_path / "child.yaml", f'y: "{self.SSTI_TEXT}"\n')
        doc = self._write(tmp_path / "u.yaml", f"x: !include '{child.as_posix()}'\n")
        with pytest.raises(SecurityError):
            ConfigLoader(interpolate=True).load(str(doc), sandbox=True)

    @pytest.mark.usefixtures("needs_yaml")
    def test_hand_registered_tag_constructor_filters_mapping(self, tmp_path):
        import yaml

        from yaconfiglib import CommandsDisabledError

        cmd, marker = self._command(tmp_path)

        class _HandLoader(yaml.SafeLoader):
            pass

        _HandLoader.add_constructor("!include", ConfigLoader(allow_commands=False))
        with pytest.raises(CommandsDisabledError):
            yaml.load(
                f'x: !include {{pathname: "{cmd}", allow_commands: true}}',
                Loader=_HandLoader,
            )
        assert not marker.exists()

    def test_use_policy_only_tightens(self):
        from yaconfiglib.utils.trust import current_policy, use_policy

        with use_policy(False, True, False):
            with use_policy(True, False, False) as effective:
                assert effective == (False, True, False)
                assert current_policy() == (False, True, False)
        assert current_policy() == (True, False, False)

    @pytest.mark.usefixtures("needs_yaml")
    def test_documented_transform_and_key_factory_work_sandboxed(self, tmp_path):
        child = self._write(tmp_path / "child.yaml", "include:\n  me: true\n")
        doc = self._write(
            tmp_path / "u.yaml",
            f'x: !include {{pathname: "{child.as_posix()}",'
            ' transform: "{ pathname.name: value.include }",'
            ' key_factory: "%pathname.as_posix()"}\n',
        )
        result = ConfigLoader(sandbox=True).load(str(doc))
        assert result == {"x": {"child.yaml": {"me": True}}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_mapping_loader_command_still_blocked(self, tmp_path):
        from yaconfiglib import CommandsDisabledError

        cmd, marker = self._command(tmp_path)
        doc = self._write(
            tmp_path / "u.yaml", f'x: !include {{pathname: "{cmd}", loader: command}}\n'
        )
        with pytest.raises(CommandsDisabledError):
            ConfigLoader(allow_commands=False).load(str(doc))
        assert not marker.exists()

    @pytest.mark.usefixtures("needs_yaml")
    def test_advanced_example_includes_still_load(self, monkeypatch):
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        monkeypatch.chdir(repo_root)
        result = ConfigLoader().load("examples/advanced.yaml")
        assert result["dynamic_includes"] == {"includeme.yaml": {"me": True}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_sequence_include_form(self, tmp_path):
        child = self._write(tmp_path / "child.yaml", "b: 2\n")
        doc = self._write(tmp_path / "u.yaml", f'x: !include ["{child.as_posix()}"]\n')
        assert ConfigLoader().load(str(doc)) == {"x": {"b": 2}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_caller_transform_still_applies(self, tmp_path):
        doc = self._write(tmp_path / "u.yaml", "a: 5\n")
        assert ConfigLoader().load(str(doc), transform="value.a") == 5

    def test_hardened_policy_without_jinja2(self, monkeypatch):
        monkeypatch.setattr("yaconfiglib.loader.jinja2", None)
        result = ConfigLoader(allow_commands=False, sandbox=True).load(
            '#!a.json\n{"a": 1}'
        )
        assert result == {"a": 1}


class TestUntrustedRobustness:
    """A config author must not be able to hang, exhaust or mutate the host."""

    @pytest.mark.usefixtures("needs_yaml")
    def test_aliased_yaml_interpolates_in_linear_time(self):
        import time

        # 7 levels, each a list of 9 aliases of the previous: ~3.7s when every
        # reference is walked, well under 0.1s when shared containers are walked once.
        lines = ['l0: &l0 ["{{ 1 + 1 }}", "plain"]']
        for level in range(1, 7):
            aliases = ", ".join([f"*l{level - 1}"] * 9)
            lines.append(f"l{level}: &l{level} [{aliases}]")
        lines.append("top: *l6")
        source = "#!aliases.yaml\n" + "\n".join(lines) + "\n"

        started = time.perf_counter()
        result = ConfigLoader(interpolate=True, sandbox=True).load(source)
        elapsed = time.perf_counter() - started

        assert elapsed < 1, f"interpolation took {elapsed:.1f}s"
        assert result["top"][0] is result["top"][1]
        assert result["l0"] == [2, "plain"]

    def test_command_runs_with_stdin_closed(self):
        import subprocess
        import sys

        import yaconfiglib

        # The child must import the same yaconfiglib this test process imported,
        # not whatever the interpreter's installed copy resolves to.
        package_root = str(pathlib.Path(yaconfiglib.__file__).resolve().parent.parent)
        inner = (
            f'cmd+json://"{sys.executable}" -c "import sys; sys.stdin.read(); print(1)"'
        )
        code = (
            f"import sys\nsys.path.insert(0, {package_root!r})\n"
            "from yaconfiglib import ConfigLoader\n"
            f"print(ConfigLoader().load({inner!r}))\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            returncode = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("the command waited on the parent's stdin")
        finally:
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()
        assert returncode == 0

    def test_command_timeout_kills_the_command(self):
        import subprocess
        import sys
        import time

        started = time.perf_counter()
        with pytest.raises(subprocess.TimeoutExpired):
            ConfigLoader().load(
                f'cmd://"{sys.executable}" -c "import time; time.sleep(15)"',
                timeout=1,
            )
        assert time.perf_counter() - started < 10

    @pytest.mark.usefixtures("needs_jinja2")
    def test_inject_env_is_read_only(self, monkeypatch):
        import os

        from jinja2.exceptions import UndefinedError

        monkeypatch.delenv("YACFG_PWN", raising=False)
        source = "#!\nv: \"{% do env.update({'YACFG_PWN': 'x'}) %}ok\"\n"
        with pytest.raises(UndefinedError):
            ConfigLoader(interpolate=True, inject_env=True, sandbox=True).load(source)
        assert "YACFG_PWN" not in os.environ

    @pytest.mark.usefixtures("needs_yaml")
    def test_include_cycle_is_a_clear_error(self, tmp_path):
        (tmp_path / "a.yaml").write_text("x: !include b.yaml\n", encoding="utf-8")
        (tmp_path / "b.yaml").write_text("y: !include a.yaml\n", encoding="utf-8")
        with pytest.raises(ValueError, match="include cycle"):
            ConfigLoader(base_dir=tmp_path).load("a.yaml")

    @pytest.mark.usefixtures("needs_jinja2")
    def test_load_all_per_call_sandbox(self, tmp_path):
        from jinja2.exceptions import SecurityError

        doc = tmp_path / "s.yaml"
        doc.write_text("x: \"{{ ''.__class__.__mro__ }}\"\n", encoding="utf-8")
        with pytest.raises(SecurityError):
            list(ConfigLoader(interpolate=True).load_all(str(doc), sandbox=True))

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_all_per_call_allow_commands_reaches_includes(self, tmp_path):
        from yaconfiglib import CommandsDisabledError

        marker = tmp_path / "ran.marker"
        script = tmp_path / "mk.py"
        script.write_text(
            f"import pathlib\npathlib.Path({marker.as_posix()!r}).touch()\nprint('{{}}')\n",
            encoding="utf-8",
        )
        doc = tmp_path / "u.yaml"
        doc.write_text(
            f"x: !include 'cmd+json://python {script.as_posix()}'\n", encoding="utf-8"
        )
        with pytest.raises(CommandsDisabledError):
            list(ConfigLoader().load_all(str(doc), allow_commands=False))
        assert not marker.exists()

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_all_policy_does_not_leak_into_the_loop(self, tmp_path):
        from yaconfiglib.utils.trust import current_policy

        first = tmp_path / "one.yaml"
        first.write_text("a: 1\n", encoding="utf-8")
        second = tmp_path / "two.yaml"
        second.write_text("b: 2\n", encoding="utf-8")
        seen = []
        for _document in ConfigLoader().load_all(
            str(first), str(second), allow_commands=False
        ):
            seen.append(current_policy())
        assert seen == [(True, False, False), (True, False, False)]


@pytest.mark.usefixtures("needs_yaml")
class TestInterpolationScope:
    """interpolate=True renders once, after merging, in the merged document's scope."""

    @staticmethod
    def _write(tmp_path, name, text):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_include_sees_parent_scope_with_instance_interpolate(self, tmp_path):
        self._write(tmp_path, "svc.yaml", 'url: "http://{{ host }}/api"\n')
        self._write(tmp_path, "app.yaml", "host: example.com\nsvc: !include svc.yaml\n")

        result = ConfigLoader(base_dir=tmp_path, interpolate=True).load("app.yaml")

        assert result["svc"]["url"] == "http://example.com/api"

    def test_include_parent_reference_under_strict_does_not_raise(self, tmp_path):
        self._write(tmp_path, "svc.yaml", 'url: "http://{{ host }}/api"\n')
        self._write(tmp_path, "app.yaml", "host: example.com\nsvc: !include svc.yaml\n")

        result = ConfigLoader(base_dir=tmp_path, interpolate=True, strict=True).load(
            "app.yaml"
        )

        assert result["svc"]["url"] == "http://example.com/api"

    def test_escaped_literal_in_include_rendered_once(self, tmp_path):
        self._write(tmp_path, "inc.yaml", "lit: \"{{ '{{ x }}' }}\"\n")
        self._write(tmp_path, "app.yaml", "x: SURPRISE\ninc: !include inc.yaml\n")

        result = ConfigLoader(base_dir=tmp_path, interpolate=True).load("app.yaml")

        assert result["inc"]["lit"] == "{{ x }}"

    def test_chained_references_resolve(self, tmp_path):
        self._write(
            tmp_path,
            "app.yaml",
            'base: /srv\nlogs: "{{ base }}/logs"\nerr: "{{ logs }}/err"\n',
        )

        result = ConfigLoader(base_dir=tmp_path, interpolate=True).load("app.yaml")

        assert result["err"] == "/srv/logs/err"

    def test_chained_references_are_order_independent(self, tmp_path):
        self._write(
            tmp_path,
            "app.yaml",
            'err: "{{ logs }}/err"\nlogs: "{{ base }}/logs"\nbase: /srv\n',
        )

        result = ConfigLoader(base_dir=tmp_path, interpolate=True).load("app.yaml")

        assert result["err"] == "/srv/logs/err"

    def test_nested_reference_before_its_mapping_resolves(self, tmp_path):
        self._write(
            tmp_path,
            "app.yaml",
            'full: "{{ db.url }}/p"\ndb:\n  host: h\n  url: "x://{{ db.host }}"\n',
        )

        result = ConfigLoader(base_dir=tmp_path, interpolate=True).load("app.yaml")

        assert result["full"] == "x://h/p"

    def test_cross_key_cycle_raises_under_strict(self, tmp_path):
        self._write(tmp_path, "app.yaml", 'a: "{{ b }}"\nb: "{{ a }}"\n')

        with pytest.raises(ValueError, match="interpolation reference cycle"):
            ConfigLoader(base_dir=tmp_path, interpolate=True, strict=True).load(
                "app.yaml"
            )

    def test_cross_key_cycle_without_strict_does_not_raise(self, tmp_path):
        self._write(tmp_path, "app.yaml", 'a: "{{ b }}"\nb: "{{ a }}"\n')

        ConfigLoader(base_dir=tmp_path, interpolate=True).load("app.yaml")

    def test_load_all_resolves_chained_references(self, tmp_path):
        self._write(
            tmp_path,
            "app.yaml",
            'base: /srv\nlogs: "{{ base }}/logs"\nerr: "{{ logs }}/err"\n',
        )

        documents = list(
            ConfigLoader(base_dir=tmp_path, interpolate=True).load_all("app.yaml")
        )

        assert documents[0]["err"] == "/srv/logs/err"

    def test_escaped_literal_referenced_by_other_key_not_rerendered(self, tmp_path):
        self._write(
            tmp_path,
            "app.yaml",
            'x: SURPRISE\nlit: "{{ \'{{ x }}\' }}"\nref: "{{ lit }}"\n',
        )

        result = ConfigLoader(base_dir=tmp_path, interpolate=True).load("app.yaml")

        assert result["lit"] == "{{ x }}"
        assert result["ref"] == "{{ x }}"

    def test_inject_env_global_wins_over_document_env_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("YACFG_SCOPE_VAR", "from-environment")
        self._write(tmp_path, "app.yaml", 'env: doc\nv: "{{ env.YACFG_SCOPE_VAR }}"\n')

        result = ConfigLoader(
            base_dir=tmp_path, interpolate=True, inject_env=True
        ).load("app.yaml")

        assert result["v"] == "from-environment"


class TestLoadAs:
    """yaconfiglib.load_as and the hydration branches behind it."""

    @staticmethod
    def _fake_pydantic(monkeypatch, version):
        """Install a fake pydantic module and return its BaseModel."""
        import sys
        import types as _types

        module = _types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **values):
                self.values = values

            if version == "v2":

                @classmethod
                def model_validate(cls, data):
                    built = cls(**data)
                    built.built_with = "model_validate"
                    return built

            else:

                @classmethod
                def parse_obj(cls, data):
                    built = cls(**data)
                    built.built_with = "parse_obj"
                    return built

        module.BaseModel = BaseModel
        monkeypatch.setitem(sys.modules, "pydantic", module)
        return BaseModel

    @pytest.mark.usefixtures("needs_yaml")
    def test_top_level_load_as_dataclass(self, tmp_path):
        import dataclasses

        import yaconfiglib

        @dataclasses.dataclass
        class DB:
            host: str
            port: int = 5432

        (tmp_path / "db.yaml").write_text("host: h\nport: 6\n", encoding="utf-8")

        result = yaconfiglib.load_as(DB, str(tmp_path / "db.yaml"))

        assert result == DB(host="h", port=6)

    @pytest.mark.usefixtures("needs_yaml")
    def test_top_level_load_as_multiple_sources_deep_merge(self, tmp_path):
        import dataclasses

        import yaconfiglib

        @dataclasses.dataclass
        class DB:
            host: str
            port: int = 5432

        (tmp_path / "a.yaml").write_text("host: a\nport: 1\n", encoding="utf-8")
        (tmp_path / "b.yaml").write_text("port: 2\n", encoding="utf-8")

        result = yaconfiglib.load_as(
            DB, str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml"), merge="deep"
        )

        assert result == DB(host="a", port=2)

    @pytest.mark.usefixtures("needs_jinja2")
    def test_top_level_load_as_constructor_option_strict(self, tmp_path):
        import dataclasses

        from jinja2.exceptions import UndefinedError

        import yaconfiglib

        @dataclasses.dataclass
        class DB:
            host: str = ""

        (tmp_path / "db.yaml").write_text('host: "{{ missing }}"\n', encoding="utf-8")

        with pytest.raises(UndefinedError):
            yaconfiglib.load_as(
                DB, str(tmp_path / "db.yaml"), interpolate=True, strict=True
            )

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_as_pydantic_v2_via_fake_module(self, tmp_path, monkeypatch):
        base_model = self._fake_pydantic(monkeypatch, "v2")

        class Settings(base_model):
            pass

        (tmp_path / "s.yaml").write_text("a: 1\n", encoding="utf-8")

        result = ConfigLoader(base_dir=tmp_path).load_as(Settings, "s.yaml")

        assert result.built_with == "model_validate"
        assert result.values == {"a": 1}

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_as_pydantic_v1_via_fake_module(self, tmp_path, monkeypatch):
        base_model = self._fake_pydantic(monkeypatch, "v1")

        class Settings(base_model):
            pass

        (tmp_path / "s.yaml").write_text("a: 1\n", encoding="utf-8")

        result = ConfigLoader(base_dir=tmp_path).load_as(Settings, "s.yaml")

        assert result.built_with == "parse_obj"

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_as_plain_class_fallback(self, tmp_path):
        class Plain:
            def __init__(self, a):
                self.a = a

        (tmp_path / "p.yaml").write_text("a: 7\n", encoding="utf-8")

        result = ConfigLoader(base_dir=tmp_path).load_as(Plain, "p.yaml")

        assert result.a == 7

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_as_non_dict_raises_type_error(self, tmp_path):
        class Plain:
            pass

        (tmp_path / "list.yaml").write_text("- 1\n- 2\n", encoding="utf-8")

        with pytest.raises(TypeError, match="must be a dictionary"):
            ConfigLoader(base_dir=tmp_path).load_as(Plain, "list.yaml")

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_as_dataclass_ignores_self_key(self, tmp_path):
        import dataclasses

        import yaconfiglib

        @dataclasses.dataclass
        class DB:
            host: str

        (tmp_path / "db.yaml").write_text("self: oops\nhost: h\n", encoding="utf-8")

        result = yaconfiglib.load_as(DB, str(tmp_path / "db.yaml"))

        assert result == DB(host="h")

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_as_nested_dataclass_field_is_hydrated(self, tmp_path):
        import dataclasses

        import yaconfiglib

        @dataclasses.dataclass
        class DB:
            host: str

        @dataclasses.dataclass
        class App:
            name: str
            db: DB

        (tmp_path / "app.yaml").write_text(
            "name: svc\ndb:\n  host: h\n", encoding="utf-8"
        )

        result = yaconfiglib.load_as(App, str(tmp_path / "app.yaml"))

        assert result.db == DB(host="h")

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_as_optional_nested_dataclass_none_is_kept(self, tmp_path):
        import dataclasses
        import typing as t

        @dataclasses.dataclass
        class DB:
            host: str

        @dataclasses.dataclass
        class App:
            db: t.Optional[DB] = None

        (tmp_path / "app.yaml").write_text("db: null\n", encoding="utf-8")

        result = ConfigLoader(base_dir=tmp_path).load_as(App, "app.yaml")

        assert result.db is None

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_as_does_not_import_pydantic(self, tmp_path, monkeypatch):
        import dataclasses
        import sys

        import yaconfiglib

        @dataclasses.dataclass
        class DB:
            host: str

        looked_up = []

        class _Recorder:
            def find_module(self, name, path=None):  # pragma: no cover - py<3.12 shim
                looked_up.append(name)
                return None

            def find_spec(self, name, path=None, target=None):
                looked_up.append(name)
                return None

        monkeypatch.delitem(sys.modules, "pydantic", raising=False)
        monkeypatch.setattr(sys, "meta_path", [_Recorder()] + list(sys.meta_path))
        (tmp_path / "db.yaml").write_text("host: h\n", encoding="utf-8")

        yaconfiglib.load_as(DB, str(tmp_path / "db.yaml"))

        assert "pydantic" not in looked_up

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_as_keeps_initvar_parameter(self, tmp_path):
        import dataclasses

        @dataclasses.dataclass
        class DC:
            host: str
            seed: dataclasses.InitVar[int] = 0
            derived: int = dataclasses.field(init=False, default=0)

            def __post_init__(self, seed):
                self.derived = seed * 2

        (tmp_path / "dc.yaml").write_text("host: h\nseed: 21\n", encoding="utf-8")

        result = ConfigLoader(base_dir=tmp_path).load_as(DC, "dc.yaml")

        assert result.derived == 42


@pytest.mark.usefixtures("needs_yaml")
class TestMergeAnchors:
    """Overriding a key must not rewrite what an anchor shares with it."""

    @pytest.mark.parametrize("strategy", ["deep", "substitute"])
    def test_merge_key_override_does_not_leak(self, tmp_path, strategy):
        # `<<:` copies only top-level keys, so both environments share one `db`
        # mapping. An override layer must not rewrite it for everyone.
        (tmp_path / "app.yaml").write_text(
            "defaults: &defaults\n"
            "  db:\n"
            "    host: shared\n"
            "    pool: 5\n"
            "  hosts: [a]\n"
            "development:\n"
            "  <<: *defaults\n"
            "production:\n"
            "  <<: *defaults\n",
            encoding="utf-8",
        )
        (tmp_path / "override.yaml").write_text(
            "production:\n  db:\n    host: prod-db\n  hosts: [b]\n", encoding="utf-8"
        )

        result = ConfigLoader(base_dir=tmp_path, merge=strategy).load(
            "app.yaml", "override.yaml"
        )

        assert result["production"]["db"]["host"] == "prod-db"
        assert result["development"]["db"]["host"] == "shared"
        assert result["defaults"]["db"]["host"] == "shared"
        assert result["development"]["hosts"] == ["a"]

    @pytest.mark.parametrize("strategy", ["deep", "substitute"])
    def test_later_layer_does_not_rewrite_earlier_layer_anchor(
        self, tmp_path, strategy
    ):
        (tmp_path / "base.yaml").write_text("v: 0\n", encoding="utf-8")
        (tmp_path / "mid.yaml").write_text(
            "common: &c\n  x: 1\nb: *c\n", encoding="utf-8"
        )
        (tmp_path / "top.yaml").write_text("b:\n  x: 2\n", encoding="utf-8")

        result = ConfigLoader(base_dir=tmp_path, merge=strategy).load(
            "base.yaml", "mid.yaml", "top.yaml"
        )

        assert result["b"]["x"] == 2
        assert result["common"]["x"] == 1


class TestMergeLeafTypes:
    """Ordinary YAML/TOML dates and datetimes merge like any other value."""

    @pytest.mark.parametrize("strategy", ["deep", "substitute"])
    @pytest.mark.usefixtures("needs_yaml")
    def test_yaml_dates_merge(self, tmp_path, strategy):
        import datetime

        (tmp_path / "a.yaml").write_text(
            "name: app\nrelease: 2024-01-01\nholidays:\n  - 2024-12-25\n",
            encoding="utf-8",
        )
        (tmp_path / "b.yaml").write_text(
            "owner: team\nrelease: 2025-06-01\nholidays:\n  - 2025-12-25\n",
            encoding="utf-8",
        )

        result = ConfigLoader(base_dir=tmp_path, merge=strategy).load(
            "a.yaml", "b.yaml"
        )

        assert result["name"] == "app"
        assert result["owner"] == "team"
        assert result["release"] == datetime.date(2025, 6, 1)
        if strategy == "deep":
            assert result["holidays"] == [
                datetime.date(2024, 12, 25),
                datetime.date(2025, 12, 25),
            ]
        else:
            assert result["holidays"] == [datetime.date(2025, 12, 25)]

    def test_toml_datetimes_merge(self, tmp_path):
        (tmp_path / "a.toml").write_text(
            'name = "app"\nwhen = 2024-01-01T10:00:00Z\n', encoding="utf-8"
        )
        (tmp_path / "b.toml").write_text(
            "when = 2025-06-01T11:30:00Z\n", encoding="utf-8"
        )

        result = ConfigLoader(base_dir=tmp_path, merge="deep").load("a.toml", "b.toml")

        # Compare the date parts: the tzinfo class differs between tomllib and
        # the toml package.
        assert (result["when"].year, result["when"].month, result["when"].day) == (
            2025,
            6,
            1,
        )
        assert result["name"] == "app"


class TestMergeExtensionHooks:
    """The merge extension points stay usable for custom and extended strategies."""

    @pytest.mark.usefixtures("needs_yaml")
    def test_custom_merge_init_errors_propagate(self):
        class BrokenInit:
            def init(self, initial, configloaderkey, **options):
                return initial.missing_attr

            def __call__(self, a, b, **options):
                return b

        with pytest.raises(AttributeError):
            ConfigLoader(merge=BrokenInit()).load("#!a.yaml\na: 1\n")

    @pytest.mark.usefixtures("needs_yaml")
    def test_merge_without_init_uses_first_document(self):
        from yaconfiglib.utils.merge import MergeMethod

        # utils.merge.MergeMethod has no init(); the first document seeds the result.
        result = ConfigLoader(merge=MergeMethod.Deep).load(
            "#!a.yaml\na: 1\n", "#!b.yaml\nb: 2\n"
        )

        assert result == {"a": 1, "b": 2}

    @pytest.mark.parametrize("member", ["List", "Hash"])
    @pytest.mark.usefixtures("needs_yaml")
    def test_extended_enum_keeps_builtin_init(self, member):
        from yaconfiglib.loader import ConfigLoaderMergeMethod
        from yaconfiglib.utils.enum import IntEnum

        class _Extra(IntEnum):
            Mine = 99

        extended = ConfigLoaderMergeMethod.extend(_Extra, name="ExtendedMerge")
        sources = ("#!a.yaml\na: 1\n", "#!b.yaml\nb: 2\n")

        stock = ConfigLoader(merge=getattr(ConfigLoaderMergeMethod, member)).load(
            *sources
        )
        from_extension = ConfigLoader(merge=getattr(extended, member)).load(*sources)

        assert from_extension == stock

    def test_config_loader_merge_method_members_pickle(self):
        import pickle

        from yaconfiglib.loader import ConfigLoaderMergeMethod

        for member in ConfigLoaderMergeMethod:
            assert pickle.loads(pickle.dumps(member)) is member

    @pytest.mark.usefixtures("needs_yaml")
    def test_default_returned_only_when_nothing_loads(self, tmp_path):
        (tmp_path / "one.yaml").write_text("a: 1\n", encoding="utf-8")
        loader = ConfigLoader(base_dir=tmp_path)

        loaded = loader.load("one.yaml", default={"fallback": True})
        nothing = loader.load("no-such-*.yaml", default={"fallback": True})

        assert "fallback" not in loaded
        assert nothing == {"fallback": True}

    def test_extend_type_hints_resolve(self):
        import typing as t

        from yaconfiglib.utils.enum import IntEnum

        # Introspection tools (and mkdocstrings) resolve hints eagerly; on the
        # 3.9 floor a PEP 604 union or typing.Self here would raise.
        assert t.get_type_hints(IntEnum.extend)


@pytest.mark.usefixtures("needs_yaml")
class TestIgnoreErrorPredicate:
    def test_predicate_skips_only_selected_errors(self, tmp_path):
        (tmp_path / "good.yaml").write_text("x: 1\n")
        (tmp_path / "bad.yaml").write_text("x: [unclosed\n")  # malformed YAML

        # Skip a missing file, but let a real parse error propagate.
        def only_missing(error, **ctx):
            return isinstance(error, FileNotFoundError)

        loader = ConfigLoader(base_dir=tmp_path, ignore_error=only_missing)

        # Missing file is skipped -> the good file still loads.
        result = loader.load("nope.yaml", "good.yaml")
        assert result == {"x": 1}

        # A malformed file raises (predicate returns False for a YAML error).
        import yaml

        with pytest.raises(yaml.YAMLError):
            loader.load("bad.yaml")


class TestHashMerge:
    def _files(self, tmp_path):
        (tmp_path / "a.yaml").write_text("v: 1\n")
        (tmp_path / "b.yaml").write_text("v: 2\n")

    @pytest.mark.usefixtures("needs_yaml")
    def test_hash_default_key_is_stem(self, tmp_path):
        self._files(tmp_path)
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Hash)
        result = loader.load("a.yaml", "b.yaml")
        assert result == {"a": {"v": 1}, "b": {"v": 2}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_hash_attribute_key_factory(self, tmp_path):
        self._files(tmp_path)
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Hash)
        result = loader.load("a.yaml", "b.yaml", key_factory="name")
        assert set(result.keys()) == {"a.yaml", "b.yaml"}

    @pytest.mark.usefixtures("needs_jinja2")
    def test_hash_jinja_key_factory(self, tmp_path):
        self._files(tmp_path)
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Hash)
        result = loader.load("a.yaml", "b.yaml", key_factory="%pathname.stem ~ '!'")
        assert set(result.keys()) == {"a!", "b!"}


@pytest.mark.usefixtures("needs_yaml")
class TestPerCallOverrideIsolation:
    def test_overrides_do_not_leak_into_instance(self, tmp_path):
        (tmp_path / "a.yaml").write_text("x: 1\n")
        loader = ConfigLoader(
            base_dir=tmp_path,
            merge=ConfigLoaderMergeMethod.Simple,
            interpolate=False,
        )
        before = (
            loader.merge,
            loader.interpolate,
            loader.merge_options,
            loader.sandbox,
        )
        loader.load(
            "a.yaml",
            merge=ConfigLoaderMergeMethod.Deep,
            merge_options={"mergelists": True},
            interpolate=True,
            sandbox=True,
        )
        after = (loader.merge, loader.interpolate, loader.merge_options, loader.sandbox)
        assert before == after


# ---------------------------------------------------------------------------
# Recursive glob expansion
#
# Regression: `recursive` was documented on the constructor, on load(), and in
# the shipped API header, but neither load() nor load_all() forwarded it to
# parse_sources() — so glob expansion always ran non-recursively and both the
# instance setting and the per-call override were silent no-ops. _load() also
# resolved a `recursive` local it never used (glob expansion happens before
# _load is reached).
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("needs_yaml")
class TestRecursiveGlob:
    @staticmethod
    def _tree(tmp_path):
        (tmp_path / "top.yaml").write_text("a: 1\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "nested.yaml").write_text("b: 2\n")

    def test_instance_recursive_true_includes_nested(self, tmp_path):
        self._tree(tmp_path)
        loader = ConfigLoader(
            base_dir=tmp_path,
            recursive=True,
            merge=ConfigLoaderMergeMethod.Deep,
        )
        assert loader.load("**/*.yaml") == {"a": 1, "b": 2}

    def test_per_call_recursive_true_includes_nested(self, tmp_path):
        self._tree(tmp_path)
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Deep)
        assert loader.load("**/*.yaml", recursive=True) == {"a": 1, "b": 2}

    def test_per_call_false_overrides_instance_true(self, tmp_path):
        self._tree(tmp_path)
        loader = ConfigLoader(
            base_dir=tmp_path,
            recursive=True,
            merge=ConfigLoaderMergeMethod.Deep,
        )
        # The per-call override must win, i.e. it must not pick up the
        # instance's True — which is only observable now that either reaches
        # parse_sources at all.
        assert loader.load("**/*.yaml", recursive=False) == {"b": 2}

    def test_default_non_recursive_behavior_unchanged(self, tmp_path):
        # Pins the pre-fix default so the fix is additive: with recursive unset
        # the expansion is unchanged from the 0.11.0 behavior.
        self._tree(tmp_path)
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Deep)
        assert loader.load("**/*.yaml") == {"b": 2}

    def test_load_all_honors_instance_recursive(self, tmp_path):
        self._tree(tmp_path)
        recursive_docs = list(
            ConfigLoader(base_dir=tmp_path, recursive=True).load_all("**/*.yaml")
        )
        assert {"a": 1} in recursive_docs and {"b": 2} in recursive_docs
        plain_docs = list(ConfigLoader(base_dir=tmp_path).load_all("**/*.yaml"))
        assert plain_docs == [{"b": 2}]


class TestJinja2Unavailable:
    """Every Jinja2-backed option names the extra when Jinja2 cannot import.

    The loads run in a child process with jinja2 blocked, and with
    ignore_error=True — which also pins that the error is raised before the
    predicate is consulted, rather than being swallowed into a None result.
    """

    @staticmethod
    def _run(tmp_path, body, blocked=("jinja2",)):
        blocks = "\n".join(f"sys.modules[{name!r}] = None" for name in blocked)
        code = (
            "import sys, json\n"
            f"{blocks}\n"
            "from yaconfiglib import ConfigLoader\n"
            f"{body}\n"
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            timeout=60,
        )

    @pytest.mark.parametrize(
        "option",
        [
            'transform="value.a"',
            'key_factory="%pathname.stem"',
            "interpolate=True",
        ],
    )
    def test_feature_names_jinja2_extra(self, tmp_path, option):
        (tmp_path / "a.json").write_text('{"a": 1}')
        done = self._run(
            tmp_path,
            "loader = ConfigLoader(base_dir="
            f"{str(tmp_path)!r}, ignore_error=True)\n"
            "try:\n"
            f"    print('RESULT', loader.load('a.json', {option}))\n"
            "except Exception as exc:\n"
            "    print(type(exc).__name__, exc)\n",
        )
        assert "ImportError" in done.stdout, done.stdout + done.stderr
        assert "yaconfiglib[jinja2]" in done.stdout
        assert "RESULT" not in done.stdout

    def test_broken_jinja2_reports_import_failure(self, tmp_path):
        (tmp_path / "a.json").write_text('{"a": 1}')
        done = self._run(
            tmp_path,
            "try:\n"
            "    import jinja2\n"
            "except ImportError as exc:\n"
            "    reason = str(exc)\n"
            "else:\n"
            "    reason = None\n"
            "loader = ConfigLoader(base_dir="
            f"{str(tmp_path)!r}, ignore_error=True)\n"
            "try:\n"
            "    loader.load('a.json', transform='value.a')\n"
            "except ImportError as exc:\n"
            "    print('REASON-MATCH', reason is not None and reason in str(exc))\n"
            "    print('CAUSE', isinstance(exc.__cause__, ImportError))\n",
            # Only markupsafe: jinja2 is then installed but unimportable, the
            # real shape of a Jinja2 2.x beside MarkupSafe 2.1+.
            blocked=("markupsafe",),
        )
        assert "REASON-MATCH True" in done.stdout, done.stdout + done.stderr
        assert "CAUSE True" in done.stdout


@pytest.mark.usefixtures("needs_yaml")
class TestLoadsAndLoadArguments:
    """The stdlib-style string API: what it parses, and what it refuses.

    `loads()` read every string as YAML, so a document naming its own format
    on its first line was misparsed, and bytes in any codec that does not
    spell ``#!`` in ASCII failed with a message about paths. `load(None)` and
    `load("")` quietly returned None, turning an unset config path into an
    empty configuration.
    """

    @pytest.mark.parametrize("fmt", ["toml", "json"])
    def test_loads_honors_recognized_marker_line(self, request, fmt):
        import yaconfiglib

        if fmt == "toml":
            request.getfixturevalue("needs_toml")
            doc = "#!c.toml\n[s]\nk = 'v'\n"
            assert yaconfiglib.loads(doc) == {"s": {"k": "v"}}
        else:
            assert yaconfiglib.loads('#!c.json\n{"n": 1e5}') == {"n": 100000.0}

    def test_loads_unrecognized_marker_line_is_content(self):
        import yaconfiglib

        # A real shebang names no format, so the line stays content and the
        # document keeps the YAML default (where that line is a comment).
        assert yaconfiglib.loads("#!/usr/bin/env x\na: 1\n") == {"a": 1}

    def test_loads_script_marker_line_is_not_run(self):
        import yaconfiglib

        # CommandBackend is excluded from the claim, so a script name never
        # turns a string into a program.
        assert yaconfiglib.loads("#!gen.sh\na: 1\n") == {"a": 1}

    def test_loads_without_loader_is_yaml(self):
        import yaconfiglib

        # YAML is the documented default, and 1e5 is a string in YAML 1.1.
        assert yaconfiglib.loads('{"n": 1e5}') == {"n": "1e5"}
        assert yaconfiglib.loads('{"n": 1e5}', loader="json") == {"n": 100000.0}

    @pytest.mark.parametrize("codec", ["utf-8-sig", "utf-16", "utf-16-le", "utf-32"])
    def test_loads_bytes_in_any_codec(self, codec):
        import yaconfiglib

        assert yaconfiglib.loads("a: café\n".encode(codec), encoding=codec) == {
            "a": "café"
        }

    def test_loads_utf8_bom_bytes_default_codec(self):
        import yaconfiglib

        assert yaconfiglib.loads("k: 1\n".encode("utf-8-sig")) == {"k": 1}

    def test_loads_bytearray(self):
        import yaconfiglib

        assert yaconfiglib.loads(bytearray(b"a: 1\n")) == {"a": 1}

    @pytest.mark.parametrize("bad", [None, 42])
    def test_loads_rejects_other_types(self, bad):
        import yaconfiglib

        with pytest.raises(TypeError, match="str or bytes"):
            yaconfiglib.loads(bad)

    def test_load_none_raises_type_error(self):
        import yaconfiglib

        with pytest.raises(TypeError, match="load"):
            yaconfiglib.load(None)

    @pytest.mark.parametrize("empty", ["", b""])
    def test_load_empty_raises_value_error(self, empty):
        import yaconfiglib

        with pytest.raises(ValueError, match="load"):
            yaconfiglib.load(empty)

    def test_load_empty_iterable_returns_default(self):
        import yaconfiglib

        # Unchanged: only the single-source form is strict.
        assert yaconfiglib.load([], default={"d": 1}) == {"d": 1}

    def test_config_loader_skips_none_layer(self, tmp_path):
        (tmp_path / "base.yaml").write_text("a: 1\n", encoding="utf-8")
        loader = ConfigLoader(base_dir=str(tmp_path))
        # The hiera pattern: load("base.yaml", os.environ.get("OVERRIDE")).
        assert loader.load("base.yaml", None) == {"a": 1}

    def test_parse_sources_logs_skipped_empty_source(self, caplog):
        import logging

        caplog.set_level(logging.DEBUG, logger="yaconfiglib.utils.source")
        assert list(parse_sources([None, ""])) == []
        assert any(
            "skipping empty source" in record.message for record in caplog.records
        )

    def test_loads_bytes_reach_backend_instance_verbatim(self):
        import yaconfiglib
        from yaconfiglib.backends import ConfigBackend

        class Raw(ConfigBackend):
            def load(self, path, **options):
                return path.read_bytes()

        payload = bytes([0xFF, 0x00])
        # An ASCII-compatible codec must not decode the payload on its way to
        # a byte-oriented backend.
        assert yaconfiglib.loads(payload, loader=Raw()) == payload
