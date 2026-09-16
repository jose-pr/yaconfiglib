"""Tests for the ConfigError hierarchy and the load_error_types() catch-all.

The library raised bare `ValueError`/`TypeError`/`NotImplementedError`, with no
common base, so a caller could not tell a configuration problem from a bug
without matching message text. Every class here keeps the builtin base it raised
before, which is what the pin test guards.
"""

import pytest


class TestErrorHierarchy:
    def test_config_error_exported(self):
        import yaconfiglib

        assert issubclass(yaconfiglib.ConfigError, Exception)

    def test_commands_disabled_error_is_config_and_value_error(self):
        import yaconfiglib
        import yaconfiglib.utils.trust

        # Same class through both import paths: `except CommandsDisabledError`
        # written against either must match.
        assert (
            yaconfiglib.CommandsDisabledError
            is yaconfiglib.utils.trust.CommandsDisabledError
        )
        loader = yaconfiglib.ConfigLoader(allow_commands=False)
        with pytest.raises(yaconfiglib.ConfigError) as caught:
            loader.load("cmd+json://echo {}")
        assert isinstance(caught.value, yaconfiglib.CommandsDisabledError)
        assert isinstance(caught.value, ValueError)

    def test_unsupported_format_error_is_not_implemented_error(self, tmp_path):
        import yaconfiglib

        source = tmp_path / "plain.xyz"
        source.write_text("a: 1\n", encoding="utf-8")
        with pytest.raises(yaconfiglib.UnsupportedFormatError) as caught:
            yaconfiglib.load(str(source))
        assert isinstance(caught.value, NotImplementedError)

    def test_unsupported_format_message_lists_loader_names(self, tmp_path):
        import yaconfiglib

        source = tmp_path / "plain.xyz"
        source.write_text("a: 1\n", encoding="utf-8")
        with pytest.raises(yaconfiglib.UnsupportedFormatError) as caught:
            yaconfiglib.load(str(source))
        message = str(caught.value)
        # The names are what loader= accepts, which a regex pattern was not.
        assert "plain.xyz" in message
        assert "json" in message and "yaml" in message
        assert "Not reader" not in message

    def test_unknown_loader_error_keeps_prefix_and_lists_names(self, tmp_path):
        import yaconfiglib

        source = tmp_path / "a.yaml"
        source.write_text("a: 1\n", encoding="utf-8")
        with pytest.raises(yaconfiglib.UnknownLoaderError) as caught:
            yaconfiglib.load(str(source), loader="jsn")
        message = str(caught.value)
        assert isinstance(caught.value, ValueError)
        assert message.startswith("Unknown configuration format/loader:")
        assert "json" in message

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_error_types_contents(self):
        import configparser
        import json

        import yaml

        import yaconfiglib

        types = yaconfiglib.load_error_types()
        for expected in (
            yaconfiglib.ConfigError,
            OSError,
            json.JSONDecodeError,
            configparser.Error,
            yaml.YAMLError,
        ):
            assert expected in types, expected
        # A library bug must still crash rather than read as a config error.
        for unwanted in (ValueError, TypeError, KeyError):
            assert unwanted not in types, unwanted

    def test_builtin_except_clauses_still_match(self, tmp_path):
        import yaconfiglib

        # The pin: code written against the old bare types keeps working.
        with pytest.raises(ValueError):
            yaconfiglib.ConfigLoader(allow_commands=False).load("cmd+json://echo {}")
        source = tmp_path / "plain.xyz"
        source.write_text("a: 1\n", encoding="utf-8")
        with pytest.raises(NotImplementedError):
            yaconfiglib.load(str(source))

    @pytest.mark.usefixtures("needs_yaml")
    def test_include_cycle_error_is_config_value_error(self, tmp_path):
        import yaconfiglib

        (tmp_path / "a.yaml").write_text("b: !include b.yaml\n", encoding="utf-8")
        (tmp_path / "b.yaml").write_text("a: !include a.yaml\n", encoding="utf-8")
        with pytest.raises(yaconfiglib.ConfigValueError, match="include cycle"):
            yaconfiglib.load(str(tmp_path / "a.yaml"))

    @pytest.mark.usefixtures("needs_yaml")
    @pytest.mark.usefixtures("needs_jinja2")
    def test_interpolation_cycle_error_is_config_value_error(self, tmp_path):
        import yaconfiglib

        source = tmp_path / "a.yaml"
        source.write_text('a: "{{ b }}"\nb: "{{ a }}"\n', encoding="utf-8")
        with pytest.raises(yaconfiglib.ConfigValueError, match="reference cycle"):
            yaconfiglib.load(str(source), interpolate=True, strict=True)

    @pytest.mark.usefixtures("needs_yaml")
    def test_flatten_member_error_is_config_type_error(self, tmp_path):
        import yaconfiglib

        source = tmp_path / "scalar.yaml"
        source.write_text("just a string\n", encoding="utf-8")
        with pytest.raises(yaconfiglib.ConfigTypeError, match="flatten=True"):
            yaconfiglib.load(str(source), merge="hash", flatten=True)

    def test_dotenv_strict_error_is_config_value_error(self, tmp_path):
        import yaconfiglib
        from yaconfiglib.backends.dotenv import DotenvBackend

        source = tmp_path / ".env"
        source.write_text('A="unterminated\n', encoding="utf-8")
        with pytest.raises(yaconfiglib.ConfigValueError):
            DotenvBackend(strict=True).load(str(source))

    def test_env_collision_error_is_config_value_error(self, monkeypatch):
        import yaconfiglib
        from yaconfiglib.backends.env import EnvVarBackend

        # A prefix no real environment sets, so the collision is the test's own.
        monkeypatch.setenv("YACFGERR_DB", "1")
        monkeypatch.setenv("YACFGERR_DB__PORT", "2")
        backend = EnvVarBackend(prefix="YACFGERR_", nested_delimiter="__")
        with pytest.raises(yaconfiglib.ConfigValueError):
            backend.load(None)

    @pytest.mark.usefixtures("needs_jinja2")
    def test_j2_without_inner_extension_is_unsupported_format_error(self, tmp_path):
        import yaconfiglib

        source = tmp_path / "config.j2"
        source.write_text("a: 1\n", encoding="utf-8")
        with pytest.raises(yaconfiglib.UnsupportedFormatError):
            yaconfiglib.load(str(source))
