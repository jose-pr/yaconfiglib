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

    def test_binary_buffers_are_not_arrays(self):
        # A binary buffer is a value, not a sequence to merge element-wise.
        assert not is_array(bytearray(b"ab"))
        assert not is_array(memoryview(b"ab"))


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

    def test_list_replaced_wholesale(self):
        result = self.m([1, 2, 3], [10, 20])
        assert result == [10, 20]

    def test_list_under_key_replaced(self):
        assert self.m({"a": [1, 2, 3]}, {"a": [9]}) == {"a": [9]}

    def test_list_extends_when_b_longer(self):
        result = self.m([1], [10, 20, 30])
        assert result == [10, 20, 30]

    def test_list_replaces_scalar(self):
        assert self.m("old", [1, 2]) == [1, 2]

    def test_unknown_leaf_type_replaces(self):
        class Weird:
            pass

        weird = Weird()
        # Any value that is not a mapping or a list is a leaf, so it replaces
        # rather than raising.
        assert self.m({}, weird) is weird


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

    def test_dedupe_is_type_aware(self):
        # 1 == True == 1.0, but they are different configuration values, so an
        # override must not be swallowed as a duplicate.
        result = self.m([1, 0], [True, False, 1.0])

        assert [type(item) for item in result] == [int, int, bool, bool, float]

    @pytest.mark.parametrize("mergelists", [False, True], ids=["plain", "mergelists"])
    def test_extension_keeps_source_order(self, mergelists):
        result = self.m([], [1, {"a": 1}, 2], mergelists=mergelists)

        assert result == [1, {"a": 1}, 2]

    def test_unhashable_items_still_deduplicated(self):
        assert self.m([[1]], [[1], [2]]) == [[1], [2]]

    def test_mergelists_true_mixed_overlap_and_nonoverlap(self):
        # First position overlaps (merges in place), second does not (appends).
        a = [{"k": 1}, {"p": 1}]
        b = [{"k": 2}, {"q": 2}]
        result = self.m(a, b, mergelists=True)
        assert result == [{"k": 2}, {"p": 1}, {"q": 2}]


# ---------------------------------------------------------------------------
# Leaf values and shape changes
# ---------------------------------------------------------------------------

STRATEGIES = [MergeMethod.Simple, MergeMethod.Substitute, MergeMethod.Deep]
STRATEGY_IDS = ["Simple", "Substitute", "Deep"]


class _Colour(__import__("enum").Enum):
    """A plain Enum member: not an int, so no type whitelist would accept it."""

    RED = "red"
    BLUE = "blue"


def _leaf_document(which):
    import datetime
    import decimal

    Colour = _Colour

    if which == "a":
        return {
            "release": datetime.date(2024, 1, 1),
            "at": datetime.datetime(2024, 1, 1, 10, 0),
            "cutoff": datetime.time(9, 0),
            "rate": decimal.Decimal("1.5"),
            "colour": Colour.RED,
            "tags": {"one"},
        }
    return {
        "release": datetime.date(2025, 6, 1),
        "at": datetime.datetime(2025, 6, 1, 11, 30),
        "cutoff": datetime.time(17, 45),
        "rate": decimal.Decimal("2.5"),
        "colour": Colour.BLUE,
        "tags": {"two"},
    }


class TestLeafValues:
    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_non_whitelisted_leaf_replaces(self, strategy):
        a = {"section": _leaf_document("a")}
        b = {"section": _leaf_document("b")}

        result = strategy(a, b)

        assert result["section"] == _leaf_document("b")

    @pytest.mark.parametrize("mergelists", [False, True], ids=["plain", "mergelists"])
    def test_deep_list_keeps_non_whitelisted_items(self, mergelists):
        import datetime

        a = [datetime.date(2024, 1, 1), datetime.datetime(2024, 1, 1, 9, 0), {"x"}]
        b = [datetime.date(2025, 1, 1), datetime.datetime(2025, 1, 1, 9, 0), {"y"}]

        result = MergeMethod.Deep(a, b, mergelists=mergelists)

        for item in b:
            assert item in result

    def test_deep_empty_base_list_takes_dates(self):
        import datetime

        assert MergeMethod.Deep([], [datetime.date(2025, 1, 1)]) == [
            datetime.date(2025, 1, 1)
        ]

    def test_simple_list_of_dates_does_not_raise(self):
        import datetime

        result = MergeMethod.Simple(
            [datetime.date(2024, 1, 1)], [datetime.date(2025, 1, 1)]
        )

        assert result == [datetime.date(2025, 1, 1)]

    def test_deep_bytearray_replaces(self):
        assert MergeMethod.Deep(bytearray(b"ab"), bytearray(b"bc")) == bytearray(b"bc")


