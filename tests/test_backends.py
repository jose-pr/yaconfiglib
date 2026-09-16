from __future__ import annotations

import os
import sys
import pytest
import subprocess

from yaconfiglib import ConfigLoader
from yaconfiglib.backends.base import ConfigBackend
from yaconfiglib.backends.dotenv import DotenvBackend
from yaconfiglib.backends.env import EnvVarBackend
from yaconfiglib.backends.jinja2 import Jinja2ConfigLoader
from yaconfiglib.backends.python_backend import PythonBackend
from yaconfiglib.backends.command import CommandBackend


class TestRegistryBackends:
    def test_dotenv_backend(self, tmp_path):
        f = tmp_path / "test.env"
        f.write_text(
            "DB_HOST=127.0.0.1 # local database\n"
            "export DB_PORT=5432\n"
            "# Comment\n"
            'DB_PASS="secret # preserved"\n'
            "DB_TOKEN='abc#123'\n"
            "DB_UNQUOTED_TOKEN=abc#123\n"
        )

        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load("test.env", loader="dotenv")
        assert result == {
            "db_host": "127.0.0.1",
            "db_port": "5432",
            "db_pass": "secret # preserved",
            "db_token": "abc#123",
            "db_unquoted_token": "abc#123",
        }

    def test_env_var_backend(self, monkeypatch):
        monkeypatch.setenv("TESTPREFIX_VAL_ONE", "hello")
        monkeypatch.setenv("TESTPREFIX_VAL_TWO", "world")
        monkeypatch.setenv("OTHER_VAR", "ignored")

        loader = ConfigLoader()
        result = loader.load(loader=EnvVarBackend(prefix="TESTPREFIX_"))
        assert result == {"val_one": "hello", "val_two": "world"}

    def test_env_var_backend_nested_delimiter(self, monkeypatch):
        monkeypatch.setenv("APP_DB__HOST", "localhost")
        monkeypatch.setenv("APP_DB__PORT", "5432")
        monkeypatch.setenv("APP_FEATURES__CACHE", "true")
        monkeypatch.setenv("OTHER_DB__HOST", "ignored")

        loader = ConfigLoader()
        result = loader.load(loader=EnvVarBackend(prefix="APP_", nested_delimiter="__"))
        assert result == {
            "db": {"host": "localhost", "port": "5432"},
            "features": {"cache": "true"},
        }

    def test_env_var_backend_coerces_scalars(self, monkeypatch):
        monkeypatch.setenv("APP_DEBUG", "true")
        monkeypatch.setenv("APP_PORT", "5432")
        monkeypatch.setenv("APP_RATE", "1.5")
        monkeypatch.setenv("APP_EMPTY", "null")
        monkeypatch.setenv("APP_ITEMS", '["a", 2]')
        monkeypatch.setenv("APP_DB__OPTIONS", '{"pool": 5}')

        loader = ConfigLoader()
        result = loader.load(
            loader=EnvVarBackend(
                prefix="APP_",
                nested_delimiter="__",
                coerce=True,
            )
        )
        assert result == {
            "debug": True,
            "port": 5432,
            "rate": 1.5,
            "empty": None,
            "items": ["a", 2],
            "db": {"options": {"pool": 5}},
        }

    def test_python_backend(self):
        loader = ConfigLoader()
        data = {"foo": "bar", "nested": [1, 2]}
        result = loader.load(loader=PythonBackend(data))
        assert result == data

    def test_jinja_backend_registered_by_name(self):
        assert ConfigBackend.get_class_by_name("jinja2") is Jinja2ConfigLoader

    def test_jinja_backend_accepts_custom_environment(self, tmp_path):
        from jinja2 import Environment

        template = tmp_path / "config.yaml.j2"
        template.write_text("value: {{ custom_value }}\n")
        environment = Environment()
        environment.globals["custom_value"] = "from-env"

        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load("config.yaml.j2", environment=environment)
        assert result == {"value": "from-env"}

    def test_jinja_backend_falls_back_to_temp_file_without_pathlib_next(
        self, tmp_path, monkeypatch
    ):
        # Regression: the ImportError fallback set MemPath = None and load()
        # called it unconditionally, so every .j2 source raised
        # "TypeError: 'NoneType' object is not callable" without pathlib_next —
        # even though the class docstring promised "a real temp file when
        # pathlib_next is unavailable". Simulating the absent import is enough;
        # MemPath is the only thing this backend uses it for.
        from yaconfiglib.backends import jinja2 as jinja2_backend

        monkeypatch.setattr(jinja2_backend, "MemPath", None)

        template = tmp_path / "config.yaml.j2"
        template.write_text("port: {{ 8000 + 80 }}\nname: plain\n")

        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load("config.yaml.j2")
        # Rendered, and still dispatched to the YAML backend by the stripped
        # filename rather than being read as text.
        assert result == {"port": 8080, "name": "plain"}


