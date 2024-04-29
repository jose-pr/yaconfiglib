import re

from jinja2 import Environment
from pathlib_next import Path, PosixPathname
from pathlib_next.mempath import MemPath

from yaconfiglib.backends.base import ConfigBackend
from yaconfiglib.utils import jinja2

__all__ = ["Jinja2ConfigLoader"]


class Jinja2ConfigLoader(ConfigBackend):
    PATHNAME_REGEX = re.compile(r".*\.((j2)|(jinja2))$", re.IGNORECASE)

    def load(
        self,
        path: Path,
        encoding: str = None,
        configloader: ConfigBackend = None,
        envoriment: Environment = None,
        **kwargs,
    ) -> None:
        encoding = encoding or self.DEFAULT_ENCODING
        template = jinja2.load_template(
            path.read_text(encoding=encoding),
            environment=envoriment or jinja2.DEFAULT_ENV,
        )
        pathname = PosixPathname(path.as_posix())
        rendered = template.render(pathname=pathname)
        mempath = MemPath(
            path.with_name(path.stem).as_posix(),
        )
        mempath.parent.mkdir(parents=True, exist_ok=True)
        mempath.write_text(rendered, encoding=encoding)
        configloader = configloader or ConfigBackend.get_class_by_path(mempath)

        rendered = configloader.load(
            mempath,
            encoding=encoding,
            configloader=configloader,
            **kwargs,
        )
        return rendered
