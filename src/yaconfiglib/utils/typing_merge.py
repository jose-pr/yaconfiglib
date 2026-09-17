from __future__ import annotations

import collections
import collections.abc as _abc
import contextlib
import inspect
import sys
import types
import typing
from argparse import Namespace
from dataclasses import fields as _dc_fields
from dataclasses import is_dataclass

from .. import errors as _errors
from ..errors import ConfigTypeError, ConfigValueError

T = typing.TypeVar("T")

__all__ = ["typed_merge", "OpaqueMerge", "opaque", "TypedNamespace"]

_NONE_TYPE = type(None)
# Deliberately a superset of backends/env.py's word sets: typed_merge sees raw
# strings from INI and dotenv, where '1'/'0' are common spellings of a flag.
# Never import those sets from a backend — utils must not depend on backends.
_BOOL_WORDS = {
    **{word: True for word in ("true", "yes", "on", "1")},
    **{word: False for word in ("false", "no", "off", "0")},
}
# types.UnionType (the X | Y syntax at runtime) is 3.10+; guard with getattr.
_UNION_TYPE = getattr(types, "UnionType", None)


def _strip_annotated(hint: object) -> object:
    """Return *hint* with any ``Annotated[...]`` wrappers removed."""
    while typing.get_origin(hint) is typing.Annotated:
        hint = typing.get_args(hint)[0]
    return hint


def _runtime_class(member: object) -> type | None:
    """The class an ``isinstance`` test for union *member* should use, if any."""
    member = _strip_annotated(member)
    cls = typing.get_origin(member) or member
    if cls is typing.Any or not isinstance(cls, type):
        return None
    return cls


def _resolve_hint(hint: object, value: object) -> object:
    """Normalize *hint* for merging *value*: strip Annotated, pick a union member.

    A union drops its ``NoneType`` members (None sources are filtered out before
    this is called), collapses to ``typing.Any`` if any member is ``Any``, and
    otherwise resolves to the first member *value* is already an instance of —
    so a hint that already accepts the value never coerces it. Failing that it
    falls back to the documented first member.
    """
    while True:
        hint = _strip_annotated(hint)
        origin = typing.get_origin(hint)
        if origin is typing.Union or (
            _UNION_TYPE is not None and origin is _UNION_TYPE
        ):
            members = [m for m in typing.get_args(hint) if m is not _NONE_TYPE]
            if not members:
                return _NONE_TYPE
            if any(_strip_annotated(m) is typing.Any for m in members):
                return typing.Any
            chosen = None
            for member in members:
                runtime_cls = _runtime_class(member)
                if runtime_cls is None:
                    continue
                try:
                    matches = isinstance(value, runtime_cls)
                except TypeError:
                    # A non-runtime-checkable Protocol; it cannot claim the value.
                    continue
                if matches:
                    chosen = member
                    break
            hint = members[0] if chosen is None else chosen
            continue
        return hint


if sys.version_info >= (3, 14):

    def _own_annotations(klass: type) -> dict:
        # PEP 649: a class __dict__ no longer carries __annotations__ at all.
        import annotationlib

        return annotationlib.get_annotations(
            klass, format=annotationlib.Format.FORWARDREF
        )

else:

    def _own_annotations(klass: type) -> dict:
        return klass.__dict__.get("__annotations__", {})


def _type_hints(origin: type) -> dict:
    """Resolve *origin*'s annotations, entry by entry if the whole set fails.

    ``typing.get_type_hints`` is all-or-nothing: one unresolvable forward
    reference used to leave every sibling field unhinted and so uncoerced. The
    fallback walks the MRO base-first and resolves each class's own annotations
    one at a time, keeping what resolves and dropping only what does not.
    """
    try:
        return typing.get_type_hints(origin)
    except (TypeError, NameError, AttributeError):
        # A new exception type here is a bug we want to see, not swallow
        # (project error-handling stance: no bare except Exception).
        pass

    hints: dict = {}
    for klass in reversed(getattr(origin, "__mro__", (origin,))):
        for name, annotation in _own_annotations(klass).items():
            holder = types.SimpleNamespace(__annotations__={name: annotation})
            try:
                hints.update(
                    typing.get_type_hints(
                        holder,
                        # sys.modules.get, not indexing: a KeyError from a class
                        # whose module is not imported would escape this except.
                        globalns=getattr(
                            sys.modules.get(klass.__module__), "__dict__", {}
                        ),
                        localns=dict(vars(klass)),
                    )
                )
            except (TypeError, NameError, AttributeError):
                # A subclass re-annotating a name unresolvably must not leave the
                # base class's resolved hint in place.
                hints.pop(name, None)
    return hints


