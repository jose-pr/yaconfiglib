"""Tests that every public annotation resolves, and says what the runtime accepts.

Two separate problems live here. `typing.get_type_hints()` failed on Python 3.9 —
the project's own floor — for most of the public API, because hints were spelled
with PEP 604 unions and `typing.Self`. And several hints described something
narrower than the documented behaviour, so a type checker rejected calls from the
library's own README.

The targets are **discovered**, not listed: a hand-written list silently stops
covering new API, and the previous attempt at one was missing ten callables.
"""

import inspect
import os
import pkgutil
import sys
import typing

import pytest

import yaconfiglib

#: Modules that import an optional extra at module level. Only these may fail to
#: import during discovery, mirroring the suite's needs_* fixture convention.
_OPTIONAL_MODULES = frozenset(
    {
        "yaconfiglib.backends.yaml",
        "yaconfiglib.backends.toml",
        "yaconfiglib.backends.jinja2",
        "yaconfiglib.utils.jinja2",
    }
)


def _public_callables():
    """Every public callable the package defines, as ``{qualified name: object}``.

    A name counts when it does not start with ``_`` (a dunder method does count)
    and the object was defined in the module being walked — re-exports are
    therefore visited once, where they are written.
    """
    found = {}
    modules = [yaconfiglib]
    for info in pkgutil.walk_packages(yaconfiglib.__path__, "yaconfiglib."):
        try:
            modules.append(__import__(info.name, fromlist=["_"]))
        except ImportError:
            if info.name in _OPTIONAL_MODULES:
                continue
            raise
    for module in modules:
        for name, obj in vars(module).items():
            if name.startswith("_"):
                continue
            if inspect.isfunction(obj) and obj.__module__ == module.__name__:
                found[f"{module.__name__}.{name}"] = obj
            elif inspect.isclass(obj) and obj.__module__ == module.__name__:
                for attr, member in vars(obj).items():
                    if attr.startswith("_") and not (
                        attr.startswith("__") and attr.endswith("__")
                    ):
                        continue
                    function = member
                    if isinstance(member, (classmethod, staticmethod)):
                        function = member.__func__
                    if (
                        inspect.isfunction(function)
                        and function.__module__ == module.__name__
                    ):
                        found[f"{module.__name__}.{obj.__name__}.{attr}"] = function
    return found


_CALLABLES = _public_callables()
_IDS = sorted(_CALLABLES)


def _flatten_union(hint):
    """Every member of *hint*, flattening nested unions."""
    if typing.get_origin(hint) is typing.Union:
        members = []
        for member in typing.get_args(hint):
            members.extend(_flatten_union(member))
        return members
    return [hint]


def _hints(obj):
    return typing.get_type_hints(obj)


