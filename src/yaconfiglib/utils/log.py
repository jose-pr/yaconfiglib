import logging
from logging import Logger

from .enum import IntEnum

__all__ = ["LogLevel", "Logger", "getLogger"]


class LogLevel(IntEnum):
    Critical = logging.CRITICAL
    Error = logging.ERROR
    Warning = logging.WARNING
    Info = logging.INFO
    Debug = logging.DEBUG


def getLogger(logger: LogLevel | Logger, default_name=None):

    if isinstance(logger, Logger):
        return logger
    level = logger
    logger = logging.getLogger(default_name or __name__)
    logger.setLevel(LogLevel(level or LogLevel.Warning))
    return logger
