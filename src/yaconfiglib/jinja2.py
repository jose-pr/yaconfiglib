import io
import re
import typing
from io import IOBase

from jinja2 import Environment, Template
from pathlib_next import Path, PosixPathname

from .reader import Reader

__all__ = ["Jinja2Reader"]

JINJA_ENV = Environment(extensions=["jinja2.ext.do"])


class _MemoryPath(Path):
    def __init__(self, path: PosixPathname, content: bytes) -> None:
        self.path = path
        self.content = content

    def _open(self, mode="r", buffering=-1) -> IOBase:
        return io.BytesIO(self.content)

    def as_uri(self) -> str:
        return f"memmory:{self.path.as_posix()}"

    @property
    def parent(self):
        return self.path.parent

    @property
    def parts(self):
        return (self.path, self.content)

    def relative_to(self, other):
        raise NotImplementedError()

    @property
    def segments(self):
        return self.path.segments

    def with_segments(self, *segments: str):
        raise NotImplementedError()


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
        rendered = self.reader_factory(
            _MemoryPath(
                self.path.with_name(self.path.stem), rendered.encode(self.encoding)
            ),
            encoding=self.encoding,
            reader_factory=self.reader_factory,
            **self.kwargs,
        )()
        return rendered
