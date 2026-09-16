from __future__ import annotations

import os
import re
import types

from jinja2 import Environment
from jinja2.sandbox import SandboxedEnvironment

try:
    from pathlib_next import Path, PosixPathname
    from pathlib_next.mempath import MemPath
except ImportError:
    from pathlib import Path
    from pathlib import PurePosixPath as PosixPathname  # type: ignore[no-redef]

    MemPath = None  # type: ignore[assignment,misc]

from yaconfiglib.backends.base import ConfigBackend
from yaconfiglib.backends.command import CommandBackend
from yaconfiglib.utils import jinja2
from yaconfiglib.utils.source import _materialize_temp
from yaconfiglib.utils.trust import CommandsDisabledError, current_policy, is_hardened

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
                ``!include``/``!load`` directives keep working. The template's
                own path is passed as the resolved document's ``origin``, so
                relative includes resolve next to the template rather than
                next to the rendered copy.
            environment: A :class:`jinja2.Environment` to render with.
                Defaults to :data:`yaconfiglib.utils.jinja2.DEFAULT_ENV`, or
                to the shared sandboxed/strict environment when the load's
                ``sandbox=True``, ``allow_commands=False`` or ``strict=True``
                is in effect. Under ``sandbox=True`` or
                ``allow_commands=False`` it must be a
                :class:`jinja2.sandbox.SandboxedEnvironment`. The legacy
                keyword ``envoriment`` (a historical typo) is still accepted
                as a fallback — prefer ``environment``.

        Returns:
            The parsed object produced by the backend matching the
            rendered filename (with the ``.j2``/``.jinja2`` suffix
            stripped).

        Raises:
            ValueError: If a non-sandboxed *environment* is supplied while
                ``sandbox=True`` or ``allow_commands=False`` is in effect.
            CommandsDisabledError: If the rendered document is a command
                source while ``allow_commands=False`` is in effect.
        """
        encoding = encoding or self.DEFAULT_ENCODING
        # A .j2 body is template code, so it follows the load's effective trust
        # policy: sandboxed whenever commands are disabled or the sandbox is on,
        # and strict whenever strict is in effect.
        policy = current_policy()
        hardened = is_hardened(policy)
        strict = policy[2]
        environment = environment or kwargs.pop("envoriment", None)
        if environment is None:
            if hardened or strict:
                environment = jinja2.get_environment(strict, hardened)
            else:
                environment = jinja2.DEFAULT_ENV
        elif hardened and not isinstance(environment, SandboxedEnvironment):
            raise ValueError(
                f"refusing to render {path.as_posix()!r} with a non-sandboxed "
                "environment: sandbox=True or allow_commands=False is in effect"
            )
        template = jinja2.load_template(
            path.read_text(encoding=encoding),
            environment=environment,
        )
        context = {"pathname": PosixPathname(path.as_posix())}
        if getattr(loader, "inject_env", False):
            # A read-only snapshot: a template must not be able to change the
            # process environment.
            context["env"] = types.MappingProxyType(dict(os.environ))
        rendered = template.render(**context)
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
        if isinstance(rendered_loader, CommandBackend) and not policy[0]:
            raise CommandsDisabledError(
                f"refusing to run rendered command source {str(target)!r} "
                f"from {path.as_posix()!r}: allow_commands=False"
            )

        # The rendered document lives in memory or in a temp file, so relative
        # includes inside it resolve next to the template they came from.
        kwargs.setdefault("origin", path)
        rendered = rendered_loader.load(
            target,
            encoding=encoding,
            loader=parent_loader,
            **kwargs,
        )
        return rendered
