"""Skip marks for the optional extras, usable inside ``pytest.param``.

The ``needs_*`` fixtures in ``conftest.py`` cover whole classes and whole test
functions. A single *parameter* of a parametrized test cannot use them:
``pytest.param`` rejects ``pytest.mark.usefixtures``. These marks are the
equivalent for that case.
"""

import importlib.util

import pytest


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _toml_backend_registered() -> bool:
    import yaconfiglib.backends

    return hasattr(yaconfiglib.backends, "TomlConfig")


needs_yaml = pytest.mark.skipif(not _has("yaml"), reason="requires yaconfiglib[yaml]")
needs_jinja2 = pytest.mark.skipif(
    not _has("jinja2"), reason="requires yaconfiglib[jinja2]"
)
needs_toml = pytest.mark.skipif(
    not _toml_backend_registered(),
    reason="TOML backend unavailable: requires yaconfiglib[toml]",
)
