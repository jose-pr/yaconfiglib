"""
Tests for Jinja2 interpolation utilities (utils/jinja2.py).
"""
import pytest

from yaconfiglib.utils import jinja2 as j2


# ---------------------------------------------------------------------------
# compile / eval helpers
# ---------------------------------------------------------------------------

class TestCompile:
    def test_simple_render(self):
        fn = j2.compile("Hello {{ name }}!")
        assert fn(name="World") == "Hello World!"

    def test_no_variables(self):
        fn = j2.compile("static string")
        assert fn() == "static string"


class TestEval:
    def test_expression_returns_int(self):
        fn = j2.eval("1 + 2")
        assert fn() == 3

    def test_expression_with_variable(self):
        fn = j2.eval("x * 2")
        assert fn(x=5) == 10

    def test_expression_returns_dict(self):
        fn = j2.eval("dict(a=1, b=2)")
        assert fn() == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# interpolate
# ---------------------------------------------------------------------------

class TestInterpolate:
    def test_plain_string_no_change(self):
        assert j2.interpolate("hello") == "hello"

    def test_template_string(self):
        result = j2.interpolate("Hello {{ name }}!", {"name": "World"})
        assert result == "Hello World!"

    def test_pure_expression_preserves_type_int(self):
        # {{ 10 }} with no surrounding text → int not str
        result = j2.interpolate("{{ 10 }}", {})
        assert result == 10
        assert isinstance(result, int)

    def test_pure_expression_preserves_type_dict(self):
        result = j2.interpolate("{{ dict(d=1) }}", {})
        assert result == {"d": 1}

    def test_interpolate_dict_keys_and_values(self):
        data = {"{{ 'key' }}": "{{ 1 + 1 }}"}
        result = j2.interpolate(data, {})
        assert result == {"key": 2}

    def test_interpolate_list(self):
        data = ["{{ 1 }}", "{{ 2 }}", "static"]
        result = j2.interpolate(data, {})
        assert result == [1, 2, "static"]

    def test_interpolate_nested(self):
        data = {"outer": {"inner": "{{ x }}"}}
        result = j2.interpolate(data, {"x": 42})
        assert result["outer"]["inner"] == 42

    def test_interpolate_non_string_passthrough(self):
        assert j2.interpolate(123, {}) == 123
        assert j2.interpolate(3.14, {}) == 3.14
        assert j2.interpolate(None, {}) is None

    def test_interpolate_with_globals(self):
        result = j2.interpolate("{{ greeting }}, {{ name }}!", {"greeting": "Hi", "name": "Alice"})
        assert result == "Hi, Alice!"


# ---------------------------------------------------------------------------
# load_template
# ---------------------------------------------------------------------------

class TestLoadTemplate:
    def test_custom_environment(self):
        from jinja2 import Environment
        env = Environment()
        t = j2.load_template("{{ x }}", environment=env)
        assert t.render(x="ok") == "ok"
