# Errors

Every exception yaconfiglib raises for a configuration condition is a
`ConfigError` **and** the builtin exception that condition raised before, so
`except ValueError` or `except NotImplementedError` written against an earlier
version keeps matching.

A parser's own errors are never wrapped: a malformed YAML file still raises
`yaml.YAMLError`, and a missing file still raises `FileNotFoundError`. That is
what lets an `ignore_error` predicate decide per error type. To catch
everything a load can raise for a configuration or I/O reason in one clause,
use [`load_error_types()`](#yaconfiglib.errors.load_error_types).

::: yaconfiglib.errors.ConfigError

::: yaconfiglib.errors.ConfigValueError

::: yaconfiglib.errors.ConfigTypeError

::: yaconfiglib.errors.UnsupportedFormatError

::: yaconfiglib.errors.UnknownLoaderError

::: yaconfiglib.errors.CommandsDisabledError

::: yaconfiglib.errors.load_error_types
