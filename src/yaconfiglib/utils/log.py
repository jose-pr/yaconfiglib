from __future__ import annotations

import logging

from .enum import IntEnum

__all__ = ["LogLevel"]


class LogLevel(IntEnum):
    Critical = logging.CRITICAL
    Error = logging.ERROR
    Warning = logging.WARNING
    Info = logging.INFO
    Debug = logging.DEBUG