#: "this failure is the model's, not one field's" — a construction error.
_NO_FIELD = object()


@contextlib.contextmanager
def _field(origin: object, segment: object = _NO_FIELD):
    """Attribute a coercion or construction failure to *origin*'s *segment*.

    Only `TypeError` and `ValueError` are caught — the two types coercion
    constructors and this module's own raises produce — and the error is always
    re-raised, with its type and identity untouched. Wrapped **outside** the
    two internal fallbacks (`_construct_sequence`'s retry and the mapping
    retry), so an error those swallow on purpose is never annotated.
    """
    try:
        yield
    except (TypeError, ValueError) as error:
        _attribute_field(error, origin, segment)
        raise


def _attribute_field(
    error: BaseException, origin: object, segment: object = _NO_FIELD
) -> None:
    """Record *segment* and *origin*'s name on *error*, then let it travel on.

    The key is built from the inside out: each level prepends its own field or
    index to whatever path the level below recorded, so an error from deep in a
    model reads `at db.hosts[0].port (Outer)` — relative to the model the
    caller actually asked for, whose name wins because outer handlers run last.

    Pass no *segment* for a construction failure: the error belongs to this
    model as a whole, not to one of its fields.
    """
    model = getattr(origin, "__name__", None)
    if segment is _NO_FIELD:
        _errors._add_error_context(error, model=model)
        return
    existing = getattr(error, "config_key", ()) or ()
    # Set directly: _add_error_context keeps the first key it is given, and here
    # each level legitimately extends the path.
    try:
        error.config_key = (segment,) + tuple(existing)
    except (AttributeError, TypeError):
        pass
    _errors._add_error_context(error, model=model)


def _construct_sequence(origin: type, value: object, items: list) -> object:
    """Build *items* into *origin*, or fall back to *value* if it refuses them."""
    target = origin
    if inspect.isabstract(origin):
        # Sequence[str] / MutableSequence[int] resolve to an abstract class that
        # cannot be instantiated: keep the value's own concrete type, else list.
        target = type(value) if isinstance(value, _abc.Sequence) else list
    try:
        return target(items)
    except TypeError:
        # An unconstructible sequence type — a tuple subclass with a fixed
        # __new__, say. Restore the last-value-wins result this branch gave
        # before it started rebuilding. Element errors are raised above, so
        # this catch cannot swallow one.
        return value


