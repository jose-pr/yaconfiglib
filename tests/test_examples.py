"""The shipped examples must run, from any working directory."""

import subprocess
import sys

import pytest

import yaconfiglib


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


@pytest.mark.usefixtures("needs_yaml", "needs_jinja2")
def test_security_checklist_snippet_runs(tmp_path):
    """The untrusted-config checklist in `docs/guide/security.md` must work.

    Its exact keyword set, so the documented recipe cannot drift from the API:
    a copied snippet that raises `TypeError` is worse than no snippet.
    """
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "app.yaml").write_text(
        "name: demo\ngreeting: 'hello {{ name }}'\nextra: !include 'extra.yaml'\n",
        encoding="utf-8",
    )
    (conf / "extra.yaml").write_text("tier: prod\n", encoding="utf-8")
    (tmp_path / "secret.yaml").write_text("secret: s3cr3t\n", encoding="utf-8")

    config = yaconfiglib.load(
        str(conf / "app.yaml"),
        base_dir=str(conf),
        allow_commands=False,
        interpolate=True,
        sandbox=True,
        confine_to=True,
    )
    assert config["greeting"] == "hello demo"
    assert config["extra"] == {"tier": "prod"}

    # And the claim the checklist makes about file reads.
    (conf / "hostile.yaml").write_text(
        f"stolen: !include '{(tmp_path / 'secret.yaml').as_posix()}'\n",
        encoding="utf-8",
    )
    with pytest.raises(yaconfiglib.ConfinementError):
        yaconfiglib.load(
            str(conf / "hostile.yaml"),
            base_dir=str(conf),
            allow_commands=False,
            interpolate=True,
            sandbox=True,
            confine_to=True,
        )
