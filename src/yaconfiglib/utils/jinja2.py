"""
Jinja2 templating utilities for configuration interpolation.

Provides helpers to compile, evaluate, and interpolate Jinja2 templates
within configuration data structures (strings, mappings, sequences).
"""

from __future__ import annotations

import logging
import typing as _ty
import weakref as _weakref
from collections import OrderedDict as _OrderedDict

from jinja2 import Environment, Template

logger = logging.getLogger(__name__)

DEFAULT_ENV = Environment(extensions=["jinja2.ext.do"])

#: A string with none of Jinja's delimiters cannot be a template.
_JINJA_MARKERS = ("{{", "{%", "{#")


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


_CACHE_MAX = 1024
# LRU caches keyed on (code, id(env)); the value carries a weakref to the env
# so a recycled id() (a new env reusing a GC'd env's address) can't return a
# render bound to the dead env. OrderedDict gives O(1) LRU without clearing the
# whole cache at capacity (the old dict did, causing recompile stampedes).
_COMPILE_CACHE: "_OrderedDict[tuple, tuple]" = _OrderedDict()
_EVAL_CACHE: "_OrderedDict[tuple, tuple]" = _OrderedDict()


def _cache_get(cache: _OrderedDict, code: str, env: Environment):
    key = (code, id(env))
    hit = cache.get(key)
    if hit is None:
        return None
    env_ref, value = hit
    if env_ref() is env:
        cache.move_to_end(key)
        return value
    del cache[key]  # id() was recycled onto a different env — recompile
    return None


def _cache_put(cache: _OrderedDict, code: str, env: Environment, value) -> None:
    try:
        env_ref = _weakref.ref(env)
    except TypeError:
        env_ref = lambda: env  # non-weakrefable env: keep it alive via closure
    cache[(code, id(env))] = (env_ref, value)
    cache.move_to_end((code, id(env)))
    while len(cache) > _CACHE_MAX:
        cache.popitem(last=False)


def compile(
    code: str,
    environment: Environment | None = None,
    globals: _ty.MutableMapping | None = None,
) -> _ty.Callable[..., str]:
    """Return a render callable for *code* (a Jinja2 template string)."""
    env = environment or DEFAULT_ENV
    cached = _cache_get(_COMPILE_CACHE, code, env)
    if cached is not None:
        return cached
    render = load_template(code, environment=env, globals=globals).render
    _cache_put(_COMPILE_CACHE, code, env, render)
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
    cached = _cache_get(_EVAL_CACHE, code, env)
    if cached is not None:
        return cached

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

    _cache_put(_EVAL_CACHE, code, env, _eval)
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
        # Fast path: a string with no Jinja delimiter renders to itself, so
        # skip the cache lookup + Template.render entirely. Most config strings
        # are plain text — this avoids paying Jinja for every one of them.
        if not any(marker in data for marker in _JINJA_MARKERS):
            return data
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
