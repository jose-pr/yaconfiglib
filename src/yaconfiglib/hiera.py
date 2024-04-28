import typing

from pathlib_next import Path
from pathlib_next.mempath import MemPath

from . import loader
from .backends import ConfigBackend
from .utils import Logger, LogLevel, getLogger
from .utils.merge import Merge, MergeMethod

LOGGER = getLogger(LogLevel.Warning, __name__)

__all__ = ["HieraConfigLoader"]


class HieraConfigLoader(ConfigBackend):

    DEFAULT_ENCODING: str = "utf-8"

    def __init__(
        self,
        merge: MergeMethod | Merge = MergeMethod.Simple,
        merge_options: dict[str] = None,
        interpolate=False,
        backend: ConfigBackend = None,
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
        config_backend: ConfigBackend = None,
        interpolate: bool = None,
        **backend_args,
    ):
        data = None
        backend = config_backend or self.backend
        interpolate = self.interpolate if interpolate is None else interpolate

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
        if interpolate:
            from yaconfiglib.utils import jinja2

            return jinja2.interpolate(data, data, self.logger)
        return data

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

    def dump(self, data: object, *, config_backend: ConfigBackend = None, **kwds):
        """dump the data as string"""
        backend = config_backend or self.backend
        return backend.dumps(data, **kwds)


DEFAULT_LOADER = HieraConfigLoader()


def dump(*args, **kwds):
    """dump the data as config"""
    return DEFAULT_LOADER.dump(*args, **kwds)


def load(*args, **kwds):
    return DEFAULT_LOADER.load(*args, **kwds)
