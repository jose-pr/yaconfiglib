"""Backend for executing commands/scripts and parsing output."""

from __future__ import annotations

import re
import subprocess
import typing

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

from .base import ConfigBackend

__all__ = ["CommandBackend"]


class CommandBackend(ConfigBackend):
    """Executes a script/command and parses stdout into a configuration object.

    Sources are recognized either by a URI scheme prefix (``exec://``,
    ``cmd://``, ``sh://``, or a format-tagged variant like ``cmd+json://``)
    or by a ``.sh``/``.bat``/``.ps1``/``.cmd`` file extension. The command
    is run through the shell and its stdout is parsed as configuration
    data — this makes it easy to source secrets or dynamic values from
    external tools, e.g. ``cmd+json://aws secretsmanager get-secret-value ...``.

    Output format resolution, in priority order:

    1. An explicit ``format=`` argument.
    2. The ``+fmt`` suffix on the scheme (e.g. ``cmd+yaml://...``).
    3. A ``#!fmt`` shebang line at the start of the command's stdout.
    4. Sniffing: try json, yaml, toml, dotenv, ini in turn.

    If parsing fails and no format was requested, the raw stdout string is
    returned as a fallback rather than raising.
    """

    PATHNAME_REGEX = re.compile(
        r"^(exec|cmd|sh|exec\+\w+|cmd\+\w+)(://|:\\|:/|:).*|.*?\.(sh|bat|ps1|cmd)$",
        re.IGNORECASE
    )
    NAME = "command"

    @classmethod
    def can_load_path(cls, path: Path) -> bool:
        """Return True if *path* matches a command scheme prefix or script extension."""
        path_str = str(path)
        return (
            cls.PATHNAME_REGEX.match(path_str) is not None or
            (cls.PATHNAME_REGEX.match(path.name) is not None if cls.PATHNAME_REGEX else False)
        )

    def load(
        self,
        path: Path | str,
        encoding: str = None,
        format: str | list[str] = None,
        path_factory: typing.Callable[[str], Path] = None,
        **options,
    ) -> object:
        """Run the command encoded in *path* and parse its stdout.

        Args:
            path: A scheme-prefixed command string (e.g.
                ``"cmd+json://echo {}"``), a bare shell command, or a
                script file path.
            encoding: Unused directly (subprocess output is captured as
                text); accepted for interface consistency with other
                backends.
            format: Explicit output format, or a comma-separated/list of
                candidate formats to try in order. Overrides shebang
                detection and sniffing.
            path_factory: Unused; accepted for interface consistency.

        Returns:
            The parsed stdout, or the raw stripped stdout string if no
            format could be determined and sniffing failed.

        Raises:
            subprocess.CalledProcessError: If the command exits non-zero.
            ValueError: If an explicit *format*/shebang format is
                requested but the output cannot be parsed as that format,
                or output is empty while a format was requested.
        """
        path_str = str(path)
        explicit_format = format

        # 1. Parse inline command schemes using regex to handle normalized slashes
        m = re.match(
            r"^(exec|cmd|sh|exec\+\w+|cmd\+\w+)(://|:\\|:/|:)",
            path_str,
            re.IGNORECASE
        )
        if m:
            scheme = m.group(1)
            command = path_str[m.end():]
            if "+" in scheme:
                _, scheme_fmt = scheme.split("+", 1)
                if not explicit_format:
                    explicit_format = scheme_fmt
        else:
            command = path_str

        # 2. Execute command
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )
        output = result.stdout.strip()

        # 3. Parse shebang from output if present
        shebang_format = None
        if output.startswith("#!"):
            lines = output.split("\n", 1)
            first_line = lines[0].strip()
            shebang_cmd = first_line[2:].strip()
            if shebang_cmd:
                parts = shebang_cmd.split()
                last_part = parts[-1] if parts else ""
                shebang_format = (
                    last_part.split("/")[-1].split("\\")[-1].lower().lstrip(".")
                )
            output = lines[1] if len(lines) > 1 else ""

        if not output:
            if explicit_format or shebang_format:
                raise ValueError(
                    f"Command output is empty, cannot parse as {explicit_format or shebang_format}"
                )
            return ""

        # 4. Parse content using yaconfiglib.loads
        from yaconfiglib import loads

        # Strip loader/format argument to avoid infinite recursion
        loads_options = {
            k: v for k, v in options.items() if k not in ("loader", "format")
        }

        candidates = []
        if explicit_format:
            if isinstance(explicit_format, str):
                candidates = [f.strip() for f in explicit_format.split(",")]
            else:
                candidates = list(explicit_format)
        elif shebang_format:
            candidates = [shebang_format]
        else:
            candidates = ["json", "yaml", "toml", "dotenv", "ini"]

        for fmt in candidates:
            if fmt == "command":
                continue
            try:
                return loads(output, loader=fmt, **loads_options)
            except Exception:
                # If explicit_format or shebang_format failed and is the only candidate,
                # we want to propagate the error. Otherwise, continue.
                if len(candidates) == 1 and (explicit_format or shebang_format):
                    raise
                continue

        if explicit_format or shebang_format:
            raise ValueError(f"Failed to parse command output as {candidates}")

        # Sniffing fallback: return the raw output if no candidate parses successfully
        return output
