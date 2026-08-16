"""
Tests for MergeMethod (simple, substitute, deep) and typed_merge.
"""

import typing
from argparse import Namespace
from dataclasses import dataclass, field

import pytest

from yaconfiglib.utils.merge import (
    MergeMethod,
    OpaqueMerge,
    TypedNamespace,
    is_array,
    is_scalar,
    opaque,
    typed_merge,
)


def _ip_factory(v):
    """An ipaddress-style factory FUNCTION (not a class), used as a field hint.

    Mirrors netutils.IPNetwork/IPAddress: callable, coerces a raw value, but is
    not a class so it cannot be an argument to issubclass()/isinstance().
    """
    return f"net:{v}"


class _NetConfig(Namespace):
    # A field annotated by a factory function rather than a class — this is the
    # real-world shape that used to crash typed_merge (issubclass() arg 1).
    network: _ip_factory


# ---------------------------------------------------------------------------
# is_scalar / is_array helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_scalar_primitives(self):
        for v in (1, 3.14, True, False, "hello", b"bytes", None):
            assert is_scalar(v), f"{v!r} should be scalar"

    def test_not_scalar(self):
        for v in ([], {}, (1, 2)):
            assert not is_scalar(v), f"{v!r} should not be scalar"

    def test_array_list(self):
        assert is_array([1, 2, 3])

    def test_array_tuple(self):
        assert is_array((1, 2))

    def test_not_array_dict(self):
        assert not is_array({"a": 1})

    def test_not_array_string(self):
        assert not is_array("hello")

    def test_mutable_array(self):
        assert is_array([1], mutable=True)
        assert not is_array((1,), mutable=True)


# ---------------------------------------------------------------------------
# Simple merge
# ---------------------------------------------------------------------------


class TestSimpleMerge:
    m = MergeMethod.Simple

    def test_scalar_replaces(self):
        assert self.m(1, 2) == 2

    def test_none_b_returns_a(self):
        assert self.m("keep", None) == "keep"

    def test_dict_update_shallow(self):
        a = {"x": 1, "y": 2}
        result = self.m(a, {"y": 99, "z": 3})
        assert result == {"x": 1, "y": 99, "z": 3}

    def test_dict_replaces_scalar(self):
        assert self.m("old", {"k": "v"}) == {"k": "v"}

    def test_list_element_replace(self):
        result = self.m([1, 2, 3], [10, 20])
        assert result == [10, 20, 3]

    def test_list_extends_when_b_longer(self):
        result = self.m([1], [10, 20, 30])
        assert result == [10, 20, 30]

    def test_list_replaces_scalar(self):
        assert self.m("old", [1, 2]) == [1, 2]

    def test_unsupported_type_raises(self):
        class Weird:
            pass

        with pytest.raises(TypeError):
            self.m({}, Weird())


# ---------------------------------------------------------------------------
# Substitute merge
# ---------------------------------------------------------------------------


class TestSubstituteMerge:
    m = MergeMethod.Substitute

    def test_scalar_replaces(self):
        assert self.m("a", "b") == "b"

    def test_list_replaces(self):
        assert self.m([1, 2], [3, 4]) == [3, 4]

    def test_list_replaces_scalar(self):
        assert self.m("x", [1, 2]) == [1, 2]

    def test_none_b_noop(self):
        assert self.m("keep", None) == "keep"

    def test_dict_recursive(self):
        a = {"a": 1, "b": {"x": 10, "y": 20}}
        b = {"b": {"y": 99, "z": 30}, "c": 3}
        result = self.m(a, b)
        assert result == {"a": 1, "b": {"x": 10, "y": 99, "z": 30}, "c": 3}

    def test_dict_from_list_of_dicts(self):
        a = {"a": 1}
        b = [{"b": 2}, {"c": 3}]
        result = self.m(a, b)
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_dict_from_list_with_non_dict_raises(self):
        with pytest.raises(TypeError):
            self.m({"a": 1}, [42])


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    m = MergeMethod.Deep

    def test_scalar_replaces(self):
        assert self.m(1, 2) == 2

    def test_none_b_noop(self):
        assert self.m("keep", None) == "keep"

    def test_none_a_takes_b(self):
        assert self.m(None, 42) == 42

    def test_dict_deep(self):
        a = {"a": 1, "b": {"x": 10, "y": 20}}
        b = {"b": {"y": 99, "z": 30}, "c": 3}
        result = self.m(a, b)
        assert result == {"a": 1, "b": {"x": 10, "y": 99, "z": 30}, "c": 3}

    def test_list_extends_unique_scalars(self):
        result = self.m([1, 2, 3], [3, 4, 5])
        # 3 is already in a; 4 and 5 are appended
        assert set(result) == {1, 2, 3, 4, 5}

    def test_list_does_not_duplicate(self):
        result = self.m([1, 2], [1, 2])
        assert result == [1, 2]

    def test_dict_from_list(self):
        a = {"a": 1}
        b = [{"b": 2}]
        result = self.m(a, b)
        assert result == {"a": 1, "b": 2}

    def test_mergelists_false_no_positional_dict_merge(self):
        a = [{"k": 1}]
        b = [{"k": 2}]
        # Without mergelists, dicts in lists are appended
        result = self.m(a, b, mergelists=False)
        assert len(result) == 2

    def test_mergelists_true_merges_matching_dicts(self):
        a = [{"k": 1, "v": "a"}]
        b = [{"k": 1, "v": "b"}]
        result = self.m(a, b, mergelists=True)
        # dicts share key "k" → positional merge → single dict
        assert len(result) == 1
        assert result[0]["v"] == "b"

    def test_mergelists_true_keeps_nonoverlapping_dict(self):
        # Regression: a positionally-matched b dict sharing NO key with the a
        # dict used to be popped before the overlap check, so it was neither
        # merged nor appended — it vanished (returned [{'k': 1}]).
        result = self.m([{"k": 1}], [{"z": 9}], mergelists=True)
        assert result == [{"k": 1}, {"z": 9}]

    def test_mergelists_true_mixed_overlap_and_nonoverlap(self):
        # First position overlaps (merges in place), second does not (appends).
        a = [{"k": 1}, {"p": 1}]
        b = [{"k": 2}, {"q": 2}]
        result = self.m(a, b, mergelists=True)
        assert result == [{"k": 2}, {"p": 1}, {"q": 2}]


