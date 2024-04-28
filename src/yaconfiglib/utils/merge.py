import logging
import types
import typing
from argparse import Namespace
from dataclasses import is_dataclass

from .enum import IntEnum

SCALAR = int | str | bool | float | types.NoneType | bytes


def is_scalar(obj) -> typing.TypeGuard[SCALAR]:
    return isinstance(obj, typing.get_args(SCALAR))


@typing.overload
def is_array(
    obj, mutable: typing.Literal[True]
) -> typing.TypeGuard[typing.MutableSequence]: ...
@typing.overload
def is_array(
    obj, mutable: typing.Literal[False] = False
) -> typing.TypeGuard[typing.Sequence]: ...
def is_array(obj, mutable=False) -> typing.TypeGuard[typing.Sequence]:
    return (
        isinstance(obj, typing.MutableSequence)
        if mutable
        else isinstance(obj, typing.Sequence)
    ) and not isinstance(obj, typing.Mapping)


@typing.runtime_checkable
class Merge(typing.Protocol):

    def __call__(
        self,
        a: object,
        b: object,
        *,
        logger: logging.Logger,
        memo: dict = None,
        **options,
    ) -> object:
        raise NotImplementedError()


class MergeMethod(IntEnum):
    Simple = 1
    Deep = 2
    Substitute = 3

    def __call__(
        self,
        a: object,
        b: object,
        *,
        logger: logging.Logger,
        memo: dict = None,
        **options,
    ):
        method: Merge = getattr(self, f"_{self.name.lower()}")
        return method(a, b, logger=logger, memo=memo, **options)

    def _simple(
        self,
        a: object,
        b: object,
        *,
        logger: logging.Logger,
        memo: dict = None,
        **options,
    ):
        logger.debug(
            "simplemerge %s (%s) and %s (%s)"
            % (
                a,
                type(a),
                b,
                type(b),
            )
        )
        if b is None:
            logger.debug("pass as b is None")
            pass
        elif is_scalar(b):
            logger.debug(
                'simplemerge: primitiveTypes replace a "%s"  w/ b "%s"'
                % (
                    a,
                    b,
                )
            )
            a = b
        elif is_array(b):
            logger.debug(
                'simplemerge: listTypes a "%s"  w/ b "%s"'
                % (
                    a,
                    b,
                )
            )
            if is_array(a):
                for k, v in enumerate(b):
                    try:
                        a[k] = self._simple(
                            a[k], b[k], logger=logger, memo=memo, **options
                        )
                    except IndexError:
                        a[k] = b[k]
            else:
                logger.debug(
                    "simplemerge: replace %s w/ list %s"
                    % (
                        a,
                        b,
                    )
                )
                a = b
        elif isinstance(b, typing.Mapping):
            if isinstance(a, typing.Mapping):
                logger.debug(
                    'simplemerge: update %s:"%s" by %s:"%s"'
                    % (
                        type(a),
                        a,
                        type(b),
                        b,
                    )
                )
                if isinstance(a, typing.MutableMapping):
                    a.update(b)
                else:
                    a = type(a)(**a, **b)
            else:
                logger.debug(
                    "simplemerge: replace %s w/ dict %s"
                    % (
                        a,
                        b,
                    )
                )
                a = b
        else:
            raise NotImplementedError(
                'can not (simple)merge %s to %s (@ "%s" try to merge "%s")'
                % (
                    type(b),
                    type(a),
                    a,
                    b,
                )
            )
        return a

    def _substitute(
        self,
        a: object,
        b: object,
        *,
        logger: logging.Logger,
        memo: dict = None,
        **options,
    ):
        logger.debug(">" * 30)
        logger.debug(
            "substmerge %s and %s"
            % (
                a,
                b,
            )
        )
        # FIXME: make None usage configurable
        if b is None:
            logger.debug("pass as b is None")
            pass

        # treat listTypes as primitiveTypes in merge
        # subsititues list, don't merge them

        if a is None or is_scalar(b) or is_array(b):
            logger.debug(
                'substmerge: replace a "%s"  w/ b "%s"'
                % (
                    a,
                    b,
                )
            )
            a = b

        elif isinstance(a, typing.Mapping):
            if isinstance(b, typing.Mapping):
                logger.debug(
                    'substmerge: dict ... "%s" and "%s"'
                    % (
                        a,
                        b,
                    )
                )
                for k in b:
                    if k in a:
                        logger.debug(
                            'substmerge dict: loop for key "%s": "%s" and "%s"'
                            % (
                                k,
                                a[k],
                                b[k],
                            )
                        )
                        a[k] = self._substitute(
                            a[k], b[k], logger=logger, memo=memo, **options
                        )
                    else:
                        logger.debug("substmerge dict: set key %s" % k)
                        a[k] = b[k]
            elif isinstance(b, typing.Sequence):
                logger.debug(
                    'substmerge: dict <- list ... "%s" <- "%s"'
                    % (
                        a,
                        b,
                    )
                )
                for bd in b:
                    if isinstance(bd, typing.Mapping):
                        a = self._substitute(a, bd, logger=logger, memo=memo, **options)
                    else:
                        raise NotImplementedError(
                            "can not merge element from list of type %s to dict "
                            '(@ "%s" try to merge "%s")'
                            % (
                                type(b),
                                a,
                                b,
                            )
                        )
            else:
                raise NotImplementedError(
                    'can not merge %s to %s (@ "%s" try to merge "%s")'
                    % (
                        type(b),
                        type(a),
                        a,
                        b,
                    )
                )
        logger.debug('end substmerge part: return: "%s"' % a)
        logger.debug("<" * 30)
        return a

    def _deep(
        self,
        a: object,
        b: object,
        *,
        logger: logging.Logger,
        memo: dict = None,
        mergelists: bool = None,
        **options,
    ):
        logger.debug(">" * 30)
        logger.debug(
            "deepmerge %s and %s"
            % (
                a,
                b,
            )
        )
        mergelists = False if mergelists is None else bool(mergelists)
        # FIXME: make None usage configurable
        if b is None:
            logger.debug("pass as b is None")
            pass
        if a is None or is_scalar(b):
            logger.debug(
                'deepmerge: replace a "%s"  w/ b "%s"'
                % (
                    a,
                    b,
                )
            )
            a = b
        elif is_array(a):
            if is_array(b):
                logger.debug(
                    'deepmerge: lists extend %s:"%s" by %s:"%s"'
                    % (
                        type(a),
                        a,
                        type(b),
                        b,
                    )
                )
                iter_ = (
                    be for be in b if be not in a and (is_scalar(be) or is_array(be))
                )
                if isinstance(a, typing.MutableSequence):
                    a.extend(iter_)
                else:
                    a = type(a)([*a, *iter_])
                srcdicts: dict[int, typing.Mapping] = {}
                for k, bd in enumerate(b):
                    if isinstance(bd, typing.Mapping):
                        srcdicts.update({k: bd})
                logger.debug("srcdicts: %s" % srcdicts)
                for k, ad in enumerate(a):
                    logger.debug(
                        'deepmerge ad "%s" w/ k "%s" of type %s' % (ad, k, type(ad))
                    )
                    if isinstance(ad, typing.Mapping):
                        if k in srcdicts:
                            # we merge only if at least one key in dict is matching
                            merge = False
                            if mergelists:
                                for ak in ad.keys():
                                    if ak in srcdicts[k].keys():
                                        merge = True
                                        break
                            if merge:
                                # pylint: disable=undefined-loop-variable
                                # FIXME undefined-loop-variable : this is not well readable !!!
                                logger.debug(
                                    "deepmerge ad: deep merge list dict elem w/ "
                                    'key:%s: "%s" and "%s"'
                                    % (
                                        ak,
                                        ad,
                                        srcdicts[k],
                                    )
                                )
                                a[k] = self._deep(
                                    ad,
                                    srcdicts[k],
                                    logger=logger,
                                    memo=memo,
                                    mergelists=mergelists,
                                    **options,
                                )
                                del srcdicts[k]
                logger.debug("deepmerge list: remaining srcdicts elems: %s" % srcdicts)
                for k, v in srcdicts.items():
                    logger.debug("deepmerge list: new dict append %s:%s" % (k, v))
                    a.append(v)
            else:
                raise NotImplementedError(
                    'can not merge %s to %s (@ "%s"  try to merge "%s")'
                    % (
                        type(b),
                        type(a),
                        a,
                        b,
                    )
                )
        elif isinstance(a, typing.Mapping):
            if isinstance(b, typing.Mapping):
                logger.debug(
                    'deepmerge: dict ... "%s" and "%s"'
                    % (
                        a,
                        b,
                    )
                )
                for k in b:
                    if k in a:
                        logger.debug(
                            'deepmerge dict: loop for key "%s": "%s" and "%s"'
                            % (
                                k,
                                a[k],
                                b[k],
                            )
                        )
                        a[k] = self._deep(
                            a[k],
                            b[k],
                            logger=logger,
                            memo=memo,
                            mergelists=mergelists,
                            **options,
                        )
                    else:
                        logger.debug("deepmerge dict: set key %s" % k)
                        a[k] = b[k]
            elif is_array(b):
                logger.debug(
                    'deepmerge: dict <- list ... "%s" <- "%s"'
                    % (
                        a,
                        b,
                    )
                )
                for bd in b:
                    if isinstance(bd, typing.Mapping):
                        a = self._deep(
                            a,
                            bd,
                            logger=logger,
                            memo=memo,
                            mergelists=mergelists,
                            **options,
                        )
                    else:
                        raise NotImplementedError(
                            "can not merge element from list of type %s to dict "
                            '(@ "%s" try to merge "%s")'
                            % (
                                type(b),
                                a,
                                b,
                            )
                        )
            else:
                raise NotImplementedError(
                    'can not merge %s to %s (@ "%s" try to merge "%s")'
                    % (
                        type(b),
                        type(a),
                        a,
                        b,
                    )
                )
        logger.debug('end deepmerge part: return: "%s"' % a)
        logger.debug("<" * 30)
        return a


