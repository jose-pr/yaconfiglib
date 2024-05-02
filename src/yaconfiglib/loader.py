import typing

from pathlib_next import Path, PosixPathname

try:
    from .utils import jinja2
except ImportError:
    ...

from .backends import ConfigBackend
from .utils.enum import IntEnum
from .utils.log import Logger, LogLevel, getLogger
from .utils.merge import Merge, MergeMethod, is_array
from .utils.source import SourceLike, parse_sources

__all__ = ["ConfigLoader", "ConfigLoaderMergeMethod"]


class _ConfigLoaderMergeMethod(IntEnum):
    Last = 4
    List = 5
    Hash = 6

    def init(
        self,
        initial: object,
        configloaderkey: str,
        logger: Logger,
        memo: dict = None,
        **options,
    ):
        match self:
            case ConfigLoaderMergeMethod.List:
                return [initial]
            case ConfigLoaderMergeMethod.Hash:
                return {configloaderkey: initial}
            case _:
                return initial

    def _last(
        self,
        a: object,
        b: object,
        *,
        configloaderkey: str,
        logger: Logger,
        memo: dict = None,
        **options,
    ):
        return b

    def _list(
        self,
        a: list,
        b: object,
        *,
        configloaderkey: str,
        logger: Logger,
        memo: dict = None,
        **options,
    ):
        a.append(b)
        return a

    def _hash(
        self,
        a: dict,
        b: object,
        *,
        configloaderkey: str,
        logger: Logger,
        memo: dict = None,
        **options,
    ):
        a[configloaderkey] = b
        return a


if typing.TYPE_CHECKING:

    class ConfigLoaderMergeMethod(
        _ConfigLoaderMergeMethod, MergeMethod, typing.Protocol
    ): ...

else:
    ConfigLoaderMergeMethod = MergeMethod.extend(
        _ConfigLoaderMergeMethod,
        name=_ConfigLoaderMergeMethod.__name__.removeprefix("_"),
    )


class _IgnoreError(typing.Protocol):
    def __call__(self, error: Exception, *args, **kwargs) -> bool: ...