# ---------------------------------------------------------------------------
# typed_merge
# ---------------------------------------------------------------------------


class TestTypedMerge:
    def test_scalar_last_wins(self):
        assert typed_merge(int, 1, 2, 3) == 3

    def test_none_returns_none(self):
        assert typed_merge(int) is None

    def test_dict_merge(self):
        result = typed_merge(dict, {"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_exported_from_package_root(self):
        import yaconfiglib

        assert yaconfiglib.typed_merge is typed_merge
        assert "typed_merge" in yaconfiglib.__all__


# ---------------------------------------------------------------------------
# typed_merge — parametrized generics
#
# Regression cluster: generic args were read off the ORIGINAL `cls` rather than
# the union-unwrapped origin, and the sequence branch tested a class object with
# instance checks so it could never run. Result: plain generics coerced nothing,
# union-wrapped generics picked NoneType as the child type and crashed, and
# sequence element types were ignored. Nothing in the suite exercised `cls_args`
# — every typed case used dataclass/Namespace field hints — which is how all
# three shipped green. `typing.Dict`/`List` spellings keep the 3.9 floor.
# ---------------------------------------------------------------------------


class TestTypedMergeGenerics:
    def test_mapping_value_type_is_coerced(self):
        # Was {'a': '1'}: cls_args came back empty for a non-union generic, so
        # child_cls was never set and the value passed through uncoerced.
        result = typed_merge(typing.Dict[str, int], {"a": "1"})
        assert result == {"a": 1}
        assert isinstance(result["a"], int)

    def test_mapping_value_coercion_across_several_objects(self):
        result = typed_merge(typing.Dict[str, int], {"a": "1"}, {"a": "2", "b": "3"})
        assert result == {"a": 2, "b": 3}
        assert all(isinstance(v, int) for v in result.values())

    def test_sequence_element_type_is_coerced(self):
        # Was [1, 2]: the sequence branch was unreachable, so this fell through
        # to the scalar tail and returned the list unchanged.
        result = typed_merge(typing.List[str], [1, 2])
        assert result == ["1", "2"]
        assert all(isinstance(v, str) for v in result)

    def test_sequence_last_object_wins(self):
        # Sequences keep last-object-wins semantics — not element-wise merging.
        assert typed_merge(typing.List[str], [1, 2, 3], [9]) == ["9"]

    def test_tuple_origin_rebuilds_a_tuple(self):
        result = typed_merge(typing.Tuple[str, ...], (1, 2))
        assert result == ("1", "2")
        assert isinstance(result, tuple)

    def test_optional_mapping_merges_instead_of_crashing(self):
        # Was TypeError: NoneType takes no arguments — cls_args held the
        # UNION's args, so child_cls became NoneType.
        result = typed_merge(
            typing.Optional[typing.Dict[str, int]], {"a": 1}, {"b": "2"}
        )
        assert result == {"a": 1, "b": 2}

    def test_optional_sequence_coerces_elements(self):
        assert typed_merge(typing.Optional[typing.List[str]], [1, 2]) == ["1", "2"]

    def test_optional_scalar_last_wins(self):
        # Union unwrap leaves a bare `int`, which has no args — the scalar tail.
        assert typed_merge(typing.Optional[int], 1, "2") == 2

    def test_str_hint_is_not_treated_as_a_sequence(self):
        # str/bytes are Sequences; if they reached the sequence branch a string
        # hint would be rebuilt character by character.
        assert typed_merge(str, "hello") == "hello"
        assert typed_merge(typing.Optional[str], "a", "b") == "b"

    def test_bare_unparametrized_generic_has_no_args(self):
        # No args → child_cls stays None → elements keep their own types.
        result = typed_merge(list, [1, "a"])
        assert result == [1, "a"]

    def test_dataclass_field_annotated_bare_list(self):
        # Pins the no-args path through a dataclass field hint: the element
        # types must survive untouched rather than being coerced to anything.
        @dataclass
        class Cfg:
            items: list = field(default_factory=list)

        merged = typed_merge(Cfg, Cfg(items=[1, 2]), Cfg(items=[3]))
        assert merged.items == [3]

    def test_dataclass_field_annotated_parametrized_list(self):
        @dataclass
        class Cfg:
            ports: typing.List[int] = field(default_factory=list)

        merged = typed_merge(Cfg, Cfg(ports=["1"]), Cfg(ports=["8080", "443"]))
        assert merged.ports == [8080, 443]
        assert all(isinstance(p, int) for p in merged.ports)

    def test_dataclass_field_annotated_parametrized_dict(self):
        @dataclass
        class Cfg:
            limits: typing.Dict[str, int] = field(default_factory=dict)

        merged = typed_merge(Cfg, Cfg(limits={"a": "1"}), Cfg(limits={"b": "2"}))
        assert merged.limits == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# typed_merge — non-class type hints (factory functions, opaque objects)
# ---------------------------------------------------------------------------


class TestTypedMergeNonClassHint:
    def test_factory_function_hint_coerces_last_value(self):
        # A non-class callable origin: last value wins, coerced through it.
        assert typed_merge(_ip_factory, "10.0.0.0/8", "192.168.0.0/16") == (
            "net:192.168.0.0/16"
        )

    def test_factory_rejecting_value_falls_back_to_raw(self):
        def strict(v):
            if not isinstance(v, str):
                raise TypeError("need a str")
            return v.upper()

        # The factory rejects an int → merge stays total, returning the raw
        # last value rather than raising.
        assert typed_merge(strict, 1, 2) == 2

    def test_non_class_non_callable_hint_last_wins(self):
        sentinel = object()  # neither a class nor callable
        assert typed_merge(sentinel, 1, 2) == 2

    def test_factory_field_hint_on_namespace_does_not_crash(self):
        # The regression: a Namespace field annotated by a factory function.
        a = _NetConfig(network="10.0.0.0/8")
        b = _NetConfig(network="192.168.0.0/16")
        merged = typed_merge(_NetConfig, a, b)
        assert merged.network == "net:192.168.0.0/16"


# ---------------------------------------------------------------------------
# typed_merge extension hooks: OpaqueMerge / opaque / TypedNamespace
# ---------------------------------------------------------------------------


class TestTypedMergeHooks:
    def test_opaque_mixin_last_wins(self):
        class Zone(OpaqueMerge, Namespace):
            pass

        merged = typed_merge(Zone, Zone(x=1), Zone(x=2))
        assert merged.x == 2

    def test_opaque_decorator_last_wins(self):
        @opaque
        class Zone(Namespace):
            pass

        merged = typed_merge(Zone, Zone(x="a"), Zone(x="z"))
        assert merged.x == "z"

    def test_opaque_bypasses_factory_function_field_hint(self):
        # Without opacity a factory-function field hint drives per-field
        # coercion; OpaqueMerge skips all field introspection.
        class Zone(OpaqueMerge, Namespace):
            network: _ip_factory  # a factory function, not a class

        merged = typed_merge(Zone, Zone(network="a"), Zone(network="b"))
        assert merged.network == "b"  # opaque → the raw last value, uncoerced

    def test_typed_namespace_applies_parse_hooks(self):
        class Cfg(TypedNamespace):
            def _parse_port(self, v):
                return int(v)

        cfg = Cfg(port="8080", host="db")
        assert cfg.port == 8080
        assert cfg.host == "db"

    def test_hooks_exported_from_package_root(self):
        import yaconfiglib

        for name in ("OpaqueMerge", "opaque", "TypedNamespace"):
            assert name in yaconfiglib.__all__
            assert getattr(yaconfiglib, name) is not None
