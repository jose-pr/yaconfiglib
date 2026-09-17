"""Backend for executing commands/scripts and parsing output."""

from __future__ import annotations

import logging
import os
import os as _os
import re
import shutil
import signal
import subprocess
import sys
import typing
import typing as _ty
from collections.abc import Mapping

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

import configparser

from ..errors import (
    CommandError,
    CommandsDisabledError,
    CommandTimeoutError,
    ConfigValueError,
    ErrorFrame,
    UnknownLoaderError,
    _add_error_context,
    _stderr_tail,
)
from ..utils import source as _source
from ..utils.source import _CMD_REGEX, CommandSource
from ..utils.trust import current_policy
from .base import ConfigBackend
from .dotenv import DotenvBackend

__all__ = ["CommandBackend"]

logger = logging.getLogger(__name__)


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


_SCRIPT_SUFFIX_REGEX = re.compile(r"\.(sh|bat|ps1|cmd)$", re.IGNORECASE)


def _script_command(path_str: str) -> "typing.Union[str, list[str]]":
    """How to launch the script at *path_str*, without a shell.

    Returns either an argv list, or — for `.bat`/`.cmd` — a full command line
    string whose program is COMSPEC. Either way the caller uses
    ``shell=False``, so the file's own name is never parsed as shell syntax:
    a script called ``x&copy nul MARK&.bat`` runs instead of injecting.

    The path is made absolute first. A relative path would otherwise be
    searched on PATH (POSIX), be refused when
    ``NoDefaultCurrentDirectoryInExePath`` is set (Windows), or — if it began
    with ``-`` — be read as an interpreter option.
    """
    path_str = os.path.abspath(path_str)
    suffix = os.path.splitext(path_str)[1].lower()

    if suffix in (".bat", ".cmd"):
        if sys.platform != "win32":
            raise ConfigValueError(
                f"{path_str!r} is a Windows batch file and cannot run on this platform"
            )
        if "%" in path_str:
            # cmd expands %VAR% even inside quotes, so a path containing % can
            # rewrite the command line. There is no escape that survives it.
            raise ConfigValueError(
                f"refusing to run {path_str!r}: a batch file's path cannot contain '%'"
            )
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        # /d skips AutoRun, /v:off disables delayed expansion, /s makes the
        # outer quotes wrap the whole command rather than being stripped.
        return f'"{comspec}" /d /v:off /s /c ""{path_str}""'

    if suffix == ".ps1":
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if not exe:
            raise FileNotFoundError(
                f"cannot run {path_str!r}: neither pwsh nor powershell is on PATH"
            )
        # No -ExecutionPolicy: a locked-down host should still refuse.
        return [exe, "-NoProfile", "-NonInteractive", "-File", path_str]

    if suffix == ".sh":
        if sys.platform == "win32":
            shell = shutil.which("sh")
            if not shell:
                raise FileNotFoundError(f"cannot run {path_str!r}: no 'sh' on PATH")
            return [shell, path_str]
        # Direct execution only when the file really is executable (os.access
        # also reports a noexec mount) and carries a #! line; otherwise hand it
        # to /bin/sh, so a 0644 script still runs.
        if os.access(path_str, os.X_OK):
            try:
                with open(path_str, "rb") as handle:
                    if handle.read(2) == b"#!":
                        return [path_str]
            except OSError:
                pass
        return ["/bin/sh", path_str]

    # An explicit loader="command" on some other file: run it directly.
    return [path_str]


