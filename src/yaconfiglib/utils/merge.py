import logging
import typing

from .enum import IntEnum

primitiveTypes = (int, str, bool, float)

strTypes = (str,)
listTypes = (list, tuple)


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
        elif isinstance(b, primitiveTypes):
            logger.debug(
                'simplemerge: primitiveTypes replace a "%s"  w/ b "%s"'
                % (
                    a,
                    b,
                )
            )
            a = b
        elif isinstance(b, listTypes):
            logger.debug(
                'simplemerge: listTypes a "%s"  w/ b "%s"'
                % (
                    a,
                    b,
                )
            )
            if isinstance(a, listTypes):
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
        elif isinstance(b, dict):
            if isinstance(a, dict):
                logger.debug(
                    'simplemerge: update %s:"%s" by %s:"%s"'
                    % (
                        type(a),
                        a,
                        type(b),
                        b,
                    )
                )
                a.update(b)
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

        if a is None or isinstance(b, primitiveTypes) or isinstance(b, listTypes):
            logger.debug(
                'substmerge: replace a "%s"  w/ b "%s"'
                % (
                    a,
                    b,
                )
            )
            a = b

        elif isinstance(a, dict):
            if isinstance(b, dict):
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
            elif isinstance(b, listTypes):
                logger.debug(
                    'substmerge: dict <- list ... "%s" <- "%s"'
                    % (
                        a,
                        b,
                    )
                )
                for bd in b:
                    if isinstance(bd, dict):
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
        if a is None or isinstance(b, primitiveTypes):
            logger.debug(
                'deepmerge: replace a "%s"  w/ b "%s"'
                % (
                    a,
                    b,
                )
            )
            a = b
        elif isinstance(a, listTypes):
            if isinstance(b, listTypes):
                logger.debug(
                    'deepmerge: lists extend %s:"%s" by %s:"%s"'
                    % (
                        type(a),
                        a,
                        type(b),
                        b,
                    )
                )
                a.extend(
                    be
                    for be in b
                    if be not in a
                    and (isinstance(be, primitiveTypes) or isinstance(be, listTypes))
                )
                srcdicts = {}
                for k, bd in enumerate(b):
                    if isinstance(bd, dict):
                        srcdicts.update({k: bd})
                logger.debug("srcdicts: %s" % srcdicts)
                for k, ad in enumerate(a):
                    logger.debug(
                        'deepmerge ad "%s" w/ k "%s" of type %s' % (ad, k, type(ad))
                    )
                    if isinstance(ad, dict):
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
        elif isinstance(a, dict):
            if isinstance(b, dict):
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
            elif isinstance(b, listTypes):
                logger.debug(
                    'deepmerge: dict <- list ... "%s" <- "%s"'
                    % (
                        a,
                        b,
                    )
                )
                for bd in b:
                    if isinstance(bd, dict):
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
