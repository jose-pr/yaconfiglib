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