def _merge_sequence(
    origin: type, cls_args: tuple, hints: dict, value: object, init: bool
) -> object:
    """Rebuild *value* as a sequence of type *origin*, coercing every item."""
    if (
        isinstance(value, str)
        or isinstance(value, _abc.Mapping)
        or not isinstance(value, _abc.Iterable)
    ):
        # A scalar or mapping where a sequence was declared is an authoring
        # error the merge cannot guess at (wrap it as one item? split it?).
        # bytes stays allowed: a bytearray hint legitimately consumes it.
        raise ConfigTypeError(
            f"cannot merge a {type(value).__name__} value into sequence type "
            f"{origin.__name__}"
        )

    if origin is range:
        # A range cannot be rebuilt from its items; do not walk it to find out.
        return value

    fields = getattr(origin, "_fields", None)
    if issubclass(origin, tuple) and fields is not None:
        # A NamedTuple: each item takes its field's hint and the constructor
        # enforces arity. Never zip(fields, value) — zip truncates, so a
        # three-item value would silently come back as a two-field instance.
        items = []
        for i, item in enumerate(value):
            hint = hints.get(fields[i], type(item)) if i < len(fields) else type(item)
            with _field(origin, fields[i] if i < len(fields) else i):
                items.append(typed_merge(hint, item, init=init))
        with _field(origin):
            return origin(*items)

    child_cls = cls_args[0] if cls_args else None
    if issubclass(origin, tuple):
        # 3.9 spells Tuple[()]'s args as ((),) where later versions give ().
        args = () if cls_args == ((),) else cls_args
        if args and not (len(args) == 2 and args[1] is Ellipsis):
            items = list(value)
            if len(items) != len(args):
                raise ConfigTypeError(
                    f"cannot merge {len(items)} items into a "
                    f"{len(args)}-element {origin.__name__} hint"
                )
            coerced = []
            for index, (arg, item) in enumerate(zip(args, items)):
                with _field(origin, index):
                    coerced.append(typed_merge(arg or type(item), item, init=init))
            return _construct_sequence(origin, value, coerced)
        child_cls = args[0] if args else None

    items = []
    for index, item in enumerate(value):
        with _field(origin, index):
            items.append(typed_merge(child_cls or type(item), item, init=init))
    return _construct_sequence(origin, value, items)


def _construct_mapping(origin: type, objects: tuple, merged: dict) -> object:
    """Build *merged* into mapping type *origin*."""
    if inspect.isabstract(origin):
        # Mapping[str, int] resolves to an abstract class, the way an abstract
        # sequence hint does; dict is the concrete stand-in.
        return dict(merged)
    if issubclass(origin, collections.defaultdict):
        # defaultdict's first positional argument is the factory, and it
        # refuses a mapping there. Carry the last source's factory over.
        factory = None
        for obj in objects:
            if isinstance(obj, collections.defaultdict):
                factory = obj.default_factory
        return origin(factory, merged)
    try:
        # Positional: mapping keys are data, not identifiers. dict, OrderedDict
        # and TypedDict classes all accept a mapping.
        return origin(merged)
    except TypeError:
        if all(isinstance(key, str) for key in merged):
            # A dict subclass whose __init__ takes only **kwargs. The values are
            # already merged, so this retry cannot hide a nested error.
            return origin(**merged)
        raise


def _merge_fields(
    origin: type, cls_args: tuple, hints: dict, objects: tuple, init: bool
) -> object:
    """Collect every object's fields, merge each, and build one *origin*."""
    is_mapping = issubclass(origin, _abc.Mapping)
    key_cls = cls_args[0] if is_mapping and len(cls_args) > 1 else None
    child_cls = cls_args[1] if is_mapping and len(cls_args) > 1 else None

    # A TypedNamespace normalizes its fields in __init__, so build the instance
    # up front and apply its hooks once, here, rather than calling __init__ on
    # values that have already been parsed.
    target = origin.__new__(origin) if issubclass(origin, TypedNamespace) else None

    collected: dict[object, list] = {}
    for obj in objects:
        props = obj if isinstance(obj, _abc.Mapping) else vars(obj)
        # A TypedNamespace source was normalized at construction; re-running its
        # own hooks would parse the same value twice.
        parsed_source = isinstance(obj, TypedNamespace)
        for prop, value in props.items():
            if key_cls is not None:
                # Coerce keys during collection, so '80' and 80 group as ONE
                # entry: JSON, TOML, INI, dotenv and env only produce str keys,
                # which would otherwise make Dict[int, str] unsatisfiable.
                prop = typed_merge(key_cls, prop, init=init)
            if not parsed_source:
                parser = getattr(obj, f"_parse_{prop}", None)
                if parser is None and target is not None:
                    parser = getattr(target, f"_parse_{prop}", None)
                if parser is not None:
                    value = parser(value)
            collected.setdefault(prop, []).append(value)

    merged: dict[object, object] = {}
    for name, values in collected.items():
        # The fallback hint comes from the last NON-None value: type(None)
        # would drive the merge into NoneType and crash. A field whose every
        # value is None stays None.
        present = [value for value in values if value is not None]
        if not present:
            merged[name] = None
            continue
        hint = hints.get(name, child_cls or type(present[-1]))
        with _field(origin, name):
            merged[name] = (
                typed_merge(hint, *values, init=init) if hint else present[-1]
            )

    if target is not None:
        for prop, value in merged.items():
            setattr(target, prop, value)
        return target

    if is_mapping:
        if init:
            with _field(origin):
                return _construct_mapping(origin, objects, merged)
        if inspect.isabstract(origin):
            return dict(merged)
        inst = origin.__new__(origin)
        for prop, value in merged.items():
            inst[prop] = value
        return inst

    if init:
        if is_dataclass(origin):
            # A field(init=False) name is not a constructor parameter — it is
            # recomputed by __post_init__ or its default. Unknown keys still
            # pass through, which a custom **kwargs __init__ relies on.
            for name in (f.name for f in _dc_fields(origin) if not f.init):
                merged.pop(name, None)
        with _field(origin):
            return origin(**merged)

    inst = origin.__new__(origin)
    for prop, value in merged.items():
        setattr(inst, prop, value)
    return inst