def _decode(raw: bytes, codec: str, *, strict: bool) -> str:
    """Decode command output, then apply universal newlines.

    `strict=False` replaces undecodable bytes; it is used for the output of a
    command that already failed, where the text is only a diagnostic.
    """
    text = raw.decode(codec, errors="strict" if strict else "replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _run_command(
    command: "typing.Union[str, list[str]]",
    encoding: str,
    timeout: typing.Optional[float],
    shell: bool = True,
) -> bytes:
    """Run *command* with stdin closed; return its stdout as raw bytes.

    *shell* is True for a command URI or a bare command string (the documented
    behaviour) and False for a script file, whose launch line is built by
    `_script_command`.

    Raises `CommandError` on a non-zero exit and `CommandTimeoutError` when
    *timeout* elapses (after killing the whole process tree). Both are still a
    `subprocess.CalledProcessError` / `TimeoutExpired`.

    stderr from a **successful** run is logged at DEBUG rather than dropped: a
    tool's warnings used to vanish entirely. It is decoded non-strictly, since
    a warning in another codec must not fail a load whose stdout decoded.
    """
    with subprocess.Popen(
        command,
        shell=shell,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Binary: decoding happens in the caller, which needs a real
        # UnicodeDecodeError to catch. Popen(encoding=..., errors="replace")
        # would substitute U+FFFD before anything here could object.
        start_new_session=(os.name != "nt"),
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            process.communicate()
            raise CommandTimeoutError(command, timeout) from None
        except BaseException:
            # Interrupted (e.g. KeyboardInterrupt): never leave the command
            # running. Re-raised unchanged, as subprocess.run does.
            process.kill()
            raise
    if process.returncode:
        # A failing command's status wins over any decode complaint, and its
        # output is a diagnostic rather than configuration, so replace freely.
        raise CommandError(
            process.returncode,
            command,
            output=_decode(stdout, encoding, strict=False),
            stderr=_decode(stderr, encoding, strict=False),
        )
    if stderr and stderr.strip():
        # A successful command that warned still said something worth seeing.
        logger.debug(
            "command %r wrote to stderr:%s",
            command,
            _stderr_tail(_decode(stderr, encoding, strict=False)),
        )
    return stdout


#: What "this candidate format does not fit" actually looks like. Measured, not
#: guessed: every candidate format was run over INI, dotenv, prose, a bare
#: number, YAML, TOML, tabs, PEM and control characters, and each failure was a
#: `ValueError` subclass, a `configparser.Error` or a `yaml.YAMLError`.
#: `RecursionError` is here because output nested thousands deep exhausts a
#: parser rather than being the wrong format.
_PARSE_FAILURES = (ValueError, configparser.Error, RecursionError)


def _parse_failure_types() -> tuple:
    """`_PARSE_FAILURES`, plus PyYAML's base error when PyYAML is imported.

    Probed in `sys.modules` rather than imported: yaml can only have raised if
    something already imported it.
    """
    yaml = sys.modules.get("yaml")
    yaml_error = getattr(yaml, "YAMLError", None)
    if isinstance(yaml_error, type):
        return _PARSE_FAILURES + (yaml_error,)
    return _PARSE_FAILURES


def _is_wrong_format(error: BaseException, sniffing: bool) -> bool:
    """Whether a caught parse failure really means "try the next format".

    Sniffing used to swallow every exception, so a missing `!include` inside a
    command's output became an empty mapping and a disabled command looked like
    unparseable text. These three are parse-failure *types* that are not the
    candidate's own verdict.
    """
    if isinstance(error, CommandsDisabledError):
        return False
    for frame in getattr(error, "config_frames", ()) or ():
        if frame.kind == "include":
            # Raised by a document the output included, not by parsing it.
            return False
    if isinstance(error, UnknownLoaderError):
        # While sniffing, a format whose backend is not installed is simply not
        # a candidate. Asked for by name, it is an error.
        return sniffing
    return True


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
    4. Sniffing: json (any JSON value), yaml (only when the result is a
       mapping or a list), toml, dotenv (strict, so only when every
       non-comment line is an assignment), then ini.

    If no candidate accepts the output and no format was requested, the raw
    stdout string is returned as a fallback rather than raising. The yaml
    restriction is what makes the later candidates reachable at all: YAML
    reads arbitrary text as a scalar, so it used to accept INI output as one
    string. Pass ``cmd+yaml://`` to force a YAML scalar.
    """

    # Filenames only. A command URI is recognized by its own type
    # (CommandSource) or, for a direct caller, by _CMD_REGEX in can_load_path —
    # never by matching a scheme against a path's text, which is how a data file
    # named "sh:hosts.json" used to be run as a program.
    PATHNAME_REGEX = re.compile(r".*\.(sh|bat|ps1|cmd)$", re.IGNORECASE)
    NAME = "command"

    @classmethod
    def can_load_path(cls, path) -> bool:
        """Return True for a command source, or a path named like a script.

        Must not raise for any path type: backend selection probes every
        registered backend with arbitrary paths, including pure ones.
        """
        if isinstance(path, CommandSource):
            return True
        if isinstance(path, str):
            # A direct caller may still pass the text itself.
            return bool(_CMD_REGEX.match(path)) or bool(cls.PATHNAME_REGEX.match(path))
        return cls.PATHNAME_REGEX.match(path.name) is not None

    def load(
        self,
        path: "_ty.Union[str, _os.PathLike]",
        encoding: _ty.Optional[str] = None,
        format: "_ty.Optional[_ty.Union[str, _ty.Sequence[str]]]" = None,
        path_factory: "_ty.Optional[_ty.Callable[[str], _os.PathLike]]" = None,
        timeout: typing.Optional[float] = None,
        **options: _ty.Any,
    ) -> _ty.Any:
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
            CommandError: If the command exits non-zero. Also a
                `subprocess.CalledProcessError`; its message ends with the tail
                of the command's stderr.
            CommandTimeoutError: If *timeout* elapses first. Also a
                `subprocess.TimeoutExpired`.
            ConfigValueError: If the output cannot be decoded, is empty when a
                format was requested, or matches none of several requested
                formats (chained to the last parser error). Also a `ValueError`.
            Exception: Anything an `!include` inside the output raises, and
                anything that is not a parse failure, propagates with its own
                type rather than being treated as "wrong format".
            ValueError: If an explicit *format*/shebang format is
                requested but the output cannot be parsed as that format,
                or output is empty while a format was requested.
        """
        path_str = str(path)
        explicit_format = format

        # 1. A command source carries its own text and scheme. A bare string is
        # wrapped when it names a scheme; anything else runs as written, which
        # is the documented "bare shell command" form.
        source = path if isinstance(path, CommandSource) else None
        if source is None and isinstance(path, str) and _CMD_REGEX.match(path):
            source = CommandSource(path)
        if source is not None:
            command = source.command
            if not explicit_format and source.format:
                explicit_format = source.format
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

        # 2. Execute (stdin closed, so a command can neither hang the load
        # waiting for input nor consume the parent's stdin).
        codec = encoding or "utf-8"
        if source is not None or isinstance(path, str):
            # A command URI, or a bare command string: shell, as documented.
            raw = _run_command(command, codec, timeout)
        elif _source._is_materialized_source(path):
            # An in-memory document or a rendered template: run ITS body, not
            # whatever file happens to bear that virtual name on disk.
            suffix = path.suffix
            if not _SCRIPT_SUFFIX_REGEX.match(suffix or ""):
                raise ConfigValueError(
                    f"in-memory source {path.name!r} has no script extension: "
                    "name it .sh/.bat/.ps1/.cmd, or use a cmd:// source to run a "
                    "shell command"
                )
            directory, script = _source._materialize_script(path.read_bytes(), suffix)
            try:
                raw = _run_command(_script_command(script), codec, timeout, shell=False)
            finally:
                shutil.rmtree(directory, ignore_errors=True)
        else:
            # A script file on disk: launched through its interpreter with
            # shell=False, so its own name is never shell syntax.
            raw = _run_command(_script_command(path_str), codec, timeout, shell=False)

        # Strict on success: silently replacing bytes turns a non-ASCII secret
        # into U+FFFD and the failure only surfaces wherever that value is used.
        try:
            stdout = _decode(raw, codec, strict=True)
        except UnicodeDecodeError as exc:
            raise ConfigValueError(
                f"output of command source {path_str!r} is not valid {codec}; "
                "pass encoding= (for example 'oem' on Windows for cmd/.bat/"
                "PowerShell output, or the child's own code page)"
            ) from exc
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
                raise ConfigValueError(
                    "output of command "
                    f"{path_str!r} is empty, cannot parse as "
                    f"{explicit_format or shebang_format}"
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

        sniffing = not (explicit_format or shebang_format)
        failures: "list[tuple[str, BaseException]]" = []
        for fmt in candidates:
            if fmt == "command":
                continue
            reader = fmt
            if sniffing and fmt == "dotenv":
                # Strict, so that one line of prose is not read as a bare key
                # and the output handed back as an empty mapping. Passed as an
                # instance because a reader option named `strict` would be
                # routed to the ConfigLoader, where it means Jinja strictness.
                reader = DotenvBackend(strict=True)
            try:
                result = loads(output, loader=reader, **loads_options)
                if (
                    sniffing
                    and fmt == "yaml"
                    and not isinstance(result, (Mapping, list))
                ):
                    # YAML reads arbitrary text as a scalar, so accepting one
                    # here would stop every later candidate from being tried -
                    # that is how INI output became a flattened dotenv-looking
                    # dict. JSON scalars are unambiguous and stay accepted.
                    continue
                return result
            except _parse_failure_types() as error:
                # Only a parse failure is caught at all, so a TypeError, an
                # OSError or a missing file propagates untouched rather than
                # being read as "wrong format".
                if not _is_wrong_format(error, sniffing):
                    # Not "this format does not fit": a disabled command, an
                    # error from an !include inside the output, or a real
                    # failure such as a missing file. Falling through to
                    # another candidate would turn it into a plausible but
                    # wrong value.
                    _add_error_context(error, frame=ErrorFrame("command", path_str))
                    raise
                failures.append((fmt, error))
                # A single requested format's error is the answer, with its own
                # type; only sniffing moves on to the next candidate.
                if len(candidates) == 1 and (explicit_format or shebang_format):
                    _add_error_context(error, frame=ErrorFrame("command", path_str))
                    raise
                continue

        if explicit_format or shebang_format:
            detail = "; ".join(
                f"{fmt}: {str(error).splitlines()[0][:200] if str(error) else type(error).__name__}"
                for fmt, error in failures
            )
            raise ConfigValueError(
                f"could not parse output of command {path_str!r} as any of "
                f"{candidates}: {detail}"
            ) from (failures[-1][1] if failures else None)

        # Sniffing fallback: return the raw output if no candidate parses successfully
        return output
