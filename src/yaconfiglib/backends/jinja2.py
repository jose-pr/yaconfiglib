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
from yaconfiglib.utils.source import _materialize_temp

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
        # Name the rendered document after the template minus its .j2/.jinja2
        # suffix, so backend auto-detection resolves settings.yaml.j2 -> YAML.
        rendered_name = path.with_name(path.stem).as_posix()
        if MemPath is None:
            # Without pathlib_next this used to call MemPath(...) anyway —
            # TypeError: 'NoneType' object is not callable for any .j2 source —
            # despite this class's own docstring promising "a real temp file
            # when pathlib_next is unavailable". Reuse source.py's existing
            # temp-file materializer, which keeps the rendered basename as the
            # temp file's suffix so auto-detection still works.
            target = _materialize_temp(rendered, encoding, rendered_name)
        else:
            target = MemPath(rendered_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding=encoding)
        parent_loader = loader
        rendered_loader = ConfigBackend.get_class_by_path(target)()

        rendered = rendered_loader.load(
            target,
            encoding=encoding,
            loader=parent_loader,
            **kwargs,
        )
        return rendered