def typed_merge(cls: type[T], *objects: object, init: bool = True) -> T:
    """Recursively merge *objects* into an instance of *cls*.

    ``None`` objects are skipped at every level, so a later ``None`` never
    overrides an earlier value; if nothing but ``None`` is given the result is
    ``None``.

    The merge is type-guided: for mappings/dataclasses, fields are collected
    across all objects and merged field-by-field; for sequences, the last
    object's value is taken with each element coerced through the element type
    (a str or mapping value for a sequence hint raises ``TypeError``). For
    simple scalars, the last object wins, coerced through the hint — a ``bool``
    hint reads true/yes/on/1 and false/no/off/0 and raises ``ValueError`` on any
    other string.
    """
    objects = tuple(obj for obj in objects if obj is not None)
    if not objects:
        return None

    hints: dict[str, type] = {}
    child_cls: type | None = None

    # Normalize the hint once, against the value it will be applied to: strip
    # Annotated, and unwrap a union to a single member (NoneType members dropped).
    origin = _resolve_hint(cls, objects[-1])

    # Generic args come from the UNWRAPPED origin, and must be read before the
    # get_origin strip below. Reading them from `cls` was wrong in both
    # directions: for a plain generic the unwrap leaves `origin is cls`, so
    # the old `if cls is not origin else ()` guard yielded () and no element type
    # was ever recovered; for a union it yielded the UNION's args, so
    # Optional[Dict[str, int]] produced child_cls=NoneType and merging crashed
    # with "NoneType takes no arguments".
    cls_args = typing.get_args(origin)
    _stripped = typing.get_origin(origin)
    origin = origin if _stripped is None else _stripped

    # Looked up on the STRIPPED origin: typing aliases do not forward dunders,
    # so reading it off `cls` missed the hook for Optional[Zone], MyGeneric[int]
    # and every other parameterized or unioned spelling of an opaque type.
    merge_fn = getattr(origin, "__merge__", None)
    if merge_fn:
        return merge_fn(*objects, init=init)

    # typing.Any is a class on 3.11+, so it would otherwise reach the branch
    # selection below instead of the non-class escape hatch. Short-circuit it on
    # every version to the documented rule the 3.9 path already gave: last wins.
    if origin is typing.Any:
        return objects[-1]

    # A resolved hint that is not a class (e.g. an ipaddress-style factory
    # FUNCTION used as a field annotation, such as netutils.IPNetwork) cannot
    # drive the issubclass()/isinstance(_, origin) branch selection below and
    # would raise TypeError. Treat it as an opaque coercer: last value wins,
    # coerced through the callable when it is one, else returned as-is. This
    # mirrors the scalar tail, which is unreachable for a non-class origin.
    if not isinstance(origin, type):
        value = objects[-1]
        if callable(origin):
            try:
                return origin(value)
            except (TypeError, ValueError):
                return value
        return value

    hints = _type_hints(origin)

    # Sequence type: use the last object, converting each element.
    # The test is a CLASS test on the unwrapped origin. It used to be
    # `is_array(origin) and not is_scalar(origin)` — instance checks applied to a
    # class object, so `isinstance(list, (list, tuple))` was False and this whole
    # branch was unreachable; List[str] fell through to the scalar tail and
    # returned its elements uncoerced. str/bytes are Sequences too and must be
    # excluded, or every string hint would be rebuilt character by character.
    if issubclass(origin, _abc.Sequence) and not issubclass(origin, (str, bytes)):
        return _merge_sequence(origin, cls_args, hints, objects[-1], init)

    # Mapping / Namespace / dataclass: merge field by field.
    if issubclass(origin, (_abc.Mapping, Namespace)) or is_dataclass(origin):
        return _merge_fields(origin, cls_args, hints, objects, init)

    # Scalar / unknown: last value wins.
    value = objects[-1]
    if isinstance(value, origin):
        return value
    if origin is bool and isinstance(value, str):
        # bool('false') is True, which silently inverts every flag read from a
        # format that has no booleans (INI, dotenv, a command's output). An
        # unrecognized word raises, the way int('abc') does.
        try:
            return _BOOL_WORDS[value.strip().lower()]
        except KeyError:
            raise ConfigValueError(f"cannot interpret {value!r} as a bool") from None
    return origin(value)