class TestTypeChangingOverrides:
    CASES = {
        "scalar_to_dict": (5, {"k": 1}),
        "dict_to_scalar": ({"k": 1}, 5),
        "str_to_list": ("a", [1, 2]),
        "list_to_dict": ([1, 2], {"k": 1}),
        "dict_to_scalar_list": ({"k": 1}, [1, 2]),
    }

    @pytest.mark.parametrize(
        "strategy",
        [MergeMethod.Substitute, MergeMethod.Deep],
        ids=["Substitute", "Deep"],
    )
    @pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
    def test_later_value_replaces(self, strategy, case):
        a, b = self.CASES[case]

        assert strategy({"x": a}, {"x": b})["x"] == b

    @pytest.mark.parametrize(
        "strategy",
        [MergeMethod.Substitute, MergeMethod.Deep],
        ids=["Substitute", "Deep"],
    )
    def test_mapping_folds_only_nonempty_all_mapping_lists(self, strategy):
        # A list of mappings folds into the mapping...
        assert strategy({"a": 1}, [{"b": 2}]) == {"a": 1, "b": 2}
        # ...anything else replaces it, an empty list included ("clear this").
        assert strategy({"a": 1}, [42]) == [42]
        assert strategy({"a": 1}, []) == []


# ---------------------------------------------------------------------------
# Copy-on-write contract
# ---------------------------------------------------------------------------


class TestMergeCopyOnWrite:
    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_inputs_unchanged(self, strategy):
        import copy

        a = {"db": {"host": "shared", "pool": 5}, "hosts": ["a"], "n": 1}
        b = {"db": {"host": "prod"}, "hosts": ["b"]}
        a_before = copy.deepcopy(a)
        b_before = copy.deepcopy(b)

        result = strategy(a, b)

        assert a == a_before
        assert b == b_before
        assert result is not a

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_mapping_subclass_preserved(self, strategy):
        import collections

        from yaconfiglib.loader import DotAccessibleDict

        ordered = collections.OrderedDict([("a", 1)])
        assert type(strategy(ordered, {"b": 2})) is collections.OrderedDict

        default = collections.defaultdict(list, {"a": 1})
        merged_default = strategy(default, {"b": 2})
        assert type(merged_default) is collections.defaultdict
        assert merged_default.default_factory is list

        dot = DotAccessibleDict({"a": 1})
        assert type(strategy(dot, {"b": 2})) is DotAccessibleDict

    def test_simple_readonly_mapping_with_overlapping_keys(self):
        import typing

        class ReadOnly(typing.Mapping):
            def __init__(self, **values):
                self._values = dict(values)

            def __getitem__(self, key):
                return self._values[key]

            def __iter__(self):
                return iter(self._values)

            def __len__(self):
                return len(self._values)

        result = MergeMethod.Simple(ReadOnly(a=1, b=2), {"b": 99})

        assert dict(result) == {"a": 1, "b": 99}

    @pytest.mark.parametrize(
        "strategy",
        [MergeMethod.Substitute, MergeMethod.Deep],
        ids=["Substitute", "Deep"],
    )
    def test_cyclic_mappings_merge(self, strategy):
        a = {"name": "one"}
        a["self"] = a
        b = {"name": "two"}
        b["self"] = b

        result = strategy(a, b)

        assert result["name"] == "two"
        assert result["self"] is result

    def test_node_aliased_in_both_inputs_stays_aliased(self):
        shared_a = {"k": 1}
        shared_b = {"k": 2}

        result = MergeMethod.Deep(
            {"x": shared_a, "y": shared_a}, {"x": shared_b, "y": shared_b}
        )

        assert result["x"] is result["y"]

    def test_simple_does_not_mutate_aliased_list_elements(self):
        element = {"x": 1}

        MergeMethod.Simple([element, element], [{"x": 2}])

        assert element == {"x": 1}


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


# ---------------------------------------------------------------------------
# typed_merge: None sources, unions, Any, Annotated and unresolvable hints
# ---------------------------------------------------------------------------


@dataclass
class _DB:
    host: str = ""
    port: int = 0


@dataclass
class _App:
    db: typing.Optional[_DB] = None
    name: str = ""


class _Zone(OpaqueMerge, Namespace):
    pass


@dataclass
class _PartiallyTyped:
    port: int = 0
    later: "NotDefinedAnywhere" = None  # noqa: F821 - unresolvable on purpose


