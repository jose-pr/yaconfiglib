"""
Shared pytest fixtures for yaconfiglib tests.
"""

import pathlib
import pytest

EXAMPLES_DIR = pathlib.Path(__file__).parent.parent / "examples"


@pytest.fixture
def examples_dir():
    """Return the path to the examples directory."""
    return EXAMPLES_DIR


@pytest.fixture
def needs_yaml():
    """Skip unless PyYAML is installed (yaconfiglib[yaml])."""
    pytest.importorskip("yaml")


@pytest.fixture
def needs_jinja2():
    """Skip unless Jinja2 is installed (yaconfiglib[jinja2])."""
    pytest.importorskip("jinja2")


@pytest.fixture
def needs_toml():
    """Skip unless a TOML backend registered (yaconfiglib[toml] below 3.11).

    Keyed on registration rather than a module name, so it keeps working
    whichever parser the backend imports.
    """
    import yaconfiglib.backends

    if not hasattr(yaconfiglib.backends, "TomlConfig"):
        pytest.skip("TOML backend unavailable: install yaconfiglib[toml]")
