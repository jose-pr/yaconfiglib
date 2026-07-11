"""
Jinja2 templating utilities for configuration interpolation.

Provides helpers to compile, evaluate, and interpolate Jinja2 templates
within configuration data structures (strings, mappings, sequences).
"""

from __future__ import annotations

import logging
import typing as _ty

from jinja2 import Environment, Template

logger = logging.getLogger(__name__)

DEFAULT_ENV = Environment(extensions=["jinja2.ext.do"])


def load_template(
    source: str,
    name: str | None = None,
    filename: str | None = None,
    environment: Environment | None = None,
    globals: _ty.MutableMapping | None = None,
) -> Template:
    """Compile *source* into a :class:`~jinja2.Template`."""
    env = environment or DEFAULT_ENV
    code = env.compile(source, name, filename)
    return Template.from_code(env, code, env.make_globals(globals))


_COMPILE_CACHE = {}
_EVAL_CACHE = {}


def compile(
    code: str,
    environment: Environment | None = None,
    globals: _ty.MutableMapping | None = None,
) -> _ty.Callable[..., str]:
    """Return a render callable for *code* (a Jinja2 template string)."""
    env = environment or DEFAULT_ENV
    cache_key = (code, id(env))
    if cache_key in _COMPILE_CACHE:
        return _COMPILE_CACHE[cache_key]
    
    render = load_template(code, environment=env, globals=globals).render
    if len(_COMPILE_CACHE) >= 1024:
        _COMPILE_CACHE.clear()
    _COMPILE_CACHE[cache_key] = render
    return render


def eval(
    code: str,
    environment: Environment | None = None,
    globals: _ty.MutableMapping | None = None,
) -> _ty.Callable[..., object]:
    """Return a callable that evaluates *code* as a Jinja2 expression.

    The expression result is captured via a ``{% do %}`` statement and
    returned from the callable, preserving non-string Python types.
    """
    env = environment or DEFAULT_ENV
    cache_key = (code, id(env))
    if cache_key in _EVAL_CACHE:
        return _EVAL_CACHE[cache_key]

    template = load_template(
        "{% do _meta.__setitem__('result', " + code + ") %}",
        environment=env,
        globals=globals,
    )

    def _eval(**kwargs) -> object:
        _meta: dict = {}
        template.render(_meta=_meta, **kwargs)
        res = _meta["result"]
        from jinja2 import Undefined
        if isinstance(res, Undefined):
            str(res)  # Forces UndefinedError if strict
            return None
        return res

    if len(_EVAL_CACHE) >= 1024:
        _EVAL_CACHE.clear()
    _EVAL_CACHE[cache_key] = _eval
    return _eval


def interpolate(data: object, globals: dict | None = None, environment: Environment | None = None) -> object:
    """Recursively interpolate Jinja2 templates within *data*.

    * **Strings**: rendered as Jinja2 templates.  A bare ``{{ expr }}``
      (no surrounding text) is evaluated as a Python expression so that
      the return type is preserved (e.g. an integer stays an integer).
    * **Mappings**: keys and values are interpolated recursively.
    * **Sequences**: each element is interpolated recursively.

    Returns the interpolated object (may differ in type from *data* for
    pure-expression strings).
    """
    globals = {} if globals is None else globals

    if isinstance(data, str):
        stripped = data.strip()
        # Pure Jinja2 expression: {{ expr }} — evaluate to preserve type.
        if stripped.startswith("{{") and stripped.endswith("}}"):
            inner = stripped[2:-2].strip()
            if "{{" not in inner:
                result = eval(inner, environment=environment)(**globals)
                logger.debug("interpolated expression %r -> %r", data, result)
                return result
        result = compile(data, environment=environment)(**globals)
        if result != data:
            logger.debug("interpolated template %r -> %r", data, result)
        return result

    if isinstance(data, _ty.Mapping):
        if not isinstance(data, _ty.MutableMapping):
            data = dict(data)
        for key in list(data.keys()):
            value = data.pop(key)
            new_key = interpolate(key, globals, environment=environment)
            data[new_key] = interpolate(value, globals, environment=environment)
        return data

    if isinstance(data, _ty.Iterable) and not isinstance(data, (str, bytes)):
        if not isinstance(data, _ty.MutableSequence):
            data = list(data)
        for idx, value in enumerate(data):
            data[idx] = interpolate(value, globals, environment=environment)
        return data

    return data

    return data
