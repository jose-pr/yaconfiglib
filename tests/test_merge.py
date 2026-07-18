"""
Tests for MergeMethod (simple, substitute, deep) and typed_merge.
"""
from argparse import Namespace

import pytest

from yaconfiglib.utils.merge import MergeMethod, is_array, is_scalar, typed_merge


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
