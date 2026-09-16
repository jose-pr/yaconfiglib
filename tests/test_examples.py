"""The shipped examples must run, from any working directory."""

import subprocess
import sys

import pytest


@pytest.mark.usefixtures("needs_yaml", "needs_jinja2")
@pytest.mark.parametrize("name", ["example.py", "run_advanced.py"])
def test_example_script_runs_from_any_cwd(examples_dir, tmp_path, name):
    done = subprocess.run(
        [sys.executable, str(examples_dir / name)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    # The library logs this when a script registers !include/!load by hand.
    assert "overriding pre-existing YAML constructor" not in done.stderr
    # Output is JSON, not a yaml.dump full of Python object tags.
    assert "!!python/object" not in done.stdout
