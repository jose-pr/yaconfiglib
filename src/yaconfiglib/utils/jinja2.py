"""
Jinja2 templating utilities for configuration interpolation.

Provides helpers to compile, evaluate, and interpolate Jinja2 templates
within configuration data structures (strings, mappings, sequences).
"""

from __future__ import annotations

import logging
import threading as _threading
import typing as _ty
import weakref as _weakref
from collections import OrderedDict as _OrderedDict

from jinja2 import Environment, Template

from .. import errors as _errors

logger = logging.getLogger(__name__)

DEFAULT_ENV = Environment(extensions=["jinja2.ext.do"])

_ENVIRONMENTS: dict = {}


def get_environment(strict: bool, sandbox: bool = False) -> Environment:
    """Return the shared interpolation environment for ``(strict, sandbox)``.

    One instance per combination, created on first use and kept for the life of
    the process, so compiled-template caches keyed on the environment stay warm.
    ``strict`` makes undefined variables raise; ``sandbox`` uses Jinja2's
    ``SandboxedEnvironment``, which blocks attribute traversal into Python
    internals (SSTI) when the template text is untrusted.
    """
    key = (strict, sandbox)
    if key not in _ENVIRONMENTS:
        from jinja2 import StrictUndefined

        env_kwargs = {}
        if strict:
            env_kwargs["undefined"] = StrictUndefined
        if sandbox:
            from jinja2.sandbox import SandboxedEnvironment as _Env
        else:
            _Env = Environment
        _ENVIRONMENTS[key] = _Env(extensions=["jinja2.ext.do"], **env_kwargs)
    return _ENVIRONMENTS[key]


#: A string with none of Jinja's delimiters cannot be a template.
_JINJA_MARKERS = ("{{", "{%", "{#")


