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
    """Backend for ``*.j2``/``*.jinja2`` template sources.

    Renders the file as a Jinja2 template first, then dispatches the
    rendered text to whichever backend matches the *un-templated* filename
    (e.g. ``settings.yaml.j2`` renders through Jinja2 and is then parsed as
    YAML). This lets any existing format be templated by simply appending
    a ``.j2``/``.jinja2`` suffix, without needing a dedicated templated
    variant of each backend.

    The rendered output is written to an in-memory path (``MemPath``, or a
    real temp file when ``pathlib_next`` is unavailable) before being
    handed to the resolved backend, so downstream backends see ordinary
    file content and don't need any Jinja2-specific handling.
    """

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
        """Render *path* as a Jinja2 template, then load the result with the matching backend.

        Args:
            path: Path to the ``.j2``/``.jinja2`` template file.
            encoding: Text encoding for reading the template and writing
                the rendered output. Defaults to :attr:`DEFAULT_ENCODING`.
            loader: The parent :class:`~yaconfiglib.loader.ConfigLoader`,
                forwarded to the resolved backend so nested
                ``!include``/``!load`` directives keep working.
            environment: A :class:`jinja2.Environment` to render with.
                Defaults to :data:`yaconfiglib.utils.jinja2.DEFAULT_ENV`.
                The legacy keyword ``envoriment`` (a historical typo) is
                still accepted as a fallback for backward compatibility —
                prefer ``environment``.

        Returns:
            The parsed object produced by the backend matching the
            rendered filename (with the ``.j2``/``.jinja2`` suffix
            stripped).
        """
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
