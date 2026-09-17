from __future__ import annotations

import os
import sys
import pytest
import subprocess

import _extras

from yaconfiglib import ConfigLoader
from yaconfiglib.backends.base import ConfigBackend
from yaconfiglib.backends.dotenv import DotenvBackend
from yaconfiglib.backends.env import EnvVarBackend
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

    @pytest.mark.usefixtures("needs_jinja2")
    def test_jinja_backend_registered_by_name(self):
        from yaconfiglib.backends.jinja2 import Jinja2ConfigLoader

        assert ConfigBackend.get_class_by_name("jinja2") is Jinja2ConfigLoader

    @pytest.mark.usefixtures("needs_jinja2")
    def test_jinja_backend_accepts_custom_environment(self, tmp_path):
        from jinja2 import Environment

        template = tmp_path / "config.yaml.j2"
        template.write_text("value: {{ custom_value }}\n")
        environment = Environment()
        environment.globals["custom_value"] = "from-env"

        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load("config.yaml.j2", environment=environment)
        assert result == {"value": "from-env"}

    @pytest.mark.usefixtures("needs_jinja2")
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
    @pytest.mark.usefixtures("needs_yaml")
    def test_cmd_basic_execution_sniffing(self):
        loader = ConfigLoader()
        cmd = "cmd://python -c \"print({'a': 1, 'b': 2})\""
        result = loader.load(cmd)
        assert result == {"a": 1, "b": 2}

    @pytest.mark.usefixtures("needs_yaml")
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

    @pytest.mark.usefixtures("needs_yaml")
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


