"""The effective trust policy of an in-progress load.

``allow_commands``, ``sandbox`` and ``strict`` are set on a
:class:`~yaconfiglib.loader.ConfigLoader` or per ``load()`` call, but the code
that has to honour them runs further down: nested ``!include`` loads, the
command backend (including the ``loads()`` it runs on command output) and the
Jinja2 source backend. A :class:`contextvars.ContextVar` carries the effective
values to all of them, per thread and per asyncio task.

The policy only ever tightens: a nested load, an included document or a
per-call argument can disable commands or enable the sandbox, never the
reverse. Outside any load the policy is permissive, so a directly constructed
backend keeps working as before.

This module is a leaf (no yaconfiglib imports), so backends can import it
without an import cycle.
"""

from __future__ import annotations

import contextlib
import contextvars
import typing

__all__ = [
    "CommandsDisabledError",
    "current_policy",
    "is_hardened",
    "use_policy",
]


# Defined in `yaconfiglib.errors` (a leaf module, like this one) and re-exported
# here, where it used to live: `except CommandsDisabledError` imported from
# either place must match the same class.
from ..errors import CommandsDisabledError as CommandsDisabledError  # noqa: E402

#: ``(allow_commands, sandbox, strict)`` in effect for the current context.
_POLICY: "contextvars.ContextVar[typing.Tuple[bool, bool, bool]]" = (
    contextvars.ContextVar("yaconfiglib_trust_policy", default=(True, False, False))
)


def current_policy() -> typing.Tuple[bool, bool, bool]:
    """Return the effective ``(allow_commands, sandbox, strict)`` for this context."""
    return _POLICY.get()


def is_hardened(policy: typing.Optional[typing.Tuple[bool, bool, bool]] = None) -> bool:
    """Return True when commands are disabled or the sandbox is on.

    Document-selected Jinja expressions (``transform``, a ``%`` key factory,
    ``.j2`` sources) are evaluated sandboxed whenever this is True.
    """
    allow_commands, sandbox, _strict = current_policy() if policy is None else policy
    return sandbox or not allow_commands


@contextlib.contextmanager
def use_policy(
    allow_commands: bool, sandbox: bool, strict: bool
) -> typing.Iterator[typing.Tuple[bool, bool, bool]]:
    """Enter a policy combined with the current one; it can only tighten.

    Yields the effective policy and restores the previous one on exit.
    """
    parent_allow, parent_sandbox, parent_strict = _POLICY.get()
    effective = (
        parent_allow and bool(allow_commands),
        parent_sandbox or bool(sandbox),
        parent_strict or bool(strict),
    )
    token = _POLICY.set(effective)
    try:
        yield effective
    finally:
        _POLICY.reset(token)
