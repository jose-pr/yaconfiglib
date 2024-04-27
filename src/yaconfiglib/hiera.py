import io
import typing

from pathlib_next import Path

from yaconfiglib.jinja2 import jinja2_compile, jinja2_eval

from . import loader
from .utils import Logger, LogLevel, getLogger
from .utils.merge import Merge, MergeMethod

LOGGER = getLogger(LogLevel.Warning, __name__)


def interpolate(data: object, globals: dict = None, logger: Logger = None):
    globals = {} if globals is None else globals
    logger.debug(
        'interpolate "%s" of type %s ...'
        % (
            data,
            type(data),
        )
    )
    if isinstance(data, str):
        _template = data.removeprefix("{{").removesuffix("}}")
        if not "{{" in _template and _template != data:
            template = jinja2_eval(_template)
        else:
            template = jinja2_compile(data)

        _data = template(**globals)
        if not data == _data:
            logger.debug(
                'interpolated "%s" to "%s" (type: %s)'
                % (
                    data,
                    _data,
                    type(data),
                )
            )
        data = _data
    elif isinstance(data, typing.Mapping):
        if not isinstance(data, typing.MutableMapping):
            data = {**data}
        keys = list(data.keys())
        for key in keys:
            value = data.pop(key)
            key = interpolate(key, globals, logger)
            data[key] = interpolate(value, globals, logger)

    elif isinstance(data, typing.Iterable):
        if not isinstance(data, list):
            data = [*data]
        for idx, value in enumerate(data):
            data[idx] = interpolate(value, globals, logger)

    return data


class HieraConfigLoader:

    DEFAULT_ENCODING: str = "utf-8"

    def __init__(
        self,
        merge: MergeMethod | Merge = MergeMethod.Simple,
        merge_options: dict[str] = None,
        interpolate=False,
        backend: loader.ConfigBackend = None,
        encoding: str = None,
        logger: int | LogLevel | Logger = LogLevel.Warning,
        missingfiles_level: int | LogLevel = LogLevel.Error,
    ):
        self.merge = merge if isinstance(merge, Merge) else MergeMethod(merge)
        self.merge_options = {} if merge_options is None else merge_options
        self.interpolate = True if interpolate is None else bool(interpolate)
        self.backend = backend or loader.DEFAULT_LOADER
        self.missingfiles_level = LogLevel(missingfiles_level or LogLevel.Error)
        self.encoding = encoding or self.DEFAULT_ENCODING
        self.logger = getLogger(logger)

    def load(self, *sources: str | Path):
        data = None
        for source in self._validate_sources(sources):
            self.logger.debug("source: %s ..." % source)
            if not source:
                continue
            if "\n" in source:
                self.logger.debug("loading config doc from str ...")
                f = io.StringIO(source)
                data = self._load_data(f, data)
            else:
                path = Path(source) if not isinstance(source, Path) else source

                try:
                    with path.open("r", encoding=self.encoding) as f:
                        self.logger.debug("open4reading: file %s" % f)
                        data = self._load_data(f, data)
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
            data = interpolate(data, data, self.logger)

        return data

    def _load_data(self, f: str | io.IOBase, data: object):
        for ydata in self.backend.load_all(f):
            if self.logger.isEnabledFor(LogLevel.Debug):
                self.logger.debug("config data: %s" % ydata)
            if data is None:
                data = ydata
            else:
                data = self.merge(data, ydata, logger=self.logger, **self.merge_options)
                self.logger.debug("merged data: %s" % data)
        return data

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

    def dump(self, data: object, **kwds):
        """dump the data as string"""
        return self.backend.dumps(data, **kwds)


DEFAULT_LOADER = HieraConfigLoader()


def dump(*args, **kwds):
    """dump the data as config"""
    return DEFAULT_LOADER.dump(*args, **kwds)


def load(*args, **kwds):
    return DEFAULT_LOADER.load(*args, **kwds)