class TestCommandBackend:
    def test_cmd_basic_execution_sniffing(self):
        loader = ConfigLoader()
        cmd = "cmd://python -c \"print({'a': 1, 'b': 2})\""
        result = loader.load(cmd)
        assert result == {"a": 1, "b": 2}

    def test_cmd_explicit_format(self):
        loader = ConfigLoader()
        cmd = "cmd+yaml://python -c \"print('x: 10')\""
        result = loader.load(cmd)
        assert result == {"x": 10}

    def test_cmd_format_parameter(self):
        loader = ConfigLoader()
        cmd = "cmd://python -c \"print('[section]\\nkey = \\'val\\'')\""
        result = loader.load(cmd, format="toml")
        assert result == {"section": {"key": "val"}}

    def test_cmd_shebang_detection(self):
        loader = ConfigLoader()
        cmd = "cmd://python -c \"import json; print('#!json\\n' + json.dumps({'foo': 'bar'}))\""
        result = loader.load(cmd)
        assert result == {"foo": "bar"}

    def test_cmd_multiple_formats_fallback(self):
        loader = ConfigLoader()
        cmd = "cmd://python -c \"print('[1, 2, 3]')\""
        result = loader.load(cmd, format="toml,json")
        assert result == [1, 2, 3]

    def test_cmd_script_file_extension(self, tmp_path):
        if sys.platform == "win32":
            f = tmp_path / "script.bat"
            f.write_text('@echo off\necho {"win": true}\n')
        else:
            f = tmp_path / "script.sh"
            f.write_text("#!/bin/sh\necho '{\"unix\": true}'\n")
            os.chmod(f, 0o755)

        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load(f.name)
        if sys.platform == "win32":
            assert result == {"win": True}
        else:
            assert result == {"unix": True}

    def test_yaml_dynamic_include_command(self, tmp_path):
        yaml_content = (
            "config:\n"
            "  app: !include 'cmd+json://python -c \"import json; print(json.dumps({''name'': ''my-app''}))\"'\n"
        )
        f = tmp_path / "main.yaml"
        f.write_text(yaml_content)

        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load("main.yaml")
        assert result == {"config": {"app": {"name": "my-app"}}}

    # --- Fringe Case Tests ---

    def test_cmd_execution_failure(self):
        loader = ConfigLoader()
        # Invalid command execution raises subprocess.CalledProcessError
        cmd = 'cmd://python -c "import sys; sys.exit(42)"'
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            loader.load(cmd)
        assert exc_info.value.returncode == 42

    def test_cmd_execution_empty(self):
        loader = ConfigLoader()
        # Empty output should be handled safely (returns empty raw string)
        cmd = 'cmd://python -c "pass"'
        result = loader.load(cmd)
        assert result == ""

    def test_cmd_shebang_invalid(self):
        loader = ConfigLoader()
        # Unknown/invalid format shebang should fail explicitly
        cmd = "cmd://python -c \"print('#!invalid_format\\nsome content')\""
        with pytest.raises(ValueError) as exc_info:
            loader.load(cmd)
        assert "Unknown configuration format/loader" in str(exc_info.value)