class _Recorder:
    seen = None

    @classmethod
    def __merge__(cls, *objects, init=True):
        _Recorder.seen = objects
        return objects[-1]


class TestTypedMergeNoneUnionsAndHints:
    def test_optional_scalar_field_none_keeps_earlier_value(self):
        @dataclass
        class Cfg:
            name: typing.Optional[str] = None
            debug: typing.Optional[bool] = None

        merged = typed_merge(Cfg, Cfg(name="x", debug=True), Cfg())
        assert merged.name == "x"
        assert merged.debug is True

    def test_optional_dataclass_field_none_in_any_position(self):
        db = _DB(host="h", port=5)
        assert typed_merge(_App, _App(db=db), _App()).db == db
        assert typed_merge(_App, _App(), _App(db=db)).db == db
        # The same through mappings, which is how a config document arrives.
        merged = typed_merge(_App, {"db": {"host": "h", "port": "5"}}, {"db": None})
        assert merged.db == _DB(host="h", port=5)

    def test_none_source_does_not_stringify_or_falsify(self):
        assert typed_merge(str, "a", None) == "a"
        assert typed_merge(bool, True, None) is True
        assert typed_merge(int, 7, None) == 7

    def test_untyped_mapping_none_value_keeps_earlier_value(self):
        assert typed_merge(dict, {"a": 1}, {"a": None}) == {"a": 1}
        assert typed_merge(dict, {"a": 1}, {"a": None}, {"a": 3}) == {"a": 3}

    def test_all_none_sources_give_none(self):
        assert typed_merge(str, None, None) is None
        assert typed_merge(_App, None) is None
        assert typed_merge(typing.Optional[typing.List[str]], ["a"], None) == ["a"]
        assert typed_merge(dict, {"a": None}, {"a": None}) == {"a": None}

    def test_union_skips_nonetype_member(self):
        assert typed_merge(typing.Union[None, int], "5") == 5
        assert typed_merge(typing.Optional[typing.Dict[str, int]], {"a": "1"}) == {
            "a": 1
        }

    def test_union_keeps_value_matching_a_later_member(self):
        assert typed_merge(typing.Union[str, int], 5) == 5
        assert typed_merge(typing.Union[int, str], "abc") == "abc"
        # Documented change: a string a str member already accepts is kept.
        assert typed_merge(typing.Union[int, str], "8080") == "8080"

    def test_union_coerces_non_matching_value_through_first_member(self):
        # A pin on the documented first-member rule for a value no member takes.
        assert typed_merge(typing.Union[int, float], "2") == 2

    def test_opaque_type_inside_optional_stays_opaque(self):
        z1, z2 = _Zone(x="a"), _Zone(x="z")
        assert typed_merge(typing.Optional[_Zone], z1, z2) is z2

        @dataclass
        class Cfg:
            zone: typing.Optional[_Zone] = None

        merged = typed_merge(Cfg, Cfg(zone=z1), Cfg(zone=z2))
        assert merged.zone is z2

    def test_merge_hook_never_receives_none(self):
        _Recorder.seen = None
        r = _Recorder()
        assert typed_merge(_Recorder, r, None) is r
        assert _Recorder.seen == (r,)

    def test_any_hint_last_value_wins(self):
        assert typed_merge(typing.Any, {"a": 1}, "last") == "last"
        assert typed_merge(typing.Optional[typing.Any], 1, "last") == "last"
        assert typed_merge(typing.List[typing.Any], [1, "a", {"b": 2}]) == [
            1,
            "a",
            {"b": 2},
        ]
        assert typed_merge(
            typing.Dict[str, typing.Any], {"a": {"x": 1}}, {"a": "flat"}
        ) == {"a": "flat"}

    def test_dataclass_any_field(self):
        @dataclass
        class Cfg:
            extra: typing.Any = None

        merged = typed_merge(Cfg, Cfg(extra={"a": 1}), Cfg(extra=[1, 2]))
        assert merged.extra == [1, 2]

    def test_annotated_hint_is_unwrapped(self):
        assert typed_merge(typing.Annotated[int, "port"], "5") == 5
        assert typed_merge(
            typing.Dict[str, typing.Annotated[int, "port"]], {"a": "5"}
        ) == {"a": 5}
        assert typed_merge(typing.Optional[typing.Annotated[int, "port"]], "5") == 5

    def test_unresolvable_field_annotation_keeps_other_hints(self):
        merged = typed_merge(_PartiallyTyped, _PartiallyTyped(port="8080"))
        assert merged.port == 8080
        assert merged.later is None