def load_template(
    source: str,
    name: _ty.Optional[str] = None,
    filename: _ty.Optional[str] = None,
    environment: _ty.Optional[Environment] = None,
    globals: "_ty.Optional[_ty.MutableMapping[str, _ty.Any]]" = None,
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
_REFERENCES_CACHE: "_OrderedDict[tuple, tuple]" = _OrderedDict()


# The caches are module-level state shared by every thread. Read-modify-write
# sequences (get + move_to_end, put + evict) are not atomic — least of all on a
# free-threaded build — so both run under one lock. It is never held while a
# template compiles.
_CACHE_LOCK = _threading.Lock()


def _cache_get(cache: _OrderedDict, code: str, env: Environment):
    key = (code, id(env))
    with _CACHE_LOCK:
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
    with _CACHE_LOCK:
        cache[(code, id(env))] = (env_ref, value)
        cache.move_to_end((code, id(env)))
        while len(cache) > _CACHE_MAX:
            cache.popitem(last=False)


def compile(
    code: str,
    environment: _ty.Optional[Environment] = None,
    globals: "_ty.Optional[_ty.MutableMapping[str, _ty.Any]]" = None,
) -> _ty.Callable[..., str]:
    """Return a render callable for *code* (a Jinja2 template string).

    *globals* applies to this call only: the cached template is compiled without
    it, and the values are merged under the render's own keyword arguments, which
    still win. Caching the baked-in globals returned the first caller's values to
    every later one.
    """
    env = environment or DEFAULT_ENV
    render = _cache_get(_COMPILE_CACHE, code, env)
    if render is None:
        render = load_template(code, environment=env).render
        _cache_put(_COMPILE_CACHE, code, env, render)
    if not globals:
        return render
    return lambda **kwargs: render(**{**globals, **kwargs})


def references(
    code: str, environment: _ty.Optional[Environment] = None
) -> "frozenset[str]":
    """Return the names *code* reads from its context, parsing it at most once.

    Cached like :func:`compile`/:func:`eval`, because a caller that orders values
    by their references asks for the same template text repeatedly. A template
    that does not parse yields no names: rendering it reports the syntax error
    with a better message.
    """
    env = environment or DEFAULT_ENV
    cached = _cache_get(_REFERENCES_CACHE, code, env)
    if cached is not None:
        return cached
    from jinja2 import TemplateSyntaxError
    from jinja2 import meta as _meta

    try:
        names = frozenset(_meta.find_undeclared_variables(env.parse(code)))
    except TemplateSyntaxError:
        names = frozenset()
    _cache_put(_REFERENCES_CACHE, code, env, names)
    return names


def eval(
    code: str,
    environment: _ty.Optional[Environment] = None,
    globals: "_ty.Optional[_ty.MutableMapping[str, _ty.Any]]" = None,
) -> _ty.Callable[..., _ty.Any]:
    """Return a callable that evaluates *code* as a Jinja2 expression.

    The expression result is captured via a ``{% do %}`` statement and
    returned from the callable, preserving non-string Python types.
    *globals* applies to this call only (see :func:`compile`).
    """
    env = environment or DEFAULT_ENV
    cached = _cache_get(_EVAL_CACHE, code, env)
    if cached is not None:
        if not globals:
            return cached
        return lambda **kwargs: cached(**{**globals, **kwargs})

    # The result is captured by CALLING a plain function bound as a render
    # variable — never by reaching for an attribute. jinja2's
    # SandboxedEnvironment rejects every attribute whose name starts with "_",
    # so the previous `_meta.__setitem__('result', ...)` capture raised
    # SecurityError for every bare `{{ expr }}` under sandbox=True (the capture
    # mechanism tripped the sandbox, not the user's expression). Names are not
    # sandboxed, and a bound builtin method is safely callable, so `_set` works
    # in both environments. Do not reintroduce attribute access here.
    # Compiled without `globals`: they are merged per call below, so a cached
    # evaluator never serves the first caller's values to a later one.
    template = load_template(
        "{% do _set('result', " + code + ") %}",
        environment=env,
    )

    def _eval(**kwargs) -> object:
        _meta: dict = {}
        template.render(_set=_meta.__setitem__, **kwargs)
        res = _meta["result"]
        from jinja2 import Undefined

        if isinstance(res, Undefined):
            str(res)  # Forces UndefinedError if strict
            return None
        return res

    _cache_put(_EVAL_CACHE, code, env, _eval)
    if not globals:
        return _eval
    return lambda **kwargs: _eval(**{**globals, **kwargs})


def _dotted(keypath: tuple) -> str:
    """A key path as ``db.hosts[0].name``, or ``<document>`` when empty."""
    if not keypath:
        return "<document>"
    return _errors._render_key(keypath)


def _attribute_render_error(error: BaseException, keypath: tuple) -> None:
    """Record *keypath* on *error*, unless a deeper one is already there.

    The innermost path wins: a container's own failure is reported at the value
    that actually failed, not at the ancestor that was walking it.
    """
    if getattr(error, "config_key", None) is None and keypath:
        _errors._add_error_context(error, key=keypath)


def interpolate(
    data: _ty.Any,
    globals: "_ty.Optional[_ty.Dict[str, _ty.Any]]" = None,
    environment: _ty.Optional[Environment] = None,
) -> _ty.Any:
    """Recursively interpolate Jinja2 templates within *data*, in one pass.

    Every string is rendered exactly once, against *globals* as it stands;
    a rendered result is never rendered again, so an escaped literal such as
    ``{{ '{{ x }}' }}`` survives. Resolving references between values is the
    caller's job (:class:`~yaconfiglib.loader.ConfigLoader` does it per
    top-level key). Mutable mappings and sequences are rewritten **in place**
    and returned; immutable ones are copied.

    * **Strings**: rendered as Jinja2 templates.  A bare ``{{ expr }}``
      (nothing else but an optional trailing newline) is evaluated as a Python
      expression so that the return type is preserved (e.g. an integer stays an
      integer).
    * **Mappings**: keys and values are interpolated recursively.
    * **Sequences**: each element is interpolated recursively.

    Returns the interpolated object (may differ in type from *data* for
    pure-expression strings).

    A container reached more than once (YAML anchors/aliases share one object)
    is walked once and every reference gets the same result, so the walk is
    linear in the number of distinct nodes and self-referential data terminates.

    **A raised error never removes an entry.** Each container is rendered into
    a staging list and written back only once every member succeeded, so a
    failure leaves that container exactly as it was; containers already
    finished keep their rendered values. The error carries `config_key`, the
    path to the value that failed (``("database", "password")``), which is also
    rendered into its message.
    """
    return _interpolate(data, {} if globals is None else globals, environment, {})


def _interpolate(
    data: _ty.Any,
    globals: "_ty.Dict[str, _ty.Any]",
    environment: _ty.Optional[Environment],
    memo: "_ty.Dict[int, _ty.Any]",
    *,
    keypath: tuple = (),
    on_error: "_ty.Optional[_ty.Callable[[BaseException, tuple], bool]]" = None,
) -> object:
    """Render *data*, recording where a failure happened.

    *keypath* is the path of *data* within the document, used to attribute an
    error and to say which value a DEBUG line is about. It is built eagerly,
    one tuple per entry: passing the parent path and this value's own segment
    separately measured 2-3% faster on the interpolation benchmark, and was
    rejected because forgetting to pass the segment reports the **parent's**
    path — a wrong key in an error message is the defect this exists to fix.

    *on_error* is called as ``on_error(error, keypath)`` for a value that fails
    to render: returning True keeps that value's original text and rendering
    continues, anything else re-raises. Without it every failure propagates.
    """
    # memo maps id(original container) -> (original, result). Holding the
    # original keeps it alive, so its id() cannot be reused within one pass.
    if isinstance(data, str):
        # Fast path: a string with no Jinja delimiter renders to itself, so
        # skip the cache lookup + Template.render entirely. Most config strings
        # are plain text — this avoids paying Jinja for every one of them.
        if not any(marker in data for marker in _JINJA_MARKERS):
            return data
        # Only a trailing newline may surround a bare expression: a YAML `|`/`>`
        # block adds one, while spaces inside a quoted scalar are deliberate text.
        stripped = data.rstrip("\r\n")
        try:
            # Pure Jinja2 expression: {{ expr }} — evaluate to preserve type.
            if stripped.startswith("{{") and stripped.endswith("}}"):
                inner = stripped[2:-2].strip()
                if "{{" not in inner:
                    result = eval(inner, environment=environment)(**globals)
                    # The TEMPLATE, never the result: a template holds
                    # `{{ env.DB_PASSWORD }}`, the result holds the password.
                    logger.debug(
                        "interpolated %s from expression %r", _dotted(keypath), data
                    )
                    return result
            result = compile(data, environment=environment)(**globals)
            if result != data:
                logger.debug("interpolated %s from template %r", _dotted(keypath), data)
            return result
        except Exception as error:  # noqa: BLE001 - feeds the ignore_error predicate
            # Deliberately every type: an expression can raise any Python
            # exception (`{{ 1/0 }}` raises ZeroDivisionError, not a Jinja2
            # error), and the predicate contract covers all of them.
            _attribute_render_error(error, keypath)
            if on_error is not None and on_error(error, keypath):
                return data
            raise

    if isinstance(data, _ty.Mapping):
        seen = memo.get(id(data))
        if seen is not None:
            return seen[1]
        original = data
        if not isinstance(data, _ty.MutableMapping):
            data = dict(data)
        memo[id(original)] = (original, data)
        # Staged: render every pair before touching the container. Popping each
        # key and re-inserting it lost the key outright when the render raised,
        # and took its whole section down with it at every depth.
        staged = []
        for key, value in list(data.items()):
            # A templated key is attributed under the parent path plus the raw
            # key, which is the only name it has before it renders.
            new_key = _interpolate(
                key,
                globals,
                environment,
                memo,
                keypath=keypath + (key,),
                on_error=on_error,
            )
            new_value = _interpolate(
                value,
                globals,
                environment,
                memo,
                keypath=keypath + (key,),
                on_error=on_error,
            )
            staged.append((new_key, new_value))
        data.clear()
        data.update(staged)
        return data

    if isinstance(data, _ty.Iterable) and not isinstance(data, (str, bytes)):
        seen = memo.get(id(data))
        if seen is not None:
            return seen[1]
        original = data
        if not isinstance(data, _ty.MutableSequence):
            data = list(data)
        memo[id(original)] = (original, data)
        staged = [
            _interpolate(
                value,
                globals,
                environment,
                memo,
                keypath=keypath + (idx,),
                on_error=on_error,
            )
            for idx, value in enumerate(data)
        ]
        data[:] = staged
        return data

    return data