class TestFloorResolvableSignatures:
    @pytest.mark.parametrize("qualname", _IDS)
    def test_public_hints_resolve(self, qualname):
        # The floor is 3.9, where `X | Y` and `typing.Self` are not evaluable;
        # anything introspecting this API (docs tooling, pydantic, a checker at
        # runtime) failed on most of it.
        typing.get_type_hints(_CALLABLES[qualname])

    def test_hint_targets_cover_core_api(self):
        # A pin on the discovery rule itself: these are the callables a consumer
        # actually calls, and a rule that stops finding them is broken.
        core = [
            "yaconfiglib.loader.load",
            "yaconfiglib.loader.loads",
            "yaconfiglib.loader.dump",
            "yaconfiglib.loader.dumps",
            "yaconfiglib.loader.load_as",
            "yaconfiglib.loader.ConfigLoader.__init__",
            "yaconfiglib.loader.ConfigLoader.load",
            "yaconfiglib.loader.ConfigLoader.load_as",
            "yaconfiglib.loader.ConfigLoader.load_all",
            "yaconfiglib.loader.DotAccessibleDict.get",
            "yaconfiglib.utils.source.parse_sources",
            "yaconfiglib.utils.source.has_glob_pattern",
            "yaconfiglib.utils.jinja2.interpolate",
            "yaconfiglib.utils.jinja2.compile",
            "yaconfiglib.utils.jinja2.eval",
            "yaconfiglib.utils.jinja2.load_template",
            "yaconfiglib.utils.merge.MergeMethod.__call__",
            "yaconfiglib.utils.merge.is_array",
            "yaconfiglib.utils.merge.is_scalar",
            "yaconfiglib.utils.typing_merge.typed_merge",
            "yaconfiglib.utils.enum.IntEnum.extend",
            "yaconfiglib.backends.base.ConfigBackend.load",
            "yaconfiglib.backends.base.ConfigBackend.load_all",
            "yaconfiglib.backends.base.ConfigBackend.dumps",
            "yaconfiglib.backends.base.ConfigBackend.__subclasses__",
            "yaconfiglib.backends.base.ConfigBackend.get_class_by_name",
            "yaconfiglib.backends.base.ConfigBackend.get_class_by_path",
            "yaconfiglib.backends.base.ConfigBackend.can_load_path",
            "yaconfiglib.backends.yaml.YamlConfig.load",
            "yaconfiglib.backends.yaml.YamlConfig.dumps",
            "yaconfiglib.backends.json.JsonConfig.load",
            "yaconfiglib.backends.json.JsonConfig.dumps",
            "yaconfiglib.backends.ini.IniConfig.load",
            "yaconfiglib.backends.dotenv.DotenvBackend.load",
            "yaconfiglib.backends.env.EnvVarBackend.__init__",
            "yaconfiglib.backends.env.EnvVarBackend.load",
            "yaconfiglib.backends.command.CommandBackend.load",
            "yaconfiglib.backends.python_backend.PythonBackend.__init__",
            "yaconfiglib.backends.python_backend.PythonBackend.load",
            "yaconfiglib.errors.load_error_types",
        ]
        missing = [name for name in core if name not in _CALLABLES]
        assert not missing, f"discovery missed: {missing}"
        # A NamedTuple's generated methods are not discovered, so its own field
        # hints need checking directly.
        typing.get_type_hints(yaconfiglib.errors.ErrorFrame)

    def test_key_factory_hint_accepts_path_and_value(self):
        from yaconfiglib.loader import ConfigLoader

        hint = _hints(ConfigLoader.load)["key_factory"]
        members = _flatten_union(hint)
        assert str in members
        # get_args(Callable[[A, B], R]) is ([A, B], R): the parameters are one
        # list, so the arity check reads that list.
        parameter_lists = [
            typing.get_args(m)[0]
            for m in members
            if typing.get_origin(m) is not None and typing.get_args(m)
        ]
        # It is CALLED as key_factory(path, value); a one-argument hint made
        # every documented callable an error.
        assert any(
            isinstance(params, list) and len(params) == 2 and params[1] is typing.Any
            for params in parameter_lists
        ), parameter_lists

    def test_utils_star_exports_no_future_or_typevar(self):
        import yaconfiglib.utils

        # `from .enum import *` used to leak these, because enum.py has no
        # __all__.
        assert "annotations" not in dir(yaconfiglib.utils)
        assert "T" not in dir(yaconfiglib.utils)

    def test_entry_points_return_any(self):
        from yaconfiglib.loader import ConfigLoader, DotAccessibleDict, load, loads

        # A parsed document can be a mapping, a list or a scalar, so `object`
        # made every documented attribute access an error.
        for obj in (load, loads, ConfigLoader.load, DotAccessibleDict.get):
            assert _hints(obj)["return"] is typing.Any, obj
        assert typing.get_args(_hints(ConfigLoader.load_all)["return"]) == (typing.Any,)
        assert _hints(DotAccessibleDict.__getattr__)["return"] is typing.Any

    def test_none_defaults_are_optional(self):
        violations = []
        for qualname in _IDS:
            function = _CALLABLES[qualname]
            try:
                signature = inspect.signature(function)
            except (ValueError, TypeError):
                continue
            namespace = vars(sys.modules[function.__module__])
            for name, parameter in signature.parameters.items():
                if parameter.default is not None:
                    continue
                annotation = parameter.annotation
                if annotation is inspect.Parameter.empty:
                    violations.append(f"{qualname}({name}): no annotation")
                    continue
                for _ in range(3):
                    if not isinstance(annotation, str):
                        break
                    try:
                        annotation = eval(annotation, namespace)  # noqa: S307
                    except Exception as error:  # noqa: BLE001 - reported below
                        violations.append(f"{qualname}({name}): {error}")
                        break
                if isinstance(annotation, str):
                    continue
                if annotation in (typing.Any, object):
                    continue
                if type(None) not in _flatten_union(annotation):
                    violations.append(f"{qualname}({name}): {annotation!r}")
        # PEP 484 forbids implicit Optional, and both mypy and pyright enforce
        # it: a `= None` default with a non-Optional hint rejects `None`.
        assert not violations, "\n".join(violations)

    def test_config_loader_init_hints_accept_documented_forms(self):
        from yaconfiglib.loader import ConfigLoader

        hints = _hints(ConfigLoader.__init__)
        assert str in _flatten_union(hints["key_factory"])
        assert any(
            typing.get_origin(m) is os.PathLike or m is os.PathLike
            for m in _flatten_union(hints["base_dir"])
        ), hints["base_dir"]

        factory = [
            m for m in _flatten_union(hints["loader_factory"]) if m is not type(None)
        ]
        # A (path) -> backend callable, never type[Backend]: the class form
        # steered users into "YamlConfig() takes no arguments".
        assert factory and all(
            typing.get_origin(m) is not type for m in factory
        ), factory
        assert any(
            isinstance(typing.get_args(m)[0], list)
            for m in factory
            if typing.get_args(m)
        ), factory

        path_factory = [
            m for m in _flatten_union(hints["path_factory"]) if m is not type(None)
        ]
        assert path_factory, hints["path_factory"]
        returns = [typing.get_args(m)[-1] for m in path_factory if typing.get_args(m)]
        assert returns and all(
            typing.get_origin(r) is os.PathLike or r is os.PathLike for r in returns
        ), returns

    def test_load_and_load_all_hints_accept_documented_sources(self):
        from yaconfiglib.backends.base import ConfigBackend
        from yaconfiglib.loader import ConfigLoader

        load_hints = _hints(ConfigLoader.load)
        assert _hints(ConfigLoader.load_all)["pathname"] == load_hints["pathname"]
        members = _flatten_union(load_hints["loader"])
        assert str in members
        assert ConfigBackend in members
        assert any(typing.get_origin(m) is not None for m in members), members

    def test_public_variadics_are_annotated(self):
        missing = []
        for qualname in _IDS:
            function = _CALLABLES[qualname]
            try:
                signature = inspect.signature(function)
            except (ValueError, TypeError):
                continue
            for name, parameter in signature.parameters.items():
                if (
                    parameter.kind
                    in (
                        inspect.Parameter.VAR_POSITIONAL,
                        inspect.Parameter.VAR_KEYWORD,
                    )
                    and parameter.annotation is inspect.Parameter.empty
                ):
                    missing.append(f"{qualname}(*{name})")
        assert not missing, "\n".join(missing)

        from yaconfiglib.loader import ConfigLoader

        for obj in (ConfigLoader.__init__, ConfigLoader.load):
            members = _flatten_union(_hints(obj)["merge_options"])
            assert any(
                typing.get_args(m) == (str, typing.Any) for m in members
            ), members

    def test_backend_path_hints_accept_os_pathlike(self):
        import yaconfiglib.backends as backends

        names = [
            ("yaml", "YamlConfig"),
            ("json", "JsonConfig"),
            ("ini", "IniConfig"),
            ("toml", "TomlConfig"),
            ("dotenv", "DotenvBackend"),
            ("jinja2", "Jinja2ConfigLoader"),
            ("command", "CommandBackend"),
        ]
        checked = 0
        for _module, class_name in names:
            backend = getattr(backends, class_name, None)
            if backend is None:
                continue  # optional extra not installed
            members = _flatten_union(_hints(backend.load)["path"])
            assert any(
                typing.get_origin(m) is os.PathLike or m is os.PathLike for m in members
            ), (class_name, members)
            checked += 1
        assert checked >= 5

    def test_ignore_error_protocol_names_documented_keywords(self):
        from yaconfiglib.loader import _IgnoreError

        signature = inspect.signature(_IgnoreError.__call__)
        kinds = {
            name: parameter.kind for name, parameter in signature.parameters.items()
        }
        for name in ("phase", "path", "loader"):
            assert kinds.get(name) is inspect.Parameter.KEYWORD_ONLY, name
        assert list(kinds.values()).count(inspect.Parameter.VAR_KEYWORD) == 1
        assert inspect.Parameter.VAR_POSITIONAL not in kinds.values()
        hints = _hints(_IgnoreError.__call__)
        assert hints["phase"] is str
        assert hints["return"] is bool
