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
            "DB_PASS=\"secret # preserved\"\n"
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
            f.write_text("@echo off\necho {\"win\": true}\n")
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
        cmd = "cmd://python -c \"import sys; sys.exit(42)\""
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            loader.load(cmd)
        assert exc_info.value.returncode == 42

    def test_cmd_execution_empty(self):
        loader = ConfigLoader()
        # Empty output should be handled safely (returns empty raw string)
        cmd = "cmd://python -c \"pass\""
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
                ConfigBackend.get_class_by_path(StdPath("x.zzztest"))
                is ZzzFirstBackend
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