class ConfigLoader(ConfigBackend):

    def __init__(
        self,
        base_dir: str | Path = "",
        *,
        encoding: str = None,
        path_factory: typing.Callable[[str], Path] = None,
        configloader_factory: type[ConfigBackend] = None,
        recursive: bool = None,
        key_factory: typing.Callable[[Path, object], str] = None,
        logger: int | LogLevel | Logger = LogLevel.Warning,
        interpolate: bool = None,
        merge: ConfigLoaderMergeMethod | Merge = ConfigLoaderMergeMethod.Simple,
        merge_options: dict[str] = None,
        ignore_error: _IgnoreError | bool = False,
    ) -> None:
        self.merge = (
            merge if isinstance(merge, Merge) else ConfigLoaderMergeMethod(merge)
        )
        self.merge_options = {} if merge_options is None else merge_options
        self.interpolate = False if interpolate is None else bool(interpolate)
        self.logger = getLogger(logger)
        self.path_factory = path_factory or self.DEFAULT_PATH_FACTORY
        self.base_dir = base_dir or ""
        self.encoding = encoding or self.DEFAULT_ENCODING
        self.recursive = False if recursive is None else recursive
        self.configloader_factory = configloader_factory or (
            lambda path: ConfigBackend.get_class_by_path(path)()
        )
        self.key_factory = key_factory or (lambda path, value: path.stem)
        self.ignore_error = (
            ignore_error
            if callable(ignore_error)
            else lambda error, *args, **kwargs: bool(ignore_error)
        )

    def _getpath(self, path: str | Path):
        return path if isinstance(path, Path) else self.path_factory(path)

    @property
    def base_dir(self):
        return self._base_dir

    @base_dir.setter
    def base_dir(self, value: str | Path):
        self._base_dir = self._getpath(value)

    def _load(
        self,
        path: Path,
        *,
        encoding: str,
        recursive: bool = None,
        configloader: str = None,
        transform: str = None,
        key_factory: str | typing.Callable[[Path], str] = None,
        interpolate: bool = None,
        **reader_args,
    ) -> tuple[str, object]:

        recursive = self.recursive if recursive is None else recursive

        if isinstance(configloader, str):
            configloader_factory = lambda path: ConfigBackend.get_class_by_name(
                configloader
            )(path)
        elif callable(getattr(configloader, "load", None)):
            configloader_factory = lambda path: configloader
        else:
            configloader_factory = configloader or self.configloader_factory

        if configloader is self:
            configloader_factory = self.configloader_factory

        key_factory = key_factory or self.key_factory
        if not callable(key_factory):
            if key_factory.startswith("%"):
                _eval = jinja2.eval(key_factory.removeprefix("%"))

                def _key(path: Path, value):
                    return _eval(value=value, pathname=PosixPathname(path.as_posix()))

            else:
                _keyname = key_factory

                def _key(path: Path, value):
                    val = getattr(path, _keyname)
                    if callable(val):
                        val = val()
                    return str(val)

            key_factory = _key
        self.logger.debug(f"Loading file: {path}")
        _configloader = configloader_factory(path)
        _options = dict(
            encoding=encoding,
            path_factory=self.path_factory,
            configloader=self,
            base_dir=self.base_dir,
            interpolate=(
                False if (configloader == self and self.interpolate) else interpolate
            ),
        )
        _options.update(reader_args)

        value = _configloader.load(path, **_options)
        if transform:
            value = jinja2.eval(transform)(
                value=value, pathname=PosixPathname(path.as_posix())
            )

        return key_factory(path, value), value

    def load(
        self,
        *pathname: SourceLike,
        recursive: bool = None,
        encoding: str = None,
        configloader: str = None,
        transform: str = None,
        default: object = None,
        key_factory: str | typing.Callable[[Path], str] = None,
        flatten: bool = False,
        interpolate: bool = False,
        merge: ConfigLoaderMergeMethod | Merge = None,
        merge_options: dict[str] = None,
        **reader_args,
    ):
        encoding = encoding or self.encoding
        interpolate = self.interpolate if interpolate is None else interpolate
        merge = (
            merge
            if isinstance(merge, Merge)
            else (ConfigLoaderMergeMethod(merge) if merge else self.merge)
        )
        if not merge:
            merge = self.merge
        self.merge_options = (
            self.merge_options if merge_options is None else merge_options
        )

        results = default
        _join_init = False

        for path in parse_sources(
            pathname,
            base_dir=self.base_dir,
            logger=self.logger,
            encoding=encoding,
            path_factory=self.path_factory,
        ):
            try:
                name, result = self._load(
                    path,
                    recursive=recursive,
                    encoding=encoding,
                    configloader=configloader,
                    transform=transform,
                    key_factory=key_factory,
                    **reader_args,
                )
                if _join_init:
                    results = merge(
                        results, result, logger=self.logger, configloaderkey=name
                    )
                else:
                    try:
                        results = merge.init(
                            initial=result, logger=self.logger, configloaderkey=name
                        )
                    except AttributeError:
                        results = result
                    _join_init = True
            except Exception as error:
                if self.ignore_error(error, path=path, configloader=self):
                    continue
                raise

        if flatten:
            if isinstance(results, typing.Mapping):
                result = {
                    prop: value
                    for _key, result in results.items()
                    for prop, value in result.items()
                }
            elif is_array(results):
                result = [r for result in results for r in result]
        else:
            result = results

        if interpolate:
            try:
                result = jinja2.interpolate(result, result, self.logger)
            except Exception as error:
                if not self.ignore_error(error, result=result, configloader=self):
                    raise

        return result

    def load_all(
        self,
        *pathname: Path | typing.Sequence[Path],
        encoding: str = None,
        interpolate: bool = None,
        **reader_args,
    ):
        interpolate = self.interpolate if interpolate is None else interpolate
        encoding = encoding or self.encoding
        for path in parse_sources(
            pathname,
            base_dir=self.base_dir,
            logger=self.logger,
            encoding=encoding,
            path_factory=self.path_factory,
        ):
            try:
                key, value = self._load(
                    path,
                    encoding=encoding,
                    **reader_args,
                )
                if interpolate:
                    value = jinja2.interpolate(value, value, self.logger)
                yield value

            except Exception as error:
                if not self.ignore_error(
                    error, path=path, value=value, configloader=self
                ):
                    raise


DEFAULT_LOADER = ConfigLoader()
