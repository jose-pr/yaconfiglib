from __future__ import unicode_literals

import enum as _enum
import io
import logging
import os
import re
import typing
from typing import Callable

import yaml
from jinja2 import TemplateError
from pathlib_next import Path
from yaml import parser

from yaconfiglib.jinja2 import jinja2_eval

primitiveTypes = (int, str, bool, float)

strTypes = (str,)
listTypes = (list, tuple)


class _IntEnum(_enum.IntEnum):
    @classmethod
    def _missing_(cls, value: object):
        if not isinstance(value, int):
            name = str(value).lower()
            for member in cls:
                if member.name.lower() == name:
                    return member
        super()._missing_(value)


@typing.runtime_checkable
class MergeMethodProtocol(typing.Protocol):

    def __call__(self, a: object, b: object, *, logger: logging.Logger) -> object:
        raise NotImplementedError()


class MergeMethod(_IntEnum):
    Simple = 1
    Deep = 2
    Substitute = 3

    def __call__(self, a: object, b: object, *, logger: logging.Logger):
        method: MergeMethodProtocol = getattr(self, f"_{self.name.lower()}")
        return method(a, b, logger=logger)

    def _simple(self, a: object, b: object, *, logger: logging.Logger):
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
                        a[k] = self._simple(a[k], b[k])
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

    def _substitute(self, a: object, b: object, *, logger: logging.Logger):
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
                        a[k] = self._substitute(a[k], b[k])
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
                        a = self._substitute(a, bd)
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

    def _deep(self, a: object, b: object, *, logger: logging.Logger):
        logger.debug(">" * 30)
        logger.debug(
            "deepmerge %s and %s"
            % (
                a,
                b,
            )
        )
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
                            if self.mergelists:
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
                                a[k] = self._deep(ad, srcdicts[k])
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
                        a[k] = self._deep(a[k], b[k])
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
                        a = self._deep(a, bd)
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


class LogLevel(_IntEnum):
    Critical = logging.CRITICAL
    Error = logging.ERROR
    Warning = logging.WARNING
    Info = logging.INFO
    Debug = logging.DEBUG


LOGGER = logging.getLogger(__name__)


class HieraConfigLoader:

    DEFAULT_LOADER: yaml.Loader = yaml.SafeLoader
    DEFAULT_DUMPER: yaml.Dumper = yaml.SafeDumper
    DEFAULT_ENCODING: str = "utf-8"

    def __init__(
        self,
        *sources: str | Path,
        method: MergeMethod | MergeMethodProtocol = MergeMethod.Simple,
        mergelists=True,
        interpolate=False,
        loader_cls: type[yaml.Loader] = None,
        encoding: str = None,
        logger: int | LogLevel | logging.Logger = LogLevel.Warning,
        missingfiles_level: int | LogLevel = LogLevel.Error,
        dumper_cls: type[yaml.Dumper] = None,
    ):
        self._data = None

        self.method = (
            method if isinstance(method, MergeMethodProtocol) else MergeMethod(method)
        )
        self.mergelists = True if mergelists is None else bool(mergelists)
        self.interpolate = True if interpolate is None else bool(interpolate)
        self.loader_cls = loader_cls or self.DEFAULT_LOADER
        self.dumper_cls = dumper_cls or self.DEFAULT_DUMPER
        self.missingfiles_level = LogLevel(missingfiles_level or LogLevel.Error)
        self.encoding = encoding or self.DEFAULT_ENCODING

        if isinstance(logger, logging.Logger):
            self.logger = logger
        else:
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(LogLevel(logger or LogLevel.Warning))

        self._sources = list(self._validate_sources(sources))

        for source in self._sources:
            self.logger.debug("yamlfile: %s ..." % source)
            if not source:
                continue
            if "\n" in source:
                self.logger.debug("loading yaml doc from str ...")
                f = source
                self._load_data(self.loader_cls, source)
            else:
                path = Path(source) if not isinstance(source, Path) else source

                try:
                    with path.open("r", encoding=self.encoding) as f:
                        logger.debug("open4reading: file %s" % f)
                        self._load_data(f)
                except IOError as e:
                    if self.missingfiles_level >= LogLevel.Error:
                        self.logger.log(self.missingfiles_level, e)
                        self.logger.log(
                            self.missingfiles_level,
                            "file not found: %s" % source,
                        )
                        raise FileNotFoundError(source)
                    self.logger.log(self.missingfiles_level, e)
                    self._sources.remove(source)
                    continue
        if self.interpolate:
            self._data = self._interpolate(self._data)

    def _load_data(self, f: str | io.IOBase):
        for ydata in yaml.load_all(f, self.loader_cls):
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug("yaml data: %s" % ydata)
            if self._data is None:
                self._data = ydata
            else:
                self._data = self.method(self._data, ydata, logger=self.logger)
                self.logger.debug("merged data: %s" % self._data)

    def _validate_sources(
        self,
        sources: typing.Sequence[str | Path | typing.Sequence[str | Path]],
        *,
        memo: list[str | Path] = None,
    ) -> typing.Iterator[str | Path]:
        if memo is None:
            memo = []
        for source in sources:
            if isinstance(source, (str, Path)):
                if source in memo:
                    self.logger.warning("ignoring duplicated file %s" % source)
                    continue
                yield source
            elif isinstance(source, typing.Sequence):
                yield from self._validate_sources(source, memo=memo)
            else:
                raise ValueError(
                    "unable to handle arg %s of type %s"
                    % (
                        source,
                        type(source),
                    )
                )

    def _interpolate(self, d):
        self.logger.debug(
            'interpolate "%s" of type %s ...'
            % (
                d,
                type(d),
            )
        )
        if d is None:
            return None
        if isinstance(d, strTypes):
            return self._interpolatestr(d)
        if isinstance(d, primitiveTypes):
            return d
        if isinstance(d, listTypes):
            for k, v in enumerate(d):
                d[k] = self._interpolate(v)
            return d
        if isinstance(d, dict):
            for k in d.keys():
                d[k] = self._interpolate(d[k])
            return d
        raise NotImplementedError(
            'can not interpolate "%s" of type %s'
            % (
                d,
                type(d),
            )
        )

    def _interpolatestr(self, s):
        try:
            si = jinja2_eval(s)(**self.data)
        except TemplateError as e:
            # FIXME: this seems to be broken for unicode str?
            raise Exception(
                'error interpolating string "%s" : %s'
                % (
                    s,
                    e,
                )
            )
        if not s == si:
            self.logger.debug(
                'interpolated "%s" to "%s" (type: %s)'
                % (
                    s,
                    si,
                    type(si),
                )
            )
        return si

    @property
    def sources(self):
        """returns the list of parsed yaml files / strings"""
        return self._sources

    def __str__(self):
        """String representation of the class"""
        return "%s [%s]" % (__name__, os.pathsep.join(self.sources))

    @property
    def data(self):
        """return the data, merged and interpolated if required"""
        return self._data

    def dump(self, **kwds):
        """dump the data as YAML"""
        return dump(self.data, Dumper=self.dumper_cls, **kwds)


def dump(data, **kwds):
    """dump the data as YAML"""
    return yaml.dump(data, sort_keys=False, **kwds)


def load(*args, **kwargs):
    hiyapyco = HieraConfigLoader(*args, **kwargs)
    return hiyapyco.data
