"""
Tests for Jinja2 interpolation utilities (utils/jinja2.py).
"""

import pytest

pytest.importorskip("jinja2")

from yaconfiglib.utils import jinja2 as j2  # noqa: E402 - needs the skip above


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

    def test_expression_works_under_sandboxed_environment(self):
        # Regression: eval() captured its result via `_meta.__setitem__`, an
        # underscore ATTRIBUTE access, which jinja2's SandboxedEnvironment
        # refuses — so every eval() raised SecurityError under a sandbox. The
        # capture is now a plain callable bound as a render NAME (`_set`),
        # which the sandbox permits. Type preservation must survive too.
        from jinja2.sandbox import SandboxedEnvironment

        env = SandboxedEnvironment(extensions=["jinja2.ext.do"])
        assert j2.eval("1 + 2", environment=env)() == 3
        assert j2.eval("x * 2", environment=env)(x=5) == 10
        assert isinstance(j2.eval("x * 2", environment=env)(x=5), int)

    def test_sandboxed_environment_still_blocks_ssti(self):
        # The fix must not weaken the sandbox: attribute traversal into Python
        # internals still has to raise, so eval() is not an SSTI escape hatch.
        from jinja2.exceptions import SecurityError
        from jinja2.sandbox import SandboxedEnvironment

        env = SandboxedEnvironment(extensions=["jinja2.ext.do"])
        with pytest.raises(SecurityError):
            j2.eval("''.__class__.__mro__", environment=env)()


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
        result = j2.interpolate(
            "{{ greeting }}, {{ name }}!", {"greeting": "Hi", "name": "Alice"}
        )
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


class TestLoaderInterpolationFeatures:
    def test_jinja_env_auto_injection(self, monkeypatch):
        from yaconfiglib import ConfigLoader
        from yaconfiglib.backends.python_backend import PythonBackend

        monkeypatch.setenv("MY_APP_VAR", "production")
        loader = ConfigLoader(interpolate=True, inject_env=True)
        result = loader.load(loader=PythonBackend({"mode": "{{ env.MY_APP_VAR }}"}))
        assert result == {"mode": "production"}

    def test_strict_interpolation_raises(self):
        from yaconfiglib import ConfigLoader
        from yaconfiglib.backends.python_backend import PythonBackend

        import jinja2.exceptions

        loader = ConfigLoader(interpolate=True, strict=True)
        with pytest.raises(jinja2.exceptions.UndefinedError):
            loader.load(loader=PythonBackend({"value": "{{ missing_var }}"}))


class TestInterpolatePerf:
    def test_plain_string_fast_path_returns_identity(self):
        from yaconfiglib.utils.jinja2 import interpolate

        s = "just plain text, no templating here"
        assert interpolate(s) is s  # fast path returns the same object

    def test_string_with_delimiter_still_rendered(self):
        from yaconfiglib.utils.jinja2 import interpolate

        assert interpolate("hi {{ name }}", {"name": "bob"}) == "hi bob"

    def test_compile_cache_lru_keeps_hot_entry(self):
        from yaconfiglib.utils import jinja2 as J

        J._COMPILE_CACHE.clear()
        hot = "{{ a }}-hot"
        J.compile(hot)
        # Fill past capacity with unique templates; the hot entry must survive
        # because each render of it moves it to the MRU end.
        for i in range(J._CACHE_MAX + 50):
            J.compile(f"{{{{ v{i} }}}}")
            if i % 10 == 0:
                J.compile(hot)  # keep it hot
        assert (hot, id(J.DEFAULT_ENV)) in J._COMPILE_CACHE
        assert len(J._COMPILE_CACHE) <= J._CACHE_MAX

    def test_stale_env_id_recompiles(self):
        import gc

        from jinja2 import Environment

        from yaconfiglib.utils import jinja2 as J

        J._COMPILE_CACHE.clear()
        env = Environment()
        J.compile("{{ x }}", environment=env)
        assert len(J._COMPILE_CACHE) == 1
        del env
        gc.collect()
        # A fresh env that might reuse the id must not get the dead env's render.
        env2 = Environment()
        r = J.compile("{{ x }}", environment=env2)
        assert r(x=5) == "5"


class TestBareExpressionWhitespace:
    def test_whitespace_around_expression_renders_string(self):
        from yaconfiglib.utils.jinja2 import interpolate

        # Spaces inside a quoted scalar are deliberate text, so the value is a
        # rendered string rather than the expression's own type.
        assert interpolate("  {{ 5 }}") == "  5"

    def test_trailing_newline_expression_keeps_type(self):
        from yaconfiglib.utils.jinja2 import interpolate

        # A YAML `|`/`>` block adds a trailing newline; the value stays an int.
        assert interpolate("{{ 1 + 1 }}\n") == 2