class TestYamlIncludeRegistration:
    def test_manual_include_registration_warns(self, caplog):
        """A pre-existing !include constructor is overridden with a warning.

        yaconfiglib registers !include/!load automatically on first load, so a
        manual ``yaml.add_constructor`` is unnecessary and gets replaced. The
        override must be visible (a WARNING) rather than silent.
        """
        import logging

        import yaml

        from yaconfiglib.backends.yaml import YamlConfig

        class _Fresh(yaml.SafeLoader):  # fresh class: not yet auto-registered
            # The flag is inherited via MRO from SafeLoader once any earlier load
            # registered there; shadow it so this class runs the registration path.
            _yaconfiglib_include_registered = False

        _Fresh.add_constructor("!include", lambda ldr, node: None)  # manual, foreign

        with caplog.at_level(logging.WARNING):
            YamlConfig._register_include_tags(_Fresh, loader=None, path_factory=None)

        assert any("unnecessary" in rec.getMessage() for rec in caplog.records)
        assert getattr(_Fresh, "_yaconfiglib_include_registered", False) is True

    def test_no_warning_without_preexisting_constructor(self, caplog):
        import logging

        import yaml

        from yaconfiglib.backends.yaml import YamlConfig

        class _Fresh(yaml.SafeLoader):
            _yaconfiglib_include_registered = False

        with caplog.at_level(logging.WARNING):
            YamlConfig._register_include_tags(_Fresh, loader=None, path_factory=None)

        assert not any("unnecessary" in rec.getMessage() for rec in caplog.records)


