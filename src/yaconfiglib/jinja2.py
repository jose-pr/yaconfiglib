from io import IOBase
from pathlib_next import Path, PosixPathname
import re

from jinja2 import Template, Environment
from .reader import Reader
import io

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


class Jinja2Reader(Reader):
    PATHNAME_REGEX = re.compile(r".*\.((j2)|(jinja2))$", re.IGNORECASE)

    def __init__(
        self, path: Path, encoding: str, reader_factory: type[Reader] = None, **kwargs
    ) -> None:
        self.kwargs = kwargs
        self.reader_factory = reader_factory or (
            lambda path, **kwargs: Reader.get_class_by_path(path)(path, **kwargs)
        )
        super().__init__(path, encoding, **kwargs)

    def __call__(self):
        code = JINJA_ENV.compile(self.read_text())
        pathname = PosixPathname(self.path.as_posix())
        rendered = Template.from_code(
            JINJA_ENV, code, JINJA_ENV.make_globals(None)
        ).render(pathname=pathname)
        rendered = self.reader_factory(
            _MemoryPath(
                self.path.with_name(self.path.stem), rendered.encode(self.encoding)
            ),
            self.encoding,
            reader_factory=self.reader_factory,
            **self.kwargs,
        )()
        return rendered
