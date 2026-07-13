from __future__ import annotations

import re

from jinja2 import Environment
try:
    from pathlib_next import Path, PosixPathname
    from pathlib_next.mempath import MemPath
except ImportError:
    from pathlib import Path
    from pathlib import PurePosixPath as PosixPathname  # type: ignore[no-redef]
    MemPath = None  # type: ignore[assignment,misc]

from yaconfiglib.backends.base import ConfigBackend
from yaconfiglib.utils import jinja2

__all__ = ["Jinja2ConfigLoader"]


class Jinja2ConfigLoader(ConfigBackend):
    PATHNAME_REGEX = re.compile(r".*\.((j2)|(jinja2))$", re.IGNORECASE)
    NAME = "jinja2"

    def load(
        self,
        path: Path,
        encoding: str = None,
        loader: ConfigBackend = None,
        environment: Environment = None,
        **kwargs,
    ) -> None:
        encoding = encoding or self.DEFAULT_ENCODING
        environment = environment or kwargs.pop("envoriment", None)
        template = jinja2.load_template(
            path.read_text(encoding=encoding),
            environment=environment or jinja2.DEFAULT_ENV,
        )
        pathname = PosixPathname(path.as_posix())
        rendered = template.render(pathname=pathname)
        mempath = MemPath(
            path.with_name(path.stem).as_posix(),
        )
        mempath.parent.mkdir(parents=True, exist_ok=True)
        mempath.write_text(rendered, encoding=encoding)
        parent_loader = loader
        rendered_loader = ConfigBackend.get_class_by_path(mempath)()

        rendered = rendered_loader.load(
            mempath,
            encoding=encoding,
            loader=parent_loader,
            **kwargs,
        )
        return rendered
