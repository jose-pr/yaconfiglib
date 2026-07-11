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
    """Executes a script/command and parses stdout into a configuration object."""

    PATHNAME_REGEX = re.compile(
        r"^(exec|cmd|sh|exec\+\w+|cmd\+\w+)(://|:\\|:/|:).*|.*?\.(sh|bat|ps1|cmd)$",
        re.IGNORECASE
    )
    NAME = "command"

    @classmethod
    def can_load_path(cls, path: Path) -> bool:
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
