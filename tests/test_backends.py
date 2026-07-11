from __future__ import annotations

import os
import sys
import pytest
import subprocess

from yaconfiglib import ConfigLoader
from yaconfiglib.backends.dotenv import DotenvBackend
from yaconfiglib.backends.env import EnvVarBackend
from yaconfiglib.backends.python_backend import PythonBackend
from yaconfiglib.backends.command import CommandBackend


class TestRegistryBackends:
    def test_dotenv_backend(self, tmp_path):
        f = tmp_path / "test.env"
        f.write_text("DB_HOST=127.0.0.1\nexport DB_PORT=5432\n# Comment\nDB_PASS=\"secret\"\n")
        
        loader = ConfigLoader(base_dir=tmp_path)
        result = loader.load("test.env", loader="dotenv")
        assert result == {"db_host": "127.0.0.1", "db_port": "5432", "db_pass": "secret"}

    def test_env_var_backend(self, monkeypatch):
        monkeypatch.setenv("TESTPREFIX_VAL_ONE", "hello")
        monkeypatch.setenv("TESTPREFIX_VAL_TWO", "world")
        monkeypatch.setenv("OTHER_VAR", "ignored")

        loader = ConfigLoader()
        result = loader.load(loader=EnvVarBackend(prefix="TESTPREFIX_"))
        assert result == {"val_one": "hello", "val_two": "world"}

    def test_python_backend(self):
        loader = ConfigLoader()
        data = {"foo": "bar", "nested": [1, 2]}
        result = loader.load(loader=PythonBackend(data))
        assert result == data


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
