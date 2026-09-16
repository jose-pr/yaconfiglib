"""
Tests for ConfigLoader — loading, merging, and example file compatibility.
"""

import pathlib
import typing

import pytest

from yaconfiglib import ConfigLoader
from yaconfiglib.loader import ConfigLoaderMergeMethod
from yaconfiglib.utils.source import parse_sources

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

    def test_substitute_merge(self, tmp_path):
        (tmp_path / "a.yaml").write_text("list: [1, 2, 3]\n")
        (tmp_path / "b.yaml").write_text("list: [4, 5]\n")
        loader = ConfigLoader(
            base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Substitute
        )
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

    def test_flatten_scalar_result_raises_clear_error(self):
        from yaconfiglib.backends.python_backend import PythonBackend

        loader = ConfigLoader()
        with pytest.raises(TypeError, match="flatten=True"):
            loader.load(loader=PythonBackend("scalar"), flatten=True)


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

    def test_advanced_example_loads_from_any_cwd(self, tmp_path, monkeypatch):
        """The example's includes are relative to advanced.yaml, not to the CWD."""
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader().load(str(EXAMPLES / "advanced.yaml"))

        assert result["database_config"]["python.testing.pytestEnabled"] is True
        assert result["dynamic_includes"] == {"includeme.yaml": {"me": True}}

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

    def test_duplicate_path_source_loads_once(self, tmp_path):
        (tmp_path / "a.yaml").write_text("file: a.yaml\n")
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.List)
        result = loader.load("a.yaml", "a.yaml")
        assert result == [{"file": "a.yaml"}]

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


# ---------------------------------------------------------------------------
# Per-call override isolation & global-state hygiene (loader_state fixes)
# ---------------------------------------------------------------------------


class TestLoaderStateHygiene:
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
        ConfigLoader(log_level=logging.DEBUG)
        ConfigLoader()
        assert mod_logger.level == before


class TestSecurityControls:
    def test_commands_blocked_top_level(self):
        import pytest

        from yaconfiglib import CommandsDisabledError, ConfigLoader

        loader = ConfigLoader(allow_commands=False)
        with pytest.raises(CommandsDisabledError):
            loader.load("cmd://python -c \"print({'a': 1})\"")

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

    def test_sandbox_blocks_ssti(self):
        import pytest
        from jinja2.exceptions import SecurityError

        from yaconfiglib import ConfigLoader

        payload = "#!\nx: \"{{ ''.__class__.__mro__ }}\"\n"
        # The sandbox refuses attribute traversal into Python internals with a
        # Jinja SecurityError specifically (not just any error).
        with pytest.raises(SecurityError):
            ConfigLoader(interpolate=True, sandbox=True).load(payload)

    def test_sandbox_allows_ordinary_interpolation(self):
        from yaconfiglib import ConfigLoader

        payload = '#!\ngreeting: "hello {{ name }}"\nname: world\n'
        result = ConfigLoader(interpolate=True, sandbox=True).load(payload)
        assert result["greeting"] == "hello world"

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

    def test_per_call_allow_commands_reaches_scalar_include(self, tmp_path):
        from yaconfiglib import CommandsDisabledError

        cmd, marker = self._command(tmp_path)
        doc = self._write(tmp_path / "u.yaml", f"x: !include '{cmd}'\n")
        with pytest.raises(CommandsDisabledError):
            ConfigLoader().load(str(doc), allow_commands=False)
        assert not marker.exists()

    def test_per_call_sandbox_reaches_scalar_include(self, tmp_path):
        from jinja2.exceptions import SecurityError

        child = self._write(tmp_path / "child.yaml", f'y: "{self.SSTI_TEXT}"\n')
        doc = self._write(tmp_path / "u.yaml", f"x: !include '{child.as_posix()}'\n")
        with pytest.raises(SecurityError):
            ConfigLoader(interpolate=True).load(str(doc), sandbox=True)

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

    def test_mapping_loader_command_still_blocked(self, tmp_path):
        from yaconfiglib import CommandsDisabledError

        cmd, marker = self._command(tmp_path)
        doc = self._write(
            tmp_path / "u.yaml", f'x: !include {{pathname: "{cmd}", loader: command}}\n'
        )
        with pytest.raises(CommandsDisabledError):
            ConfigLoader(allow_commands=False).load(str(doc))
        assert not marker.exists()

    def test_advanced_example_includes_still_load(self, monkeypatch):
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        monkeypatch.chdir(repo_root)
        result = ConfigLoader().load("examples/advanced.yaml")
        assert result["dynamic_includes"] == {"includeme.yaml": {"me": True}}

    def test_sequence_include_form(self, tmp_path):
        child = self._write(tmp_path / "child.yaml", "b: 2\n")
        doc = self._write(tmp_path / "u.yaml", f'x: !include ["{child.as_posix()}"]\n')
        assert ConfigLoader().load(str(doc)) == {"x": {"b": 2}}

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

    def test_inject_env_is_read_only(self, monkeypatch):
        import os

        from jinja2.exceptions import UndefinedError

        monkeypatch.delenv("YACFG_PWN", raising=False)
        source = "#!\nv: \"{% do env.update({'YACFG_PWN': 'x'}) %}ok\"\n"
        with pytest.raises(UndefinedError):
            ConfigLoader(interpolate=True, inject_env=True, sandbox=True).load(source)
        assert "YACFG_PWN" not in os.environ

    def test_include_cycle_is_a_clear_error(self, tmp_path):
        (tmp_path / "a.yaml").write_text("x: !include b.yaml\n", encoding="utf-8")
        (tmp_path / "b.yaml").write_text("y: !include a.yaml\n", encoding="utf-8")
        with pytest.raises(ValueError, match="include cycle"):
            ConfigLoader(base_dir=tmp_path).load("a.yaml")

    def test_load_all_per_call_sandbox(self, tmp_path):
        from jinja2.exceptions import SecurityError

        doc = tmp_path / "s.yaml"
        doc.write_text("x: \"{{ ''.__class__.__mro__ }}\"\n", encoding="utf-8")
        with pytest.raises(SecurityError):
            list(ConfigLoader(interpolate=True).load_all(str(doc), sandbox=True))

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
        with pytest.raises(Exception):
            loader.load("bad.yaml")


class TestHashMerge:
    def _files(self, tmp_path):
        (tmp_path / "a.yaml").write_text("v: 1\n")
        (tmp_path / "b.yaml").write_text("v: 2\n")

    def test_hash_default_key_is_stem(self, tmp_path):
        self._files(tmp_path)
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Hash)
        result = loader.load("a.yaml", "b.yaml")
        assert result == {"a": {"v": 1}, "b": {"v": 2}}

    def test_hash_attribute_key_factory(self, tmp_path):
        self._files(tmp_path)
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Hash)
        result = loader.load("a.yaml", "b.yaml", key_factory="name")
        assert set(result.keys()) == {"a.yaml", "b.yaml"}

    def test_hash_jinja_key_factory(self, tmp_path):
        self._files(tmp_path)
        loader = ConfigLoader(base_dir=tmp_path, merge=ConfigLoaderMergeMethod.Hash)
        result = loader.load("a.yaml", "b.yaml", key_factory="%pathname.stem ~ '!'")
        assert set(result.keys()) == {"a!", "b!"}


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
