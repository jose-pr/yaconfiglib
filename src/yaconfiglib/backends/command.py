"""Backend for executing commands/scripts and parsing output."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import typing

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

from ..utils.trust import CommandsDisabledError, current_policy
from .base import ConfigBackend

__all__ = ["CommandBackend"]


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Kill a shell=True process and everything it started.

    Killing only the shell is not enough: a grandchild keeps the output pipes
    open, so the read that follows would still wait for it to finish.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            # The command runs in its own session, so its pid is the group id.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _run_command(command: str, encoding: str, timeout: typing.Optional[float]) -> str:
    """Run *command* through the shell with stdin closed; return its stdout.

    Raises CalledProcessError on a non-zero exit and TimeoutExpired when
    *timeout* elapses (after killing the whole process tree).
    """
    with subprocess.Popen(
        command,
        shell=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Decode explicitly: the locale codec (cp1252 on Windows) mangles UTF-8
        # output from tools like secret managers. errors="replace" keeps the
        # format-sniffing path total instead of raising mid-decode.
        encoding=encoding,
        errors="replace",
        start_new_session=(os.name != "nt"),
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            process.communicate()
            raise subprocess.TimeoutExpired(command, timeout) from None
        except BaseException:
            # Interrupted (e.g. KeyboardInterrupt): never leave the command
            # running. Re-raised unchanged, as subprocess.run does.
            process.kill()
            raise
    if process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode, command, output=stdout, stderr=stderr
        )
    return stdout


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
        re.IGNORECASE,
    )
    NAME = "command"

    @classmethod
    def can_load_path(cls, path: Path) -> bool:
        """Return True if *path* matches a command scheme prefix or script extension."""
        path_str = str(path)
        return cls.PATHNAME_REGEX.match(path_str) is not None or (
            cls.PATHNAME_REGEX.match(path.name) is not None
            if cls.PATHNAME_REGEX
            else False
        )

    def load(
        self,
        path: Path | str,
        encoding: str = None,
        format: str | list[str] = None,
        path_factory: typing.Callable[[str], Path] = None,
        timeout: typing.Optional[float] = None,
        **options,
    ) -> object:
        """Run the command encoded in *path* and parse its stdout.

        Args:
            path: A scheme-prefixed command string (e.g.
                ``"cmd+json://echo {}"``), a bare shell command, or a
                script file path.
            encoding: Codec used to decode the command's stdout/stderr
                (default ``utf-8``; previously the locale codec, which
                mangled UTF-8 output on Windows).
            format: Explicit output format, or a comma-separated/list of
                candidate formats to try in order. Overrides shebang
                detection and sniffing.
            path_factory: Unused; accepted for interface consistency.
            timeout: Seconds to wait for the command before killing it and
                its child processes. ``None`` (the default) waits
                indefinitely. Reachable per call, e.g.
                ``loader.load("cmd://...", timeout=30)``.

        Returns:
            The parsed stdout, or the raw stripped stdout string if no
            format could be determined and sniffing failed.

        Raises:
            subprocess.CalledProcessError: If the command exits non-zero.
            subprocess.TimeoutExpired: If *timeout* elapses first.
            ValueError: If an explicit *format*/shebang format is
                requested but the output cannot be parsed as that format,
                or output is empty while a format was requested.
        """
        path_str = str(path)
        explicit_format = format

        # 1. Parse inline command schemes using regex to handle normalized slashes
        m = re.match(
            r"^(exec|cmd|sh|exec\+\w+|cmd\+\w+)(://|:\\|:/|:)", path_str, re.IGNORECASE
        )
        if m:
            scheme = m.group(1)
            command = path_str[m.end() :]
            if "+" in scheme:
                _, scheme_fmt = scheme.split("+", 1)
                if not explicit_format:
                    explicit_format = scheme_fmt
        else:
            command = path_str

        # Backstop for every route that reaches this backend without passing
        # ConfigLoader._load's gate (e.g. a rendered .j2 dispatch or a wrapper
        # backend). Outside a load the policy allows, so a directly constructed
        # CommandBackend keeps working.
        if not current_policy()[0]:
            raise CommandsDisabledError(
                f"refusing to run command source {path_str!r}: allow_commands=False"
            )

        # 2. Execute command (stdin closed, so a command can neither hang the
        # load waiting for input nor consume the parent's stdin).
        stdout = _run_command(command, encoding or "utf-8", timeout)
        output = stdout.strip()

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

        # Strip loader/format to avoid infinite recursion, and origin because the
        # command's output is not a file next to it (ConfigLoader has no such
        # parameter, so passing it through would raise TypeError).
        loads_options = {
            k: v for k, v in options.items() if k not in ("loader", "format", "origin")
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
            except (
                Exception
            ):  # noqa: BLE001 - format sniffing must survive ANY parse error
                # If explicit_format or shebang_format failed and is the only candidate,
                # we want to propagate the error. Otherwise, continue.
                if len(candidates) == 1 and (explicit_format or shebang_format):
                    raise
                continue

        if explicit_format or shebang_format:
            raise ValueError(f"Failed to parse command output as {candidates}")

        # Sniffing fallback: return the raw output if no candidate parses successfully
        return output