@pytest.mark.usefixtures("needs_yaml")
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
    @pytest.mark.usefixtures("needs_yaml")
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

    @pytest.mark.usefixtures("needs_yaml")
    def test_sibling_include_with_absolute_top_level_path(self, tmp_path, monkeypatch):
        conf, elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(elsewhere)

        result = ConfigLoader().load(str(conf / "app.yaml"))

        assert result == {"db": {"host": "from-conf"}}

    @pytest.mark.usefixtures("needs_yaml")
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

    @pytest.mark.usefixtures("needs_yaml")
    def test_same_relative_name_at_each_depth_is_not_a_cycle(self, tmp_path):
        conf = tmp_path / "conf"
        (conf / "x" / "x").mkdir(parents=True)
        (conf / "a.yaml").write_text("x: !include x/a.yaml\n", encoding="utf-8")
        (conf / "x" / "a.yaml").write_text("y: !include x/a.yaml\n", encoding="utf-8")
        (conf / "x" / "x" / "a.yaml").write_text("z: 1\n", encoding="utf-8")

        result = ConfigLoader(base_dir=conf).load("a.yaml")

        assert result == {"x": {"y": {"z": 1}}}

    @pytest.mark.parametrize("form", ["sequence", "mapping"])
    @pytest.mark.usefixtures("needs_yaml")
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

    @pytest.mark.usefixtures("needs_yaml")
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

    @pytest.mark.usefixtures("needs_jinja2")
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

    @pytest.mark.usefixtures("needs_yaml")
    def test_relative_base_dir_is_not_joined_twice(self, tmp_path, monkeypatch):
        conf, _elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = ConfigLoader(base_dir="conf").load("app.yaml")

        assert result == {"db": {"host": "from-conf"}}

    @pytest.mark.usefixtures("needs_yaml")
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
    @pytest.mark.usefixtures("needs_yaml")
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

    @pytest.mark.usefixtures("needs_yaml")
    def test_command_output_include_uses_base_dir(self, tmp_path, monkeypatch):
        conf, elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(elsewhere)

        result = ConfigLoader(base_dir=conf).load(
            "cmd+yaml://python -c \"print('d: !include db.toml')\""
        )

        assert result == {"d": {"host": "from-conf"}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_file_relative_script_include_still_blocked(self, tmp_path, monkeypatch):
        from yaconfiglib import CommandsDisabledError

        conf, elsewhere = self._tree(tmp_path)
        (conf / "gen.sh").write_text("echo 'a: 1'\n", encoding="utf-8")
        (conf / "app.yaml").write_text("s: !include gen.sh\n", encoding="utf-8")
        monkeypatch.chdir(elsewhere)

        with pytest.raises(CommandsDisabledError):
            ConfigLoader(allow_commands=False).load(str(conf / "app.yaml"))


@pytest.mark.usefixtures("needs_yaml")
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


@pytest.mark.usefixtures("needs_yaml")
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


@pytest.mark.usefixtures("needs_jinja2")
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


@pytest.mark.usefixtures("needs_jinja2")
class TestJinja2TemplateNaming:
    """A .j2 template must keep the format extension it renders to."""

    def test_j2_without_inner_extension_names_the_template(self, tmp_path):
        template = tmp_path / "config.j2"
        template.write_text("a: 1\n", encoding="utf-8")

        with pytest.raises(NotImplementedError, match=r"config\.j2") as exc_info:
            ConfigLoader(base_dir=tmp_path).load("config.j2")
        assert "config.yaml.j2" in str(exc_info.value)

    def test_j2_without_inner_extension_names_the_template_without_pathlib_next(
        self, tmp_path, monkeypatch
    ):
        from yaconfiglib.backends import jinja2 as jinja2_backend

        monkeypatch.setattr(jinja2_backend, "MemPath", None)
        template = tmp_path / "config.j2"
        template.write_text("a: 1\n", encoding="utf-8")

        with pytest.raises(NotImplementedError, match=r"config\.j2") as exc_info:
            ConfigLoader(base_dir=tmp_path).load("config.j2")
        assert "config.yaml.j2" in str(exc_info.value)


class TestDotenvDispatch:
    @pytest.mark.parametrize(
        "name, expected",
        [
            pytest.param(
                "app.env.yaml",
                "YamlConfig",
                marks=_extras.needs_yaml,
            ),
            pytest.param(
                ".env.yaml",
                "YamlConfig",
                marks=_extras.needs_yaml,
            ),
            ("x.env.json", "JsonConfig"),
            pytest.param(
                "x.env.toml",
                "TomlConfig",
                marks=_extras.needs_toml,
            ),
            ("x.env.ini", "IniConfig"),
            pytest.param(
                ".env.j2",
                "Jinja2ConfigLoader",
                marks=_extras.needs_jinja2,
            ),
            pytest.param(
                "config.env.yaml.j2",
                "Jinja2ConfigLoader",
                marks=_extras.needs_jinja2,
            ),
            (".env", "DotenvBackend"),
            (".env.local", "DotenvBackend"),
            (".env.development.local", "DotenvBackend"),
            ("secrets.env", "DotenvBackend"),
        ],
    )
    def test_dispatch(self, name, expected):
        import pathlib

        assert ConfigBackend.get_class_by_path(pathlib.Path(name)).__name__ == expected

    def test_custom_backend_beats_dotenv(self):
        import pathlib
        import re

        class ZzzEnvFmt(ConfigBackend):
            PATHNAME_REGEX = re.compile(r".*\.zzzenvfmt$")
            NAME = "zzzenvfmt"

            def load(self, path, **options):
                return {}

        assert (
            ConfigBackend.get_class_by_path(pathlib.Path("x.env.zzzenvfmt"))
            is ZzzEnvFmt
        )

    @pytest.mark.usefixtures("needs_yaml")
    def test_env_yaml_file_loads_as_yaml(self, tmp_path):
        (tmp_path / "app.env.yaml").write_text("db:\n  host: h\n  port: 5432\n")
        result = ConfigLoader(base_dir=tmp_path).load("app.env.yaml")
        assert result == {"db": {"host": "h", "port": 5432}}

    @pytest.mark.usefixtures("needs_jinja2")
    def test_env_j2_renders(self, tmp_path):
        (tmp_path / ".env.j2").write_text("KEY={{ 1 + 1 }}\n")
        result = ConfigLoader(base_dir=tmp_path).load(".env.j2")
        assert result == {"key": "2"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_include_of_env_yaml_file(self, tmp_path):
        (tmp_path / "app.env.yaml").write_text("host: h\n")
        (tmp_path / "main.yaml").write_text("inc: !include app.env.yaml\n")
        result = ConfigLoader(base_dir=tmp_path).load("main.yaml")
        assert result == {"inc": {"host": "h"}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_unregistered_format_suffix_is_not_claimed(self, monkeypatch):
        import pathlib

        from yaconfiglib.backends.yaml import YamlConfig

        monkeypatch.setattr(YamlConfig, "PATHNAME_REGEX", None)
        with pytest.raises(NotImplementedError):
            ConfigBackend.get_class_by_path(pathlib.Path("app.env.yaml"))


class TestBackendIOContract:
    @pytest.mark.parametrize(
        "backend_name, name, body, expected",
        [
            ("json", "c.json", '{"a": 1}', {"a": 1}),
            ("ini", "c.ini", "[s]\na = 1\n", {"s": {"a": "1"}}),
            pytest.param(
                "toml",
                "c.toml",
                "a = 1\n",
                {"a": 1},
                marks=_extras.needs_toml,
            ),
            pytest.param(
                "jinja2",
                "c.yaml.j2",
                "a: {{ 1 }}\n",
                {"a": 1},
                marks=[_extras.needs_jinja2, _extras.needs_yaml],
            ),
        ],
    )
    def test_str_path_accepted(self, tmp_path, backend_name, name, body, expected):
        (tmp_path / name).write_text(body)
        backend = ConfigBackend.get_class_by_name(backend_name)()
        assert backend.load(str(tmp_path / name)) == expected

    @pytest.mark.parametrize(
        "tag, backend_name, name, body, expected",
        [
            ("!toml", "toml", "c.toml", "a = 1\n", {"a": 1}),
            ("!json", "json", "c.json", '{"a": 1}', {"a": 1}),
            ("!ini", "ini", "c.ini", "[s]\na = 1\n", {"s": {"a": "1"}}),
        ],
    )
    @pytest.mark.usefixtures("needs_yaml")
    def test_backend_as_yaml_tag_constructor(
        self, tmp_path, tag, backend_name, name, body, expected
    ):
        import yaml

        (tmp_path / name).write_text(body)

        class _L(yaml.SafeLoader):
            pass

        _L.add_constructor(tag, ConfigBackend.get_class_by_name(backend_name)())
        posix = (tmp_path / name).as_posix()
        assert yaml.load(f"x: {tag} '{posix}'", Loader=_L) == {"x": expected}

    @pytest.mark.parametrize(
        "name, body, check",
        [
            (".env", "DB_HOST=h\nDB_PORT=5432\n", {"db_host": "h", "db_port": "5432"}),
            ("c.json", '{"k": 1}', {"k": 1}),
            ("c.toml", "k = 1\n", {"k": 1}),
            ("c.ini", "[s]\nk = 1\n", {"s": {"k": "1"}}),
        ],
    )
    def test_utf8_bom_ignored(self, tmp_path, name, body, check):
        (tmp_path / name).write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
        assert ConfigLoader(base_dir=tmp_path).load(name) == check


class TestDotenvParsing:
    def test_multiline_double_quoted_value(self, tmp_path):
        (tmp_path / "c.env").write_text(
            'KEY="-----BEGIN KEY-----\nline2\n-----END KEY-----"\nPORT=1\n'
        )
        result = ConfigLoader(base_dir=tmp_path).load("c.env")
        assert result == {
            "key": "-----BEGIN KEY-----\nline2\n-----END KEY-----",
            "port": "1",
        }

    def test_multiline_single_quoted_value(self, tmp_path):
        (tmp_path / "c.env").write_text("KEY='a\nb'\nPORT=1\n")
        result = ConfigLoader(base_dir=tmp_path).load("c.env")
        assert result == {"key": "a\nb", "port": "1"}

    def test_unterminated_quote_raises(self, tmp_path):
        (tmp_path / "c.env").write_text('KEY="never closed\nPORT=1\n')
        with pytest.raises(ValueError) as excinfo:
            ConfigLoader(base_dir=tmp_path).load("c.env")
        assert "line 1" in str(excinfo.value)
        assert "KEY" in str(excinfo.value)

    def test_double_quoted_escapes_decoded(self, tmp_path):
        body = "".join(
            (
                'NL="a',
                chr(92),
                'nb"',
                "\n",
                'TAB="a',
                chr(92),
                'tb"',
                "\n",
                'QUOTE="say ',
                chr(92),
                '"hi',
                chr(92),
                '""',
                "\n",
                'BACK="C:',
                chr(92),
                chr(92),
                'tmp"',
                "\n",
            )
        )
        (tmp_path / "c.env").write_text(body)
        result = ConfigLoader(base_dir=tmp_path).load("c.env")
        assert result == {
            "nl": "a\nb",
            "tab": "a\tb",
            "quote": 'say "hi"',
            "back": "C:" + chr(92) + "tmp",
        }

    def test_unknown_escape_kept(self, tmp_path):
        (tmp_path / "c.env").write_text('KEY="a' + chr(92) + 'qb"\n')
        result = ConfigLoader(base_dir=tmp_path).load("c.env")
        assert result == {"key": "a" + chr(92) + "qb"}

    def test_single_quoted_value_is_raw(self, tmp_path):
        (tmp_path / "c.env").write_text("KEY='it" + chr(92) + "'s'\n")
        result = ConfigLoader(base_dir=tmp_path).load("c.env")
        assert result == {"key": "it" + chr(92) + "'s"}

    def test_dotted_and_dashed_keys(self, tmp_path):
        (tmp_path / "c.env").write_text("DOTTED.KEY=1\nDASH-KEY=2\n")
        result = ConfigLoader(base_dir=tmp_path).load("c.env")
        assert result == {"dotted.key": "1", "dash-key": "2"}

    def test_bare_key_line_warns_and_is_skipped(self, tmp_path, caplog):
        import logging

        (tmp_path / "c.env").write_text("NOEQ\nAFTER=ok\n")
        with caplog.at_level(logging.WARNING):
            result = ConfigLoader(base_dir=tmp_path).load("c.env")
        assert result == {"after": "ok"}
        assert "line 1" in caplog.text

    def test_apostrophe_in_unquoted_value_strips_comment(self, tmp_path):
        (tmp_path / "c.env").write_text("APOS=it's here # comment\n")
        result = ConfigLoader(base_dir=tmp_path).load("c.env")
        assert result == {"apos": "it's here"}

    def test_text_after_closing_quote_warns_and_skips(self, tmp_path, caplog):
        import logging

        (tmp_path / "c.env").write_text("S='it's here'\nAFTER=ok\n")
        with caplog.at_level(logging.WARNING):
            result = ConfigLoader(base_dir=tmp_path).load("c.env")
        assert result == {"after": "ok"}
        assert "line 1" in caplog.text

    def test_strict_raises_on_unparseable_line(self, tmp_path):
        from yaconfiglib.backends.dotenv import DotenvBackend

        (tmp_path / "c.env").write_text("NOEQ\nAFTER=ok\n")
        with pytest.raises(ValueError):
            DotenvBackend(strict=True).load(tmp_path / "c.env")

    def test_strict_raises_without_assignments(self, tmp_path):
        (tmp_path / "c.env").write_text("# only a comment\n\n")
        with pytest.raises(ValueError):
            ConfigLoader(base_dir=tmp_path).load("c.env", dotenv_strict=True)

    def test_unicode_line_separators_stay_in_value(self, tmp_path):
        separators = [
            "\x0b",
            "\x0c",
            "\x1c",
            "\x1d",
            "\x1e",
            "\x85",
            "\u2028",
            "\u2029",
        ]
        lines = ["K{}=x{}y".format(index, sep) for index, sep in enumerate(separators)]
        lines.append('D="q\u2028r"')
        lines.append("S='q\u2029r'")
        lines.append("C=3")
        (tmp_path / "u.env").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        result = ConfigLoader(base_dir=tmp_path).load("u.env")
        expected = {
            "k{}".format(index): "x{}y".format(sep)
            for index, sep in enumerate(separators)
        }
        expected["d"] = "q\u2028r"
        expected["s"] = "q\u2029r"
        expected["c"] = "3"
        assert result == expected

    def test_latin1_nel_byte_stays_in_value(self, tmp_path):
        (tmp_path / "c.env").write_bytes(b"MSG=wait\x85done\nPORT=1\n")
        result = ConfigLoader(base_dir=tmp_path).load("c.env", encoding="latin-1")
        assert result == {"msg": "wait\x85done", "port": "1"}


class TestCommandSniffing:
    @staticmethod
    def _run(tmp_path, body):
        script = tmp_path / "emit.py"
        script.write_text(body)
        return CommandBackend().load(f'cmd://"{sys.executable}" "{script}"')

    def test_ini_output_keeps_sections(self, tmp_path):
        result = self._run(
            tmp_path,
            "print('[db]')\nprint('host = dbhost')\nprint('[web]')\n"
            "print('port = 8080')\n",
        )
        assert result == {"db": {"host": "dbhost"}, "web": {"port": "8080"}}

    def test_dotenv_output_parsed_as_dotenv(self, tmp_path):
        result = self._run(
            tmp_path, "print('DB_HOST=localhost')\nprint('DB_PORT=5432')\n"
        )
        assert result == {"db_host": "localhost", "db_port": "5432"}

    def test_unparseable_output_returns_raw_string(self, tmp_path):
        result = self._run(tmp_path, "print('{{{ not: valid: [')\n")
        assert result.strip() == "{{{ not: valid: ["

    def test_plain_text_returns_raw_string(self, tmp_path):
        result = self._run(tmp_path, "print('hello world')\n")
        assert result.strip() == "hello world"

    def test_json_scalar_still_parsed(self, tmp_path):
        assert self._run(tmp_path, "print(42)\n") == 42


class TestIniBackend:
    def test_interpolation_none_keeps_percent_values(self, tmp_path):
        (tmp_path / "log.ini").write_text(
            "[formatter_generic]\n"
            "format = %(levelname)-5.5s [%(name)s] %(message)s\n"
            "[auth]\n"
            "password = p%ss\n"
            "ratio = 100%%\n"
        )
        result = ConfigLoader(base_dir=tmp_path).load("log.ini", ini_interpolation=None)
        assert result["formatter_generic"]["format"] == (
            "%(levelname)-5.5s [%(name)s] %(message)s"
        )
        assert result["auth"]["password"] == "p%ss"
        assert result["auth"]["ratio"] == "100%%"

    def test_extended_interpolation(self, tmp_path):
        (tmp_path / "e.ini").write_text("[s]\na = 1\n[t]\nb = ${s:a}-2\n")
        result = ConfigLoader(base_dir=tmp_path).load(
            "e.ini", ini_interpolation="extended"
        )
        assert result["t"]["b"] == "1-2"

    def test_invalid_interpolation_raises(self, tmp_path):
        (tmp_path / "c.ini").write_text("[s]\nk = v\n")
        with pytest.raises(ValueError):
            ConfigLoader(base_dir=tmp_path).load("c.ini", ini_interpolation="bogus")

    def test_basic_interpolation_is_default(self, tmp_path):
        (tmp_path / "c.ini").write_text("[s]\na = 1\nb = %(a)s-2\nc = 100%%\n")
        result = ConfigLoader(base_dir=tmp_path).load("c.ini")
        assert result["s"]["b"] == "1-2"
        assert result["s"]["c"] == "100%"

    def test_default_only_file_warns(self, tmp_path, caplog):
        import logging

        (tmp_path / "d.ini").write_text("[DEFAULT]\ntimeout = 30\n")
        with caplog.at_level(logging.WARNING):
            result = ConfigLoader(base_dir=tmp_path).load("d.ini")
        assert result == {}
        assert "ini_default_section" in caplog.text

    def test_default_section_escape_hatch(self, tmp_path):
        (tmp_path / "d.ini").write_text("[DEFAULT]\ntimeout = 30\n")
        result = ConfigLoader(base_dir=tmp_path).load(
            "d.ini", ini_default_section="__none__"
        )
        assert result == {"DEFAULT": {"timeout": "30"}}

    def test_cfg_extension_dispatches_to_ini(self, tmp_path):
        import pathlib

        from yaconfiglib.backends.ini import IniConfig

        (tmp_path / "app.cfg").write_text("[s]\nk = v\n")
        assert ConfigLoader(base_dir=tmp_path).load("app.cfg") == {"s": {"k": "v"}}
        assert ConfigBackend.get_class_by_path(pathlib.Path("x.env.cfg")) is IniConfig


class TestTomlParser:
    def test_heterogeneous_array(self, tmp_path):
        (tmp_path / "c.toml").write_text('a = [1, "two", 3.0]\n')
        assert ConfigLoader(base_dir=tmp_path).load("c.toml") == {"a": [1, "two", 3.0]}

    def test_lowercase_z_datetime_is_utc_aware(self, tmp_path):
        import datetime

        (tmp_path / "c.toml").write_text("d = 1979-05-27t07:32:00z\n")
        value = ConfigLoader(base_dir=tmp_path).load("c.toml")["d"]
        assert value == datetime.datetime(
            1979, 5, 27, 7, 32, tzinfo=datetime.timezone.utc
        )
        assert value.tzinfo is not None

    def test_multiline_basic_string_ending_quotes(self, tmp_path):
        (tmp_path / "c.toml").write_text('s = """a""""\n')
        assert ConfigLoader(base_dir=tmp_path).load("c.toml") == {"s": 'a"'}

    def test_offset_datetime_pickles(self, tmp_path):
        import pickle

        (tmp_path / "c.toml").write_text("d = 1979-05-27T00:32:00-08:00\n")
        value = ConfigLoader(base_dir=tmp_path).load("c.toml")["d"]
        assert pickle.loads(pickle.dumps(value)) == value


class TestMissingOptionalBackend:
    @staticmethod
    def _without(tmp_path, blocked, body):
        blocks = "\n".join(f"sys.modules[{name!r}] = None" for name in blocked)
        code = f"import sys\n{blocks}\nimport yaconfiglib\n{body}"
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

    def test_toml_path_names_extra(self, tmp_path):
        (tmp_path / "cfg.toml").write_text("a = 1\n")
        done = self._without(
            tmp_path,
            ("tomllib", "tomli"),
            "try:\n"
            "    yaconfiglib.load('cfg.toml')\n"
            "except Exception as exc:\n"
            "    print(type(exc).__name__, exc)\n",
        )
        assert "yaconfiglib[toml]" in done.stdout, done.stdout + done.stderr
        assert "No backend reads" in done.stdout

    def test_toml_loader_name_names_extra(self, tmp_path):
        done = self._without(
            tmp_path,
            ("tomllib", "tomli"),
            "try:\n"
            "    yaconfiglib.loads('a = 1', loader='toml')\n"
            "except Exception as exc:\n"
            "    print(type(exc).__name__, exc)\n",
        )
        assert "yaconfiglib[toml]" in done.stdout, done.stdout + done.stderr
        assert "Unknown configuration format/loader" in done.stdout

    def test_yaml_path_names_extra(self, tmp_path):
        (tmp_path / "cfg.yaml").write_text("a: 1\n")
        done = self._without(
            tmp_path,
            ("yaml",),
            "try:\n"
            "    yaconfiglib.load('cfg.yaml')\n"
            "except Exception as exc:\n"
            "    print(type(exc).__name__, exc)\n",
        )
        assert "yaconfiglib[yaml]" in done.stdout, done.stdout + done.stderr

    @pytest.mark.usefixtures("needs_jinja2", "needs_toml", "needs_yaml")
    def test_optional_backend_table_matches_classes(self):
        from yaconfiglib.backends import _OPTIONAL_BACKENDS
        from yaconfiglib.backends.ini import IniConfig  # noqa: F401 - registry

        for name, (pattern, _hint) in _OPTIONAL_BACKENDS.items():
            cls = ConfigBackend.get_class_by_name(name)
            assert cls is not None, name
            assert cls.PATHNAME_REGEX.pattern == pattern, name


class TestEnvVarCollisions:
    def test_scalar_then_nested_raises(self, monkeypatch):
        monkeypatch.setenv("ZZENV7_DB", "sqlite")
        monkeypatch.setenv("ZZENV7_DB__PORT", "5432")
        with pytest.raises(ValueError) as excinfo:
            EnvVarBackend().load(prefix="ZZENV7_", nested_delimiter="__")
        assert "ZZENV7_DB" in str(excinfo.value)
        assert "ZZENV7_DB__PORT" in str(excinfo.value)

    def test_nested_then_scalar_raises(self, monkeypatch):
        monkeypatch.setenv("ZZENV7_DB__PORT", "5432")
        monkeypatch.setenv("ZZENV7_DB", "sqlite")
        with pytest.raises(ValueError) as excinfo:
            EnvVarBackend().load(prefix="ZZENV7_", nested_delimiter="__")
        assert "ZZENV7_DB" in str(excinfo.value)
        assert "ZZENV7_DB__PORT" in str(excinfo.value)

    def test_empty_key_after_prefix_is_skipped(self, monkeypatch):
        monkeypatch.setenv("ZZENV7_", "d")
        monkeypatch.setenv("ZZENV7_KEEP", "k")
        result = EnvVarBackend().load(prefix="ZZENV7_")
        assert result == {"keep": "k"}

    def test_prefix_case_insensitive_when_flag_set(self, monkeypatch):
        from yaconfiglib.backends import env as env_module

        monkeypatch.setattr(env_module, "_ENV_KEYS_CASE_INSENSITIVE", True)
        monkeypatch.setenv("ZZENV7_LOWER", "x")
        assert EnvVarBackend().load(prefix="zzenv7_") == {"lower": "x"}

    def test_prefix_case_sensitive_when_flag_clear(self, monkeypatch):
        # Pre-fix this fails only because the flag does not exist yet, so
        # monkeypatch.setattr raises.
        from yaconfiglib.backends import env as env_module

        monkeypatch.setattr(env_module, "_ENV_KEYS_CASE_INSENSITIVE", False)
        monkeypatch.setenv("ZZENV7_LOWER", "x")
        assert EnvVarBackend().load(prefix="zzenv7_") == {}


@pytest.mark.usefixtures("needs_yaml")
class TestPythonBackendDocs:
    def test_documented_layering_example(self, tmp_path):
        from yaconfiglib import ConfigLoaderMergeMethod
        from yaconfiglib.backends.python_backend import PythonBackend

        (tmp_path / "base.yaml").write_text("base_key: from_base\noverride_key: base\n")
        loader = ConfigLoader(base_dir=tmp_path)
        base = loader.load("base.yaml")
        override = loader.load(loader=PythonBackend({"override_key": "override_value"}))
        config = ConfigLoaderMergeMethod.Deep(base, override)
        assert config == {
            "base_key": "from_base",
            "override_key": "override_value",
        }


class TestCommandSourceText:
    """A command's text reaches the shell exactly as written."""

    def test_command_text_reaches_the_shell_unchanged(self):
        script = "import sys,json; print(json.dumps(sys.argv[1:]))"
        source = (
            f'cmd+json://python -c "{script}" '
            "prod/db https://vault.example.com/v1/secret a//b ./rel s/./x/"
        )
        # Through the loader, so parse_sources' path factory gets a chance to
        # rewrite the text - which is the defect this pins.
        assert ConfigLoader().load(source) == [
            "prod/db",
            "https://vault.example.com/v1/secret",
            "a//b",
            "./rel",
            "s/./x/",
        ]

    def test_command_with_python_division(self):
        assert ConfigLoader().load('cmd+json://python -c "print(10/4)"') == 2.5

    def test_parse_sources_yields_command_source(self):
        import pathlib

        from pathlib_next import LocalPath

        from yaconfiglib.utils.source import CommandSource, parse_sources

        yielded = list(parse_sources(["cmd+json://echo a//b"]))
        assert yielded == ["cmd+json://echo a//b"]
        source = yielded[0]
        assert isinstance(source, CommandSource)
        assert (source.scheme, source.format, source.command) == (
            "cmd+json",
            "json",
            "echo a//b",
        )
        assert CommandSource("CMD+JSON://echo 1").format == "json"
        assert CommandSource("cmd:/usr/bin/env").command == "/usr/bin/env"
        # A path object is a file, whatever its text says.
        assert not isinstance(
            next(iter(parse_sources([LocalPath("sh:x.json")]))), CommandSource
        )

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("sh:hosts.json", "JsonConfig"),
            ("cmd:settings.yaml", "YamlConfig"),
            ("exec:pyproject.toml", "TomlConfig"),
            ("SH:HOSTS.JSON", "JsonConfig"),
        ],
    )
    def test_scheme_named_file_dispatches_by_extension(self, name, expected):
        from pathlib_next import LocalPath

        got = ConfigBackend.get_class_by_path(LocalPath("conf") / name)
        assert got.__name__ == expected

    def test_hash_merge_key_for_command_is_its_source_text(self):
        first = 'cmd+json://python -c "print(1)"'
        second = 'cmd+json://python -c "import json; print(json.dumps(2))"'
        loaded = ConfigLoader(merge="hash").load(first, second)
        assert sorted(loaded) == sorted([first, second])

    def test_transform_pathname_for_command_is_its_source_text(self):
        source = 'cmd+json://python -c "print(1)"'
        assert ConfigLoader().load(source, transform="pathname.name") == source

    def test_can_load_path_on_paths_matches_script_extension_only(self):
        import pathlib

        assert CommandBackend.can_load_path(pathlib.Path("gen.SH")) is True
        assert CommandBackend.can_load_path(pathlib.Path("app.env")) is False
        assert CommandBackend.can_load_path(pathlib.PurePosixPath("x.yaml")) is False
        assert CommandBackend.can_load_path("cmd://echo 1") is True


WIN = sys.platform == "win32"


def _script(directory, stem):
    """Write a script named *stem* that prints {"ok": true}; return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    if WIN:
        path = directory / f"{stem}.bat"
        path.write_text('@echo off\necho {"ok": true}\n', encoding="utf-8")
    else:
        path = directory / f"{stem}.sh"
        path.write_text("#!/bin/sh\necho '{\"ok\": true}'\n", encoding="utf-8")
        path.chmod(0o755)
    return path


class TestScriptLaunch:
    """A script file is launched through its interpreter, never through a shell."""

    MARK = "MARK"

    def _mark(self, tmp_path):
        return tmp_path / self.MARK

    def test_script_name_metacharacter_direct(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stem = "x&copy nul MARK&" if WIN else "a;touch MARK;"
        path = _script(tmp_path / "conf", stem)
        loader = ConfigLoader(base_dir=str(tmp_path))
        assert loader.load(f"conf/{path.name}") == {"ok": True}
        assert not (tmp_path / "conf" / self.MARK).exists()
        assert not self._mark(tmp_path).exists()

    def test_script_name_metacharacter_glob(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stem = "x&copy nul MARK&" if WIN else "a;touch MARK;"
        _script(tmp_path / "conf", stem)
        loader = ConfigLoader(base_dir=str(tmp_path))
        assert loader.load("conf/*") == {"ok": True}
        assert not (tmp_path / "conf" / self.MARK).exists()

    def test_script_name_metacharacter_ignore_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stem = "x&copy nul MARK&" if WIN else "a;touch MARK;"
        _script(tmp_path / "conf", stem)
        loader = ConfigLoader(base_dir=str(tmp_path), ignore_error=True)
        assert loader.load("conf/*") == {"ok": True}
        assert not (tmp_path / "conf" / self.MARK).exists()

    def test_script_dir_with_ampersand_and_space(self, tmp_path):
        path = _script(tmp_path / "R&D dir", "gen")
        assert ConfigLoader().load(str(path)) == {"ok": True}

    @pytest.mark.skipif(not WIN, reason="Windows batch files only")
    def test_percent_in_batch_path_refused(self, tmp_path):
        path = _script(tmp_path / "conf", "%OS%")
        with pytest.raises(ValueError, match="%"):
            ConfigLoader().load(str(path))

    @pytest.mark.skipif(not WIN, reason="Windows batch files only")
    def test_caret_in_batch_path_runs(self, tmp_path):
        path = _script(tmp_path / "conf", "a^b")
        assert ConfigLoader().load(str(path)) == {"ok": True}

    def test_legit_named_script_runs(self, tmp_path):
        path = _script(tmp_path, "gen")
        assert ConfigLoader().load(str(path)) == {"ok": True}

    @pytest.mark.skipif(not WIN, reason="Windows .cmd only")
    def test_cmd_extension_runs(self, tmp_path):
        path = tmp_path / "gen.cmd"
        path.write_text('@echo off\necho {"ok": true}\n', encoding="utf-8")
        assert ConfigLoader().load(str(path)) == {"ok": True}

    @pytest.mark.skipif(WIN, reason="POSIX permissions only")
    def test_non_executable_sh_runs(self, tmp_path):
        path = tmp_path / "gen.sh"
        path.write_text("echo '{\"ok\": true}'\n", encoding="utf-8")
        path.chmod(0o644)
        assert ConfigLoader().load(str(path)) == {"ok": True}

    @pytest.mark.skipif(WIN, reason="POSIX only")
    def test_bat_on_posix_raises(self, tmp_path):
        path = tmp_path / "gen.bat"
        path.write_text('echo {"ok": true}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="batch"):
            ConfigLoader().load(str(path))

    @pytest.mark.skipif(WIN, reason="POSIX only")
    def test_executable_shebang_script_runs(self, tmp_path):
        path = _script(tmp_path, "gen")
        assert ConfigLoader().load(str(path)) == {"ok": True}

    def test_in_memory_script_runs_body_mempath(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # A decoy of the same name on disk: the memory body must win.
        decoy = _script(tmp_path, "gen")
        decoy.write_text(
            (
                '@echo off\necho {"ok": false}\n'
                if WIN
                else "#!/bin/sh\necho '{\"ok\": false}'\n"
            ),
            encoding="utf-8",
        )
        name = "gen.bat" if WIN else "gen.sh"
        body = (
            f'#!{name}\n@echo off\necho {{"who": "memory"}}\n'
            if WIN
            else f'#!{name}\n#!/bin/sh\necho \'{{"who": "memory"}}\'\n'
        )
        assert ConfigLoader().load(body, loader="command") == {"who": "memory"}

    def test_in_memory_script_runs_body_tempfile(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("yaconfiglib.utils.source.MemPath", None)
        name = "gen.bat" if WIN else "gen.sh"
        body = (
            f'#!{name}\n@echo off\necho {{"who": "memory"}}\n'
            if WIN
            else f'#!{name}\n#!/bin/sh\necho \'{{"who": "memory"}}\'\n'
        )
        assert ConfigLoader().load(body, loader="command") == {"who": "memory"}

    def test_in_memory_without_script_extension_refused(self, tmp_path, monkeypatch):
        import yaconfiglib

        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="script extension"):
            yaconfiglib.loads("echo 1", loader="command")

    def test_loads_command_loader_named_non_script_refused(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="script extension"):
            ConfigLoader().load('#!data.yaml\n{"a": 1}', loader="command")

    # --- Windows interpreter cases. Named "gui" because a mis-dispatch opens
    # the file association instead of running the script.

    @staticmethod
    def _powershell_allows_scripts():
        import shutil
        import subprocess

        exe = shutil.which("pwsh") or shutil.which("powershell")
        if not exe:
            return False
        done = subprocess.run(
            [exe, "-NoProfile", "-Command", "Get-ExecutionPolicy"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return done.stdout.strip() not in ("Restricted", "AllSigned")

    @pytest.mark.skipif(not WIN, reason="Windows PowerShell only")
    def test_ps1_runs_powershell_gui(self, tmp_path):
        if not self._powershell_allows_scripts():
            pytest.skip("PowerShell execution policy forbids running scripts")
        path = tmp_path / "it's R&D.ps1"
        path.write_text("Write-Output '{\"ok\": true}'\n", encoding="utf-8")
        assert ConfigLoader().load(str(path)) == {"ok": True}

    @pytest.mark.skipif(not WIN, reason="Windows PowerShell only")
    def test_ps1_metacharacter_path_gui(self, tmp_path):
        if not self._powershell_allows_scripts():
            pytest.skip("PowerShell execution policy forbids running scripts")
        directory = tmp_path / "R&D dir"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "gen.ps1"
        path.write_text("Write-Output '{\"ok\": true}'\n", encoding="utf-8")
        assert ConfigLoader().load(str(path)) == {"ok": True}

    @pytest.mark.skipif(not WIN, reason="Windows .sh-through-sh only")
    def test_sh_on_windows_uses_sh_gui(self, tmp_path):
        import shutil

        if not shutil.which("sh"):
            pytest.skip("no 'sh' on PATH")
        path = tmp_path / "gen.sh"
        path.write_text("echo '{\"ok\": true}'\n", encoding="utf-8")
        assert ConfigLoader().load(str(path)) == {"ok": True}


class TestCommandOutputDecoding:
    """Command output is decoded strictly, so corruption is not silent."""

    @staticmethod
    def _emit(tmp_path, body):
        script = tmp_path / "emit.py"
        script.write_text(body, encoding="utf-8")
        return f'cmd://"{sys.executable}" "{script}"'

    def test_invalid_utf8_output_raises(self, tmp_path):
        source = self._emit(
            tmp_path,
            "import sys\nsys.stdout.buffer.write(bytes([99, 97, 102, 233]))\n",
        )
        with pytest.raises(ValueError, match="encoding="):
            CommandBackend().load(source)

    def test_explicit_encoding_decodes(self, tmp_path):
        source = self._emit(
            tmp_path,
            "import sys\nsys.stdout.buffer.write(bytes([99, 97, 102, 233]))\n",
        )
        assert CommandBackend().load(source, encoding="latin-1") == "caf\xe9"

    def test_crlf_output_normalized(self, tmp_path):
        pytest.importorskip("yaml")
        # The child writes real CRLFs, assembled here so no escape survives a
        # round trip through the file it writes.
        crlf = "chr(13) + chr(10)"
        body = (
            "import sys\n"
            f"nl = {crlf}\n"
            "out = nl.join(['#!yaml', 'x: |', '  l1', '  l2', 'y: 2', ''])\n"
            "sys.stdout.buffer.write(out.encode('utf-8'))\n"
        )
        source = self._emit(tmp_path, body)
        assert CommandBackend().load(source) == {"x": "l1\nl2\n", "y": 2}

    def test_failed_command_status_wins_over_decode(self, tmp_path):
        source = self._emit(
            tmp_path,
            "import sys\nsys.stdout.buffer.write(bytes([233]))\nsys.exit(3)\n",
        )
        with pytest.raises(subprocess.CalledProcessError) as excinfo:
            CommandBackend().load(source)
        assert excinfo.value.returncode == 3
        assert isinstance(excinfo.value.stderr, str)


class TestCommandErrors:
    """A wrapped tool is no harder to diagnose than the tool run directly.

    A failing command reported only its exit status — the reason it printed to
    stderr was reachable only by inspecting the exception — and format sniffing
    swallowed every exception, so a missing `!include` inside a command's output
    became an empty mapping instead of an error.
    """

    @staticmethod
    def _emit(tmp_path, body, name="emit.py", scheme="cmd"):
        script = tmp_path / name
        script.write_text(body, encoding="utf-8")
        return f'{scheme}://"{sys.executable}" "{script}"'

    def test_nonzero_exit_raises_command_error_with_stderr_tail(self, tmp_path):
        import subprocess

        import yaconfiglib

        source = self._emit(
            tmp_path,
            "import sys\n"
            "sys.stderr.write('AccessDeniedException: token expired\\n')\n"
            "sys.exit(3)\n",
        )
        with pytest.raises(yaconfiglib.CommandError) as caught:
            CommandBackend().load(source, timeout=60)
        error = caught.value
        assert isinstance(error, subprocess.CalledProcessError)
        assert error.returncode == 3
        # The reason is in the message now, not only in .stderr.
        assert "token expired" in str(error)

    def test_command_error_still_caught_as_called_process_error(self, tmp_path):
        import subprocess

        source = self._emit(tmp_path, "import sys\nsys.exit(42)\n")
        # The pin: code written against the stdlib type keeps working.
        with pytest.raises(subprocess.CalledProcessError) as caught:
            CommandBackend().load(source, timeout=60)
        assert caught.value.returncode == 42

    def test_stderr_tail_is_bounded(self, tmp_path):
        import yaconfiglib

        source = self._emit(
            tmp_path,
            "import sys\n"
            "for i in range(500):\n"
            "    sys.stderr.write('L%04d\\n' % i)\n"
            "sys.exit(1)\n",
        )
        with pytest.raises(yaconfiglib.CommandError) as caught:
            CommandBackend().load(source, timeout=60)
        message = str(caught.value)
        # The tail is what a tool puts its reason in; the head is noise.
        assert "L0499" in message
        assert "L0000" not in message
        assert len(message) < 4000

    def test_stdout_never_in_error_message(self, tmp_path):
        import subprocess

        source = self._emit(
            tmp_path,
            "import sys\nprint('TOPSECRET')\nsys.stderr.write('failed\\n')\n"
            "sys.exit(1)\n",
        )
        # Caught as the stdlib type, so this is a true pin: it held before the
        # stderr tail was added and must still hold after.
        with pytest.raises(subprocess.CalledProcessError) as caught:
            CommandBackend().load(source, timeout=60)
        # stdout is the payload — frequently the very secret being fetched.
        assert "TOPSECRET" not in str(caught.value)
        assert "TOPSECRET" in caught.value.output

    def test_timeout_raises_command_timeout_error(self, tmp_path):
        import subprocess

        import yaconfiglib

        source = self._emit(tmp_path, "import time\ntime.sleep(15)\n")
        with pytest.raises(yaconfiglib.CommandTimeoutError) as caught:
            CommandBackend().load(source, timeout=1)
        assert isinstance(caught.value, subprocess.TimeoutExpired)
        assert isinstance(caught.value, yaconfiglib.ConfigError)

    def test_successful_command_stderr_logged_at_debug(self, tmp_path, caplog):
        import logging

        caplog.set_level(logging.DEBUG, logger="yaconfiglib.backends.command")
        source = self._emit(
            tmp_path,
            "import sys\nsys.stderr.write('deprecation warning\\n')\nprint('a: 1')\n",
        )
        assert CommandBackend().load(source, timeout=60) == {"a": 1}
        # A tool's warning used to vanish entirely on a successful run.
        assert any(
            "deprecation warning" in record.getMessage() for record in caplog.records
        )

    def test_sniffing_propagates_missing_include_in_output(self, tmp_path):
        from yaconfiglib import ConfigLoader

        source = self._emit(
            tmp_path, "print('db: !include nope_secret.yaml')\n", name="inc.py"
        )
        loader = ConfigLoader(base_dir=str(tmp_path))
        # Used to sniff past it and return {} — a missing secrets file must not
        # read as an empty config.
        with pytest.raises(FileNotFoundError):
            loader.load(source, timeout=60)

    def test_sniffing_propagates_nested_include_parse_error(self, tmp_path):
        import json

        from yaconfiglib import ConfigLoader

        (tmp_path / "bad.json").write_text('{"a": }\n', encoding="utf-8")
        source = self._emit(
            tmp_path, "print('db: !include bad.json')\n", name="inc2.py"
        )
        loader = ConfigLoader(base_dir=str(tmp_path))
        with pytest.raises(json.JSONDecodeError) as caught:
            loader.load(source, timeout=60)
        kinds = [f.kind for f in getattr(caught.value, "config_frames", ())]
        assert "include" in kinds

    def test_format_list_failure_names_command_each_format_and_chains(self, tmp_path):
        import yaconfiglib

        source = self._emit(tmp_path, "print('not structured at all')\n")
        with pytest.raises(yaconfiglib.ConfigValueError) as caught:
            CommandBackend().load(source, format="json,toml", timeout=60)
        error = caught.value
        assert isinstance(error, ValueError)
        message = str(error)
        assert "emit.py" in message
        assert "json:" in message and "toml:" in message
        assert error.__cause__ is not None

    def test_single_format_failure_keeps_type_and_names_command(self, tmp_path):
        import json

        source = self._emit(tmp_path, "print('not json')\n")
        with pytest.raises(json.JSONDecodeError) as caught:
            CommandBackend().load(source, format="json", timeout=60)
        assert "emit.py" in str(caught.value)

    def test_empty_output_with_format_names_command(self, tmp_path):
        import yaconfiglib

        source = self._emit(tmp_path, "pass\n")
        with pytest.raises(yaconfiglib.ConfigValueError) as caught:
            CommandBackend().load(source, format="json", timeout=60)
        assert "emit.py" in str(caught.value)

    def test_sniffing_deeply_nested_output_does_not_raise(self, tmp_path):
        source = self._emit(
            tmp_path,
            "print('[' * 5000)\n",
            name="deep.py",
        )
        # The pin: a parser exhausted by depth is a parse failure, so sniffing
        # falls through to the raw string rather than crashing the load.
        result = CommandBackend().load(source, timeout=60)
        assert isinstance(result, str)

    def test_command_source_value_errors_are_config_errors(self, tmp_path):
        import yaconfiglib

        source = self._emit(
            tmp_path,
            "import sys\nsys.stdout.buffer.write(bytes([99, 97, 102, 233]))\n",
            name="bad_codec.py",
        )
        with pytest.raises(yaconfiglib.ConfigValueError) as caught:
            CommandBackend().load(source, timeout=60)
        assert isinstance(caught.value, ValueError)
        assert isinstance(caught.value.__cause__, UnicodeDecodeError)
        # An in-memory source with no script extension, through loads().
        with pytest.raises(yaconfiglib.ConfigValueError):
            yaconfiglib.loads("echo 1", loader="command")


@pytest.mark.usefixtures("needs_yaml")
class TestCommandOutputIncludeEncoding:
    """A command's output is parsed with the codec it was decoded with.

    `encoding` is a named parameter of `CommandBackend.load`, so it was absent
    from the options forwarded to the inner `loads()`: the output document, and
    every `!include` inside it, were read as UTF-8. A non-UTF-8 include then
    raised `UnicodeDecodeError` with a hint telling the caller to pass the very
    encoding they had passed.
    """

    @staticmethod
    def _emit(tmp_path, body, name="emit.py"):
        script = tmp_path / name
        script.write_text(body, encoding="utf-8")
        return f'cmd://"{sys.executable}" "{script}"'

    @staticmethod
    def _cp1252_include(tmp_path):
        (tmp_path / "sub.yaml").write_bytes("inner: café\n".encode("cp1252"))

    def test_per_call_encoding_reaches_output_include(self, tmp_path):
        from yaconfiglib import ConfigLoader

        self._cp1252_include(tmp_path)
        source = self._emit(tmp_path, "print('sub: !include sub.yaml')\n")
        loader = ConfigLoader(base_dir=str(tmp_path))
        result = loader.load(source, encoding="cp1252", timeout=60)
        assert ascii(result["sub"]["inner"]) == ascii("café")

    def test_constructor_encoding_reaches_output_include(self, tmp_path):
        from yaconfiglib import ConfigLoader

        self._cp1252_include(tmp_path)
        source = self._emit(tmp_path, "print('sub: !include sub.yaml')\n")
        loader = ConfigLoader(base_dir=str(tmp_path), encoding="cp1252")
        result = loader.load(source, timeout=60)
        assert ascii(result["sub"]["inner"]) == ascii("café")

    def test_output_round_trips_through_the_declared_codec(self, tmp_path):
        from yaconfiglib import ConfigLoader

        # The output is DECODED with the call's codec, so by construction it
        # only ever holds characters that codec can represent — re-encoding the
        # in-memory document can therefore never fall back to UTF-8 for a
        # command source. This pins that round trip; a command that emits some
        # other codec than the one declared is simply mojibake, as it would be
        # for a file.
        source = self._emit(
            tmp_path,
            "import sys\nsys.stdout.buffer.write('msg: caf\\xe9\\n'.encode('cp1252'))\n",
            name="wide.py",
        )
        loader = ConfigLoader(base_dir=str(tmp_path), encoding="cp1252")
        result = loader.load(source, timeout=60)
        assert ascii(result["msg"]) == ascii("café")

    def test_mapping_form_encoding_applies_to_output_includes(self, tmp_path):
        from yaconfiglib import ConfigLoader

        self._cp1252_include(tmp_path)
        source = self._emit(tmp_path, "print('sub: !include sub.yaml')\n")
        (tmp_path / "parent.yaml").write_text(
            "cmd: !include {pathname: '"
            + source.replace("\\", "/")
            + "', encoding: cp1252}\n",
            encoding="utf-8",
        )
        loader = ConfigLoader(base_dir=str(tmp_path))
        result = loader.load("parent.yaml", timeout=60)
        # Pins the chosen rule: a mapping-form encoding governs the command's
        # output AND what that output includes.
        assert ascii(result["cmd"]["sub"]["inner"]) == ascii("café")