T = typing.TypeVar("T")


def typed_merge(cls: type[T], *objects: object, init=True) -> T:
    # we assume all sequence can be init from iterable
    # all mapping and namespace/dataclass can be init with their props
    if not objects:
        return None

    merge = getattr(cls, "__merge__", None)
    if merge:
        return merge(*objects, init=init)

    hints: dict[str, type]
    child_cls: type = None
    cls_opts = ()

    while True:
        origin = getattr(cls, "__origin__", cls)
        if origin is typing.Union or origin is types.UnionType:
            cls_opts = typing.get_args(cls)
            cls = cls_opts[0]
            continue
        break

    try:
        hints = typing.get_type_hints(cls)
    except:
        hints = {}
    try:
        args = typing.get_args(cls)
    except:
        args = ()

    if issubclass(origin, typing.Mapping):
        if len(args) > 1:
            child_cls = args[1]
    elif issubclass(origin, typing.Sequence):
        if args:
            child_cls = args[0]

    if is_array(origin) and not is_scalar(origin):
        value = objects[-1]
        return cls(
            (typed_merge(child_cls or type(child), child, init=init) for child in value)
        )

    if issubclass(origin, (typing.Mapping, Namespace)) or is_dataclass(origin):
        fields: dict[str, list] = {}
        for obj in objects:
            props = obj if isinstance(obj, typing.Mapping) else vars(obj)
            for prop, value in props.items():
                parser = getattr(obj, f"_parse_{prop}", None)
                if parser:
                    value = parser(value)
                fields.setdefault(prop, []).append(value)

        merged: dict[str] = {}
        for name, values in fields.items():
            hint = type(values[-1])
            hint = hints.get(name, child_cls or hint)
            if not hint:
                value = values[-1]
            else:
                value = typed_merge(hint, *values, init=init)
            merged[name] = value

        if init:
            return cls(**merged)
        else:
            inst = cls.__new__(origin)
            if issubclass(origin, typing.MutableMapping):

                def setfield(prop, value):
                    inst[prop] = value

            else:

                def setfield(prop, value):
                    setattr(inst, prop, value)

            for prop, value in merged.items():
                setfield(prop, value)
            return inst

    value = objects[-1]
    if isinstance(value, origin):
        return value
    else:
        return cls(value)
