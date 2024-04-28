import io
import typing

from pathlib_next import Path
from pathlib_next.mempath import MemPath

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
        if not isinstance(data, typing.MutableSequence):
            data = [*data]
        for idx, value in enumerate(data):
            data[idx] = interpolate(value, globals, logger)

    return data


_interpolate = interpolate


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

    def load(
        self,
        *sources: str | Path,
        config_backend: loader.ConfigBackend = None,
        interpolate: bool = None,
        **backend_args,
    ):
        data = None
        backend = config_backend or self.backend
        interpolate = self.interpolate if interpolate is None else interpolate
        if interpolate:
            interpolate = _interpolate

        for source in self._validate_sources(sources):
            try:
                self.logger.debug("source: %s ..." % source)
                for ydata in backend.load_all(source, **backend_args):
                    if self.logger.isEnabledFor(LogLevel.Debug):
                        self.logger.debug("config data: %s" % ydata)
                    if data is None:
                        data = ydata
                    else:
                        data = self.merge(
                            data, ydata, logger=self.logger, **self.merge_options
                        )
                        self.logger.debug("merged data: %s" % data)
            except IOError as e:
                if self.missingfiles_level >= LogLevel.Error:
                    self.logger.log(self.missingfiles_level, e)
                    self.logger.log(
                        self.missingfiles_level,
                        "file not found: %s" % source,
                    )
                    raise FileNotFoundError(source)
                self.logger.log(self.missingfiles_level, e)
                continue

        return interpolate(data, data, self.logger) if interpolate else data

    def _validate_sources(
        self,
        sources: typing.Sequence[str | Path | typing.Sequence[str | Path]],
        *,
        memo: list[str | Path] = None,
    ) -> typing.Iterator[Path]:
        if memo is None:
            memo = []
        for source in sources:
            if not source:
                continue
            if isinstance(source, (str, Path)):
                if source in memo:
                    self.logger.warning("ignoring duplicated file %s" % source)
                    continue
                if source.startswith("#!"):
                    filename, source = source.split("\n", maxsplit=1)
                    self.logger.debug("loading config doc from str ...")
                    filename = filename.removeprefix("#!")
                    path = MemPath(filename)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding=self.encoding)
                else:
                    path = Path(source) if not isinstance(source, Path) else source
                yield path
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

    def dump(
        self, data: object, *, config_backend: loader.ConfigBackend = None, **kwds
    ):
        """dump the data as string"""
        backend = config_backend or self.backend
        return backend.dumps(data, **kwds)


DEFAULT_LOADER = HieraConfigLoader()


def dump(*args, **kwds):
    """dump the data as config"""
    return DEFAULT_LOADER.dump(*args, **kwds)


def load(*args, **kwds):
    return DEFAULT_LOADER.load(*args, **kwds)