# ---------------------------------------------------------------------------
# Extension hooks for consumers
#
# ``typed_merge`` honors two per-type hooks:
#   * ``__merge__(cls, *objects, init=True)`` — a classmethod that fully
#     overrides how instances of ``cls`` are merged.
#   * ``_parse_<field>(value)`` — a per-field coercer looked up on each source
#     object as a field is collected.
# The helpers below package the two most common uses so consumers do not
# reimplement them.
# ---------------------------------------------------------------------------


def _last_wins(_cls, *objects: object, init: bool = True) -> object:
    return objects[-1] if objects else None


class OpaqueMerge:
    """Mixin marking a type *opaque* to :func:`typed_merge` — last object wins.

    Inherit from ``OpaqueMerge`` when instances should NOT be re-merged field by
    field — a fully-built config object whose ``__init__`` already normalized
    its fields. ``typed_merge`` then returns the last object unchanged instead
    of introspecting its fields, including when the hint is ``Optional[...]`` or
    another union. Field annotations that are factory functions rather than
    classes are already coerced per field and need no mixin.
    """

    @classmethod
    def __merge__(cls, *objects: object, init: bool = True) -> object:
        return _last_wins(cls, *objects, init=init)


def opaque(cls: type) -> type:
    """Class decorator equivalent of :class:`OpaqueMerge`.

    Marks *cls* opaque to :func:`typed_merge` (last object wins) without altering
    its base classes — useful when the type already has a fixed hierarchy.
    """
    cls.__merge__ = classmethod(_last_wins)
    return cls


class TypedNamespace(Namespace):
    """An :class:`argparse.Namespace` that applies ``_parse_<field>`` coercers.

    At construction, for every keyword given, if the instance defines a
    ``_parse_<name>(value)`` method it is applied to that field's value. This is
    the same per-field coercion convention :func:`typed_merge` honors, applied at
    build time so a constructed object is already normalized (e.g. a raw string
    field turned into an ``ipaddress`` object). Compose with :class:`OpaqueMerge`
    when such a built object should also be opaque to re-merging.

    Every value is parsed exactly once when merged: :func:`typed_merge` applies
    these hooks to raw sources while collecting them, skips them for a source
    that is already a ``TypedNamespace``, and then assembles the result without
    calling ``__init__`` — so a parser need not be idempotent. A subclass whose
    ``__init__`` does more than parse fields should put that work in a
    ``_parse_<field>`` hook or in ``__merge__``.
    """

    def __init__(self, **kwargs: object) -> None:
        for name, value in list(kwargs.items()):
            parser = getattr(self, f"_parse_{name}", None)
            if parser is not None:
                kwargs[name] = parser(value)
        super().__init__(**kwargs)
