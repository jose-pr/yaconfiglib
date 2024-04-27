import io
import re
import typing
from io import IOBase

from jinja2 import Environment, Template
from pathlib_next import Path, PosixPathname

from .reader import Reader
from .utils.mempath import MemPath

__all__ = ["Jinja2Reader"]

JINJA_ENV = Environment(extensions=["jinja2.ext.do"])


def _load_from_text(
    source: str,
    name: str = None,
    filename: str = None,
    environment: Environment = None,
    globals: typing.MutableMapping = None,
):
    environment = environment or JINJA_ENV
    code = environment.compile(source, name, filename)
    return Template.from_code(environment, code, environment.make_globals(globals))


def jinja2_compile(
    code: str, environment: Environment = None, globals: typing.MutableMapping = None
):
    return _load_from_text(code, environment=environment, globals=globals).render


def jinja2_eval(
    code: str,
    environment: Environment = None,
    globals: typing.MutableMapping = None,
):
    template = _load_from_text(
        "{% do _meta.__setitem__('result', " + code + ") %}",
        environment=environment,
        globals=globals,
    )

    def eval_(**kwargs):
        _meta = {}
        template.render(_meta=_meta, **kwargs)
        return _meta["result"]

    return eval_


class Jinja2Reader(Reader):
    PATHNAME_REGEX = re.compile(r".*\.((j2)|(jinja2))$", re.IGNORECASE)

    def __init__(
        self, path: Path, encoding: str, reader_factory: type[Reader] = None, **kwargs
    ) -> None:
        self.kwargs = kwargs
        self.reader_factory = reader_factory or Reader
        super().__init__(path, encoding, **kwargs)

    def __call__(self):
        template = _load_from_text(self.read_text(), environment=JINJA_ENV)
        pathname = PosixPathname(self.path.as_posix())
        rendered = template.render(pathname=pathname)
        mempath = MemPath(
            self.path.stem,
        )
        mempath.write_text(rendered)
        rendered = self.reader_factory(
            mempath,
            encoding=self.encoding,
            reader_factory=self.reader_factory,
            **self.kwargs,
        )()
        return rendered