class TestTemplateCacheGlobals:
    def test_compile_globals_are_per_call(self):
        from yaconfiglib.utils.jinja2 import compile

        first = compile("{{ s }}", globals={"s": "FIRST"})
        second = compile("{{ s }}", globals={"s": "SECOND"})
        assert first() == "FIRST"
        assert second() == "SECOND"

    def test_eval_globals_are_per_call(self):
        from yaconfiglib.utils.jinja2 import eval as jinja_eval

        first = jinja_eval("s", globals={"s": "FIRST"})
        second = jinja_eval("s", globals={"s": "SECOND"})
        assert first() == "FIRST"
        assert second() == "SECOND"

    def test_render_kwargs_override_compile_globals(self):
        from yaconfiglib.utils.jinja2 import compile

        render = compile("{{ s }}", globals={"s": "from-globals"})
        assert render(s="from-kwargs") == "from-kwargs"

    def test_cache_get_survives_concurrent_eviction(self, monkeypatch):
        """A lookup must not lose its entry to another thread's eviction."""
        import threading
        from collections import OrderedDict

        from jinja2 import Environment

        from yaconfiglib.utils import jinja2 as J

        class _SignallingCache(OrderedDict):
            armed = False

            def move_to_end(self, *args, **kwargs):
                if self.armed:
                    self.armed = False
                    other_started.set()
                    evicted.wait(0.5)
                return super().move_to_end(*args, **kwargs)

        other_started = threading.Event()
        evicted = threading.Event()
        env = Environment()
        cache = _SignallingCache()
        monkeypatch.setattr(J, "_CACHE_MAX", 1)
        J._cache_put(cache, "a", env, "render-a")

        def evict():
            other_started.wait(0.5)
            J._cache_put(cache, "b", env, "render-b")
            evicted.set()

        worker = threading.Thread(target=evict)
        worker.start()
        cache.armed = True
        try:
            # Without the lock this raises KeyError: the other thread evicted "a"
            # between this call's get and its move_to_end.
            J._cache_get(cache, "a", env)
        finally:
            evicted.set()
            worker.join(timeout=5)


@pytest.mark.usefixtures("needs_jinja2")
class TestInterpolationFailures:
    """A failed render loses nothing and says where it happened.

    The walk used to `pop` each key and re-insert it after rendering, so a
    raise dropped that key — and, one frame up, its whole section. It also
    logged the rendered result, which is the secret an `{{ env.X }}` template
    exists to fetch.
    """

    def test_raised_error_keeps_every_entry(self):
        from yaconfiglib.utils.jinja2 import interpolate

        document = {
            "database": {
                "host": "db.internal",
                "password": "p{% raw",
                "port": 5432,
            }
        }
        with pytest.raises(Exception):
            interpolate(document)
        # Every entry of the failing container survives, in its original form.
        assert set(document["database"]) == {"host", "password", "port"}
        assert document["database"]["password"] == "p{% raw"

    def test_raised_error_names_key_path(self):
        from yaconfiglib.utils.jinja2 import interpolate

        document = {"database": {"password": "p{% raw"}}
        with pytest.raises(Exception) as caught:
            interpolate(document)
        assert caught.value.config_key == ("database", "password")
        assert "database.password" in str(caught.value)

    def test_syntax_error_names_key_and_keeps_type(self):
        import jinja2 as _jinja2

        from yaconfiglib.utils.jinja2 import interpolate

        with pytest.raises(_jinja2.TemplateSyntaxError) as caught:
            interpolate({"other": "{{ x"})
        assert "other" in str(caught.value)

    def test_list_index_in_key_path(self):
        from yaconfiglib.utils.jinja2 import interpolate

        with pytest.raises(Exception) as caught:
            interpolate({"servers": [{"host": "{{ x"}]})
        assert caught.value.config_key == ("servers", 0, "host")
        assert "servers[0].host" in str(caught.value)

    def test_python_error_in_expression_names_key(self):
        from yaconfiglib.utils.jinja2 import interpolate

        # An expression raises whatever Python raises, not a Jinja2 type.
        with pytest.raises(ZeroDivisionError) as caught:
            interpolate({"ratio": "{{ 1/0 }}"})
        assert "ratio" in str(caught.value)

    def test_debug_log_omits_rendered_values(self, caplog):
        import logging

        from yaconfiglib.utils.jinja2 import interpolate

        caplog.set_level(logging.DEBUG, logger="yaconfiglib.utils.jinja2")
        result = interpolate({"password": "{{ secret }}"}, {"secret": "pa55-secret"})
        assert result == {"password": "pa55-secret"}
        for record in caplog.records:
            assert "pa55-secret" not in record.getMessage()

    def test_debug_log_names_template(self, caplog):
        import logging

        from yaconfiglib.utils.jinja2 import interpolate

        caplog.set_level(logging.DEBUG, logger="yaconfiglib.utils.jinja2")
        interpolate({"password": "{{ secret }}"}, {"secret": "pa55-secret"})
        # The template is what makes a record useful, and it holds no secret.
        assert any("{{ secret }}" in r.getMessage() for r in caplog.records)