class TestIncludePathResolution:
    """A relative !include resolves against the file it is written in."""

    @staticmethod
    def _tree(tmp_path):
        """conf/app.yaml + conf/db.toml, with the CWD moved to a sibling directory."""
        conf = tmp_path / "conf"
        conf.mkdir()
        (conf / "db.toml").write_text('host = "from-conf"\n', encoding="utf-8")
        (conf / "app.yaml").write_text("db: !include db.toml\n", encoding="utf-8")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        return conf, elsewhere

    @pytest.mark.parametrize(
        "path_factory_name", ["default_path_factory", "stdlib_path_factory"]
    )
    def test_sibling_include_from_another_cwd(
        self, tmp_path, monkeypatch, path_factory_name
    ):
        import pathlib

        conf, elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(elsewhere)
        kwargs = {"base_dir": tmp_path}
        if path_factory_name == "stdlib_path_factory":
            kwargs["path_factory"] = pathlib.Path

        result = ConfigLoader(**kwargs).load(str(conf / "app.yaml"))

        assert result == {"db": {"host": "from-conf"}}

    def test_sibling_include_with_absolute_top_level_path(self, tmp_path, monkeypatch):
        conf, elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(elsewhere)

        result = ConfigLoader().load(str(conf / "app.yaml"))

        assert result == {"db": {"host": "from-conf"}}

    def test_nested_include_resolves_against_each_including_file(
        self, tmp_path, monkeypatch
    ):
        conf = tmp_path / "conf"
        (conf / "sub").mkdir(parents=True)
        (conf / "leaf.toml").write_text("z = 999\n", encoding="utf-8")  # decoy
        (conf / "sub" / "leaf.toml").write_text("z = 1\n", encoding="utf-8")
        (conf / "sub" / "mid.yaml").write_text(
            "y: !include leaf.toml\n", encoding="utf-8"
        )
        (conf / "app.yaml").write_text("x: !include sub/mid.yaml\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader(base_dir=conf).load("app.yaml")

        assert result == {"x": {"y": {"z": 1}}}

    def test_same_relative_name_at_each_depth_is_not_a_cycle(self, tmp_path):
        conf = tmp_path / "conf"
        (conf / "x" / "x").mkdir(parents=True)
        (conf / "a.yaml").write_text("x: !include x/a.yaml\n", encoding="utf-8")
        (conf / "x" / "a.yaml").write_text("y: !include x/a.yaml\n", encoding="utf-8")
        (conf / "x" / "x" / "a.yaml").write_text("z: 1\n", encoding="utf-8")

        result = ConfigLoader(base_dir=conf).load("a.yaml")

        assert result == {"x": {"y": {"z": 1}}}

    @pytest.mark.parametrize("form", ["sequence", "mapping"])
    def test_every_sequence_and_mapping_source_is_rebased(
        self, tmp_path, monkeypatch, form
    ):
        conf = tmp_path / "conf"
        conf.mkdir()
        (conf / "one.yaml").write_text("a: 1\n", encoding="utf-8")
        (conf / "two.yaml").write_text("b: 2\n", encoding="utf-8")
        include = (
            'o: !include ["one.yaml", "two.yaml"]\n'
            if form == "sequence"
            else 'o: !include {pathname: "one.yaml"}\n'
        )
        (conf / "app.yaml").write_text(include, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader(base_dir=tmp_path).load(str(conf / "app.yaml"))

        expected = {"a": 1, "b": 2} if form == "sequence" else {"a": 1}
        assert result == {"o": expected}

    def test_glob_include_expands_next_to_including_file(self, tmp_path, monkeypatch):
        conf = tmp_path / "conf"
        (conf / "parts").mkdir(parents=True)
        (conf / "parts" / "p1.yaml").write_text("a: 1\n", encoding="utf-8")
        (conf / "parts" / "p2.yaml").write_text("b: 2\n", encoding="utf-8")
        (conf / "app.yaml").write_text(
            'all: !include "parts/*.yaml"\n', encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader(base_dir=tmp_path).load(str(conf / "app.yaml"))

        assert result == {"all": {"a": 1, "b": 2}}

    def test_jinja2_template_include_resolves_next_to_template(
        self, tmp_path, monkeypatch
    ):
        conf = tmp_path / "conf"
        conf.mkdir()
        (conf / "db.toml").write_text('host = "from-conf"\n', encoding="utf-8")
        (conf / "app.yaml.j2").write_text(
            "db: !include db{{ '.' }}toml\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader(base_dir=tmp_path).load(str(conf / "app.yaml.j2"))

        assert result == {"db": {"host": "from-conf"}}

    def test_command_backend_drops_origin_option(self, tmp_path):
        assert (
            CommandBackend().load('cmd+json://python -c "print(1)"', origin=tmp_path)
            == 1
        )

    def test_relative_base_dir_is_not_joined_twice(self, tmp_path, monkeypatch):
        conf, _elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader(base_dir="conf").load("app.yaml")

        assert result == {"db": {"host": "from-conf"}}

    def test_absolute_include_path_is_unchanged(self, tmp_path, monkeypatch):
        conf, elsewhere = self._tree(tmp_path)
        other = tmp_path / "other.toml"
        other.write_text('host = "from-other"\n', encoding="utf-8")
        (conf / "app.yaml").write_text(
            f"db: !include '{other.as_posix()}'\n", encoding="utf-8"
        )
        monkeypatch.chdir(elsewhere)

        result = ConfigLoader().load(str(conf / "app.yaml"))

        assert result == {"db": {"host": "from-other"}}

    @pytest.mark.parametrize("materialized_as", ["mempath", "tempfile"])
    def test_in_memory_document_include_uses_base_dir(
        self, tmp_path, monkeypatch, materialized_as
    ):
        conf, elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(elsewhere)
        if materialized_as == "tempfile":
            from yaconfiglib.utils import source as source_module

            monkeypatch.setattr(source_module, "MemPath", None)

        result = ConfigLoader(base_dir=conf).load("#!mem.yaml\nd: !include db.toml\n")

        assert result == {"d": {"host": "from-conf"}}

    def test_command_output_include_uses_base_dir(self, tmp_path, monkeypatch):
        conf, elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(elsewhere)

        result = ConfigLoader(base_dir=conf).load(
            "cmd+yaml://python -c \"print('d: !include db.toml')\""
        )

        assert result == {"d": {"host": "from-conf"}}

    def test_file_relative_script_include_still_blocked(self, tmp_path, monkeypatch):
        from yaconfiglib import CommandsDisabledError

        conf, elsewhere = self._tree(tmp_path)
        (conf / "gen.sh").write_text("echo 'a: 1'\n", encoding="utf-8")
        (conf / "app.yaml").write_text("s: !include gen.sh\n", encoding="utf-8")
        monkeypatch.chdir(elsewhere)

        with pytest.raises(CommandsDisabledError):
            ConfigLoader(allow_commands=False).load(str(conf / "app.yaml"))


class TestIncludeInheritsCallEncoding:
    """A per-call encoding= applies to every include target, at every depth."""

    CAFE = "café"

    @classmethod
    def _chain(cls, tmp_path, codec="cp1252"):
        """main.yaml -> sub.yaml -> leaf.yaml, all written in *codec*."""
        (tmp_path / "leaf.yaml").write_bytes(f"c: {cls.CAFE}\n".encode(codec))
        (tmp_path / "sub.yaml").write_bytes("b: !include leaf.yaml\n".encode(codec))
        (tmp_path / "main.yaml").write_bytes("a: !include sub.yaml\n".encode(codec))
        return {"a": {"b": {"c": cls.CAFE}}}

    @pytest.mark.parametrize("entry_point", ["loader_call", "module_load", "load_all"])
    def test_per_call_encoding_reaches_nested_file_includes(
        self, tmp_path, monkeypatch, entry_point
    ):
        import yaconfiglib

        expected = self._chain(tmp_path)
        monkeypatch.chdir(tmp_path)

        if entry_point == "loader_call":
            result = ConfigLoader().load("main.yaml", encoding="cp1252")
        elif entry_point == "module_load":
            result = yaconfiglib.load("main.yaml", encoding="cp1252")
        else:
            result = list(ConfigLoader().load_all("main.yaml", encoding="cp1252"))[0]

        assert result == expected

    def test_per_call_encoding_overrides_instance_encoding_for_includes(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "sub.yaml").write_bytes(f"b: {self.CAFE}\n".encode("utf-8"))
        (tmp_path / "main.yaml").write_bytes("a: !include sub.yaml\n".encode("utf-8"))
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader(encoding="cp1252").load("main.yaml", encoding="utf-8")

        assert result == {"a": {"b": self.CAFE}}

    def test_per_call_encoding_reaches_command_include(self, tmp_path, monkeypatch):
        # The child writes {"v": "café"} as cp1252 bytes, so the shell text stays ASCII.
        payload = ", ".join(str(b) for b in '{"v": "café"}'.encode("cp1252"))
        (tmp_path / "main.yaml").write_bytes(
            (
                "p: !include 'cmd+json://python -c \"import sys;"
                f" sys.stdout.buffer.write(bytes([{payload}]))\"'\n"
            ).encode("cp1252")
        )
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader().load("main.yaml", encoding="cp1252")

        assert result == {"p": {"v": self.CAFE}}

    def test_mapping_encoding_applies_to_its_target_only(self, tmp_path, monkeypatch):
        (tmp_path / "leaf.yaml").write_bytes(f"c: {self.CAFE}\n".encode("cp1252"))
        (tmp_path / "mid.yaml").write_bytes("b: !include leaf.yaml\n".encode("utf-16"))
        (tmp_path / "main.yaml").write_bytes(
            "a: !include {pathname: mid.yaml, encoding: utf-16}\n".encode("cp1252")
        )
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader().load("main.yaml", encoding="cp1252")

        assert result == {"a": {"b": {"c": self.CAFE}}}

    def test_null_mapping_encoding_inherits_call_encoding(self, tmp_path, monkeypatch):
        self._chain(tmp_path)
        (tmp_path / "main.yaml").write_bytes(
            "a: !include {pathname: sub.yaml, encoding: null}\n".encode("cp1252")
        )
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader().load("main.yaml", encoding="cp1252")

        assert result == {"a": {"b": {"c": self.CAFE}}}

    def test_encoding_reaches_include_inside_included_command_output(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "leaf.yaml").write_bytes(f"c: {self.CAFE}\n".encode("cp1252"))
        (tmp_path / "main.yaml").write_bytes(
            (
                "o: !include 'cmd+yaml://python -c \"print(''x: !include leaf.yaml'')\"'\n"
            ).encode("cp1252")
        )
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader().load("main.yaml", encoding="cp1252")

        assert result == {"o": {"x": {"c": self.CAFE}}}

    @pytest.mark.parametrize("form", ["sequence", "j2_template"])
    def test_per_call_encoding_reaches_sequence_and_template_includes(
        self, tmp_path, monkeypatch, form
    ):
        if form == "sequence":
            (tmp_path / "a.yaml").write_bytes(f"x: {self.CAFE}\n".encode("cp1252"))
            (tmp_path / "b.yaml").write_bytes(f"y: {self.CAFE}\n".encode("cp1252"))
            (tmp_path / "main.yaml").write_bytes(
                "s: !include [a.yaml, b.yaml]\n".encode("cp1252")
            )
            expected = {"s": {"x": self.CAFE, "y": self.CAFE}}
        else:
            (tmp_path / "leaf.yaml").write_bytes(
                f"inner: {self.CAFE}\n".encode("cp1252")
            )
            (tmp_path / "sub.yaml.j2").write_bytes(
                f"t: {self.CAFE}\nleaf: !include leaf.yaml\n".encode("cp1252")
            )
            (tmp_path / "main.yaml").write_bytes(
                "s: !include sub.yaml.j2\n".encode("cp1252")
            )
            expected = {"s": {"t": self.CAFE, "leaf": {"inner": self.CAFE}}}
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader().load("main.yaml", encoding="cp1252")

        assert result == expected

    def test_instance_encoding_still_reaches_includes(self, tmp_path, monkeypatch):
        expected = self._chain(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader(encoding="cp1252").load("main.yaml")

        assert result == expected


class TestPrivateSafeLoader:
    """!include/!load must never be armed on the shared yaml.SafeLoader."""

    @staticmethod
    def _assert_stock_safeloader_untouched(tmp_path):
        import yaml

        target = tmp_path / "t.yaml"
        target.write_text("a: 1\n", encoding="utf-8")
        assert "!include" not in yaml.SafeLoader.yaml_constructors
        assert "!load" not in yaml.SafeLoader.yaml_constructors
        # A file include, so a pre-fix run reads a file instead of running a command.
        with pytest.raises(yaml.constructor.ConstructorError):
            yaml.safe_load(f"x: !include '{target.as_posix()}'")

    def test_loads_does_not_arm_stock_safeloader(self, tmp_path):
        import yaconfiglib

        yaconfiglib.loads("a: 1")
        self._assert_stock_safeloader_untouched(tmp_path)

    def test_explicit_loader_cls_is_not_mutated(self, tmp_path):
        import yaml

        from yaconfiglib import ConfigLoader

        child = tmp_path / "child.yaml"
        child.write_text("b: 2\n", encoding="utf-8")
        parent = tmp_path / "parent.yaml"
        parent.write_text(f"x: !include '{child.as_posix()}'\n", encoding="utf-8")

        result = ConfigLoader().load(str(parent), loader_cls=yaml.SafeLoader)

        assert result == {"x": {"b": 2}}
        self._assert_stock_safeloader_untouched(tmp_path)

    def test_owned_loader_cls_wraps_foreign_classes_only(self):
        import yaml

        from yaconfiglib.backends.yaml import _IncludeSafeLoader, _owned_loader_cls

        assert _owned_loader_cls(_IncludeSafeLoader) is _IncludeSafeLoader
        wrapped = _owned_loader_cls(yaml.SafeLoader)
        assert wrapped is not yaml.SafeLoader
        assert _owned_loader_cls(yaml.SafeLoader) is wrapped

        class _UserLoader(_IncludeSafeLoader):
            pass

        assert _owned_loader_cls(_UserLoader) is not _UserLoader

    def test_parse_without_config_loader_rejects_include(self, tmp_path):
        import yaml

        import yaconfiglib
        from yaconfiglib.backends.yaml import YamlConfig

        yaconfiglib.loads("a: 1")  # registers the tags on the owned default class
        child = tmp_path / "child.yaml"
        child.write_text("b: 2\n", encoding="utf-8")
        parent = tmp_path / "parent.yaml"
        parent.write_text(f"x: !include '{child.as_posix()}'\n", encoding="utf-8")

        with pytest.raises(yaml.constructor.ConstructorError):
            YamlConfig().load(str(parent))


class TestJinja2SourceTrust:
    """.j2 sources follow the load's sandbox/allow_commands/strict settings."""

    SSTI = "{{ ''.__class__.__mro__ }}"

    @staticmethod
    def _write(path, text):
        path.write_text(text, encoding="utf-8")
        return path

    def test_top_level_j2_is_sandboxed(self, tmp_path):
        from jinja2.exceptions import SecurityError

        from yaconfiglib import ConfigLoader

        self._write(tmp_path / "settings.yaml.j2", f'x: "{self.SSTI}"\n')
        with pytest.raises(SecurityError):
            ConfigLoader(base_dir=tmp_path, sandbox=True).load("settings.yaml.j2")

    def test_in_memory_j2_include_sandboxed_under_checklist(self, tmp_path):
        from jinja2.exceptions import SecurityError

        import yaconfiglib

        doc = self._write(
            tmp_path / "u.yaml",
            r"""x: !include "#!x.yaml.j2\ny: \"{{ ''.__class__.__mro__ }}\"\n"
""",
        )
        with pytest.raises(SecurityError):
            yaconfiglib.load(
                str(doc),
                allow_commands=False,
                interpolate=True,
                sandbox=True,
                loader="yaml",
            )

    def test_on_disk_j2_include_sandboxed(self, tmp_path):
        from jinja2.exceptions import SecurityError

        from yaconfiglib import ConfigLoader

        self._write(tmp_path / "child.yaml.j2", f'y: "{self.SSTI}"\n')
        self._write(tmp_path / "u.yaml", "x: !include child.yaml.j2\n")
        with pytest.raises(SecurityError):
            ConfigLoader(base_dir=tmp_path, sandbox=True).load("u.yaml")

    def test_j2_include_sandboxed_when_only_commands_disabled(self, tmp_path):
        from jinja2.exceptions import SecurityError

        import yaconfiglib

        marker = tmp_path / "ssti.marker"
        payload = (
            f"{{{{ cycler.__init__.__globals__.os.makedirs('{marker.as_posix()}') }}}}"
        )
        doc = self._write(
            tmp_path / "u.yaml",
            'x: !include "#!x.yaml.j2\\ny: \\"' + payload + '\\"\\n"\n',
        )
        with pytest.raises(SecurityError):
            yaconfiglib.load(str(doc), allow_commands=False)
        assert not marker.exists()

    @pytest.mark.parametrize("without_pathlib_next", [False, True])
    def test_rendered_command_source_refused(
        self, tmp_path, monkeypatch, without_pathlib_next
    ):
        from yaconfiglib import CommandsDisabledError, ConfigLoader
        from yaconfiglib.backends import jinja2 as jinja2_backend

        monkeypatch.chdir(tmp_path)
        marker = tmp_path / "pwned.marker"
        if without_pathlib_next:
            monkeypatch.setattr(jinja2_backend, "MemPath", None)
            self._write(tmp_path / "x.cmd.j2", "echo pwned> pwned.marker\n")
            source = "x.cmd.j2"
        else:
            # The in-memory name itself is the shell payload once rendered.
            source = "#!x & echo pwned> pwned.marker & rem .cmd.j2\nrem\n"
        with pytest.raises(CommandsDisabledError):
            ConfigLoader(base_dir=tmp_path, allow_commands=False).load(source)
        assert not marker.exists()

    def test_per_call_sandbox_reaches_j2_include(self, tmp_path):
        from jinja2.exceptions import SecurityError

        from yaconfiglib import ConfigLoader

        child = self._write(tmp_path / "child.yaml.j2", f'y: "{self.SSTI}"\n')
        doc = self._write(tmp_path / "u.yaml", f"x: !include '{child.as_posix()}'\n")
        with pytest.raises(SecurityError):
            ConfigLoader().load(str(doc), sandbox=True)

    def test_strict_reaches_j2(self, tmp_path):
        from jinja2.exceptions import UndefinedError

        from yaconfiglib import ConfigLoader

        self._write(tmp_path / "x.yaml.j2", 'v: "{{ missing }}"\n')
        with pytest.raises(UndefinedError):
            ConfigLoader(base_dir=tmp_path, strict=True).load("x.yaml.j2")

    def test_non_sandboxed_environment_refused_when_hardened(self, tmp_path):
        from jinja2 import Environment

        from yaconfiglib import ConfigLoader

        self._write(tmp_path / "x.yaml.j2", "a: 1\n")
        with pytest.raises(ValueError):
            ConfigLoader(base_dir=tmp_path, sandbox=True).load(
                "x.yaml.j2", environment=Environment()
            )

    def test_env_available_with_inject_env(self, tmp_path, monkeypatch):
        import yaconfiglib

        monkeypatch.setenv("ENVIRONMENT", "production")
        template = self._write(
            tmp_path / "settings.yaml.j2",
            'replicas: {{ 2 if env.ENVIRONMENT == "production" else 1 }}\n',
        )
        assert yaconfiglib.load(str(template), inject_env=True) == {"replicas": 2}

    def test_ordinary_j2_renders_the_same_sandboxed(self, tmp_path):
        from yaconfiglib import ConfigLoader

        self._write(
            tmp_path / "x.yaml.j2",
            'a: {{ 1 + 1 }}\nname: "{{ pathname.name }}"\n'
            "{% set items = [] %}{% do items.append(3) %}b: {{ items[0] }}\n",
        )
        plain = ConfigLoader(base_dir=tmp_path).load("x.yaml.j2")
        sandboxed = ConfigLoader(base_dir=tmp_path, sandbox=True).load("x.yaml.j2")
        assert plain == sandboxed == {"a": 2, "name": "x.yaml.j2", "b": 3}


class TestDeterministicDispatch:
    def test_first_defined_backend_wins(self):
        import re

        from pathlib import Path as StdPath

        class ZzzFirstBackend(ConfigBackend):
            PATHNAME_REGEX = re.compile(r".*\.zzztest$")
            NAME = "zzzfirst"

            def load(self, path, **options):
                return "first"

        class ZzzSecondBackend(ConfigBackend):
            PATHNAME_REGEX = re.compile(r".*\.zzztest$")
            NAME = "zzzsecond"

            def load(self, path, **options):
                return "second"

        # Regression: recursive discovery returned a set, so which of two
        # equally-matching backends won depended on hash order.
        subs = ConfigBackend.__subclasses__(recursive=True)
        assert isinstance(subs, list)
        for _ in range(10):
            assert (
                ConfigBackend.get_class_by_path(StdPath("x.zzztest")) is ZzzFirstBackend
            )


class TestCommandOutputEncoding:
    def test_command_output_utf8_decoding(self):
        import sys

        # -X utf8 forces the child to emit UTF-8; the backend must decode it
        # as UTF-8 (previously the locale codec — cp1252 on Windows — turned
        # 'café' into mojibake).
        cmd = f"cmd://{sys.executable} -X utf8 -c \"print('caf\xe9')\""
        result = CommandBackend().load(cmd)
        assert result == "café"
