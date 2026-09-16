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


@pytest.mark.usefixtures("needs_yaml")
class TestSourceAttribution:
    """A failing source names itself, without changing what callers catch.

    A glob member's YAML error said `in "<unicode string>"`, an INI error said
    only `app.ini`, a template error said `File "<unknown>"`, and an error
    inside an included file said nothing about the file that included it. The
    context is recorded on the exception and rendered into whichever field its
    own `__str__` reads, so the type and identity are untouched.
    """

    def test_yaml_glob_member_error_names_file(self, tmp_path):
        import yaconfiglib

        (tmp_path / "10.yaml").write_text("a: 1\n", encoding="utf-8")
        (tmp_path / "20.yaml").write_text("b: [unclosed\n", encoding="utf-8")
        with pytest.raises(Exception) as caught:
            yaconfiglib.ConfigLoader(base_dir=str(tmp_path)).load("*.yaml")
        assert "20.yaml" in str(caught.value)

    def test_yaml_reader_error_names_file(self, tmp_path):
        import yaconfiglib

        source = tmp_path / "bell.yaml"
        source.write_bytes(b"a: \x07\n")
        with pytest.raises(Exception) as caught:
            yaconfiglib.load(str(source))
        assert "bell.yaml" in str(caught.value)

    def test_include_child_yaml_error_names_child_and_include_site(self, tmp_path):
        import yaconfiglib

        (tmp_path / "parent.yaml").write_text(
            "a: 1\nchild: !include child.yaml\n", encoding="utf-8"
        )
        (tmp_path / "child.yaml").write_text("b: [unclosed\n", encoding="utf-8")
        with pytest.raises(Exception) as caught:
            yaconfiglib.load(str(tmp_path / "parent.yaml"))
        message = str(caught.value)
        assert "child.yaml" in message
        assert "parent.yaml" in message
        assert "line 2" in message

    def test_include_child_json_error_keeps_type_and_names_parent(self, tmp_path):
        import json

        import yaconfiglib

        (tmp_path / "parent.yaml").write_text(
            "secrets: !include secrets.json\n", encoding="utf-8"
        )
        (tmp_path / "secrets.json").write_text('{"a": }\n', encoding="utf-8")
        with pytest.raises(json.JSONDecodeError) as caught:
            yaconfiglib.load(str(tmp_path / "parent.yaml"))
        error = caught.value
        assert type(error) is json.JSONDecodeError
        assert error.config_source.endswith("secrets.json")
        assert error.config_frames[0].kind == "include"
        assert "parent.yaml" in str(error)

    def test_json_error_names_source(self, tmp_path):
        import json

        import yaconfiglib

        source = tmp_path / "bad.json"
        source.write_text('{"a": }\n', encoding="utf-8")
        with pytest.raises(json.JSONDecodeError) as caught:
            yaconfiglib.load(str(source))
        assert "bad.json" in str(caught.value)

    def test_toml_error_names_source(self, tmp_path, request):
        import yaconfiglib

        request.getfixturevalue("needs_toml")
        source = tmp_path / "bad.toml"
        source.write_text("a = =\n", encoding="utf-8")
        with pytest.raises(Exception) as caught:
            yaconfiglib.load(str(source))
        assert "bad.toml" in str(caught.value)

    def test_ini_parse_error_names_full_path(self, tmp_path):
        import yaconfiglib

        source = tmp_path / "app.ini"
        source.write_text("key = value\n", encoding="utf-8")  # no section header
        with pytest.raises(Exception) as caught:
            yaconfiglib.load(str(source))
        message = str(caught.value)
        # The full path, not the bare basename: two app.ini files in a glob
        # have to be distinguishable.
        assert str(source) in message or source.as_posix() in message

    def test_ini_interpolation_error_names_source(self, tmp_path):
        import yaconfiglib

        nested = tmp_path / "sub" / "a"
        nested.mkdir(parents=True)
        source = nested / "app.ini"
        source.write_text("[s]\nk = %(missing)s\n", encoding="utf-8")
        with pytest.raises(Exception) as caught:
            yaconfiglib.load(str(source))
        message = str(caught.value)
        assert str(source) in message or source.as_posix() in message

    def test_utf16_file_error_names_file_and_encoding(self, tmp_path):
        import yaconfiglib

        source = tmp_path / "wide.yaml"
        source.write_bytes("a: 1\n".encode("utf-16"))
        with pytest.raises(UnicodeDecodeError) as caught:
            yaconfiglib.load(str(source))
        message = str(caught.value)
        assert "wide.yaml" in message
        assert "utf-16" in message

    def test_missing_include_names_including_file(self, tmp_path):
        import yaconfiglib

        (tmp_path / "parent.yaml").write_text(
            "missing: !include nope/x.yaml\n", encoding="utf-8"
        )
        with pytest.raises(FileNotFoundError) as caught:
            yaconfiglib.load(str(tmp_path / "parent.yaml"))
        assert "parent.yaml" in str(caught.value)

    def test_include_mapping_without_pathname_raises_constructor_error(self, tmp_path):
        import yaml

        import yaconfiglib

        (tmp_path / "parent.yaml").write_text(
            "child: !include {encoding: utf-8}\n", encoding="utf-8"
        )
        with pytest.raises(yaml.constructor.ConstructorError) as caught:
            yaconfiglib.load(str(tmp_path / "parent.yaml"))
        assert "pathname" in str(caught.value)

    def test_include_empty_sequence_raises_constructor_error(self, tmp_path):
        import yaml

        import yaconfiglib

        (tmp_path / "parent.yaml").write_text("child: !include []\n", encoding="utf-8")
        with pytest.raises(yaml.constructor.ConstructorError):
            yaconfiglib.load(str(tmp_path / "parent.yaml"))

    def test_tag_constructor_route_validates_forms(self, tmp_path):
        import yaml

        import yaconfiglib

        # The other route into the include forms: a backend instance registered
        # by hand as a PyYAML constructor.
        class Owned(yaml.SafeLoader):
            pass

        backend = yaconfiglib.ConfigLoader().loader_factory(tmp_path / "x.yaml")
        Owned.add_constructor("!include", backend)
        with pytest.raises(yaml.constructor.ConstructorError) as caught:
            yaml.load("child: !include {encoding: utf-8}\n", Owned)
        assert "pathname" in str(caught.value)

    def test_j2_template_error_names_template(self, tmp_path, request):
        import yaconfiglib

        request.getfixturevalue("needs_jinja2")
        source = tmp_path / "tpl.yaml.j2"
        source.write_text("a: {{ 1 +\n", encoding="utf-8")
        with pytest.raises(Exception) as caught:
            yaconfiglib.load(str(source))
        assert str(getattr(caught.value, "filename", "")).endswith("tpl.yaml.j2")
        assert "tpl.yaml.j2" in str(caught.value)

    def test_j2_rendered_document_error_names_template(self, tmp_path, request):
        import yaconfiglib

        request.getfixturevalue("needs_jinja2")
        source = tmp_path / "tpl.yaml.j2"
        source.write_text("a: [x\n", encoding="utf-8")
        with pytest.raises(Exception) as caught:
            yaconfiglib.load(str(source))
        kinds = [frame.kind for frame in getattr(caught.value, "config_frames", ())]
        assert "render" in kinds
        assert "tpl.yaml.j2" in str(caught.value)

    def test_merge_error_names_source(self, tmp_path):
        import yaconfiglib

        (tmp_path / "a.yaml").write_text("a: 1\n", encoding="utf-8")
        (tmp_path / "b.yaml").write_text("b: 2\n", encoding="utf-8")

        def broken(a, b, **options):
            raise TypeError("no merging today")

        loader = yaconfiglib.ConfigLoader(base_dir=str(tmp_path))
        with pytest.raises(TypeError) as caught:
            loader.load("a.yaml", "b.yaml", merge=broken)
        message = str(caught.value)
        # A plain function has no init(), so the first document is taken as-is
        # and the strategy is first called for the second source.
        assert "b.yaml" in message
        kinds = [frame.kind for frame in getattr(caught.value, "config_frames", ())]
        assert "merge" in kinds

    def test_error_type_and_identity_preserved(self, tmp_path):
        import json

        import yaconfiglib

        seen = []

        def record(error, **context):
            seen.append(error)
            return False

        source = tmp_path / "bad.json"
        source.write_text('{"a": }\n', encoding="utf-8")
        with pytest.raises(json.JSONDecodeError) as caught:
            yaconfiglib.load(str(source), ignore_error=record)
        # The pin: a predicate receives the very object that is raised, of the
        # parser's own type.
        assert seen[-1] is caught.value
        assert type(caught.value) is json.JSONDecodeError

    def test_nested_context_rendered_once(self, tmp_path):
        import yaconfiglib

        (tmp_path / "top.yaml").write_text("mid: !include mid.yaml\n", encoding="utf-8")
        (tmp_path / "mid.yaml").write_text(
            "leaf: !include leaf.yaml\n", encoding="utf-8"
        )
        (tmp_path / "leaf.yaml").write_text("b: [unclosed\n", encoding="utf-8")
        with pytest.raises(Exception) as caught:
            yaconfiglib.load(str(tmp_path / "top.yaml"))
        message = str(caught.value)
        suffix = message[message.rindex("[") :]
        # The suffix is re-rendered from the record each time, never appended
        # twice: each include site appears once, and the innermost file — which
        # PyYAML's own marks already name — is not repeated there at all.
        assert "leaf.yaml" not in suffix
        assert suffix.count("mid.yaml") == 1
        assert suffix.count("top.yaml") == 1
        kinds = [frame.kind for frame in getattr(caught.value, "config_frames", ())]
        assert kinds.count("include") == 2
