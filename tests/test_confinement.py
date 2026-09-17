"""`confine_to=`: refusing a file read that resolves outside the allowed roots.

Without confinement a document can read any file the process can, through an
`!include` with an absolute path or ``..`` traversal — which is what
`docs/guide/security.md` documented as uncovered. These tests pin both that the
control works and that it stays off unless asked for.
"""

import os
import subprocess
import sys

import pytest

from yaconfiglib import ConfigError, ConfigLoader, ConfinementError


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _tree(tmp_path):
    """``conf/`` with a relative include target, and a secret outside it."""
    conf = tmp_path / "conf"
    _write(conf / "inside.yaml", "inside: yes\n")
    _write(tmp_path / "secret.yaml", "secret: s3cr3t\n")
    return conf


def _including(conf, target):
    """``conf/app.yaml``, including *target* (a str written into the YAML)."""
    return _write(conf / "app.yaml", f"data: !include '{target}'\n")


@pytest.mark.usefixtures("needs_yaml")
class TestConfinement:
    """One rule: a local file read must resolve inside one of the roots."""

    def test_off_by_default_reads_outside(self, tmp_path):
        # Directive 1: a trusted layout that includes from anywhere keeps
        # working untouched. This is a pin, not a wish.
        conf = _tree(tmp_path)
        _including(conf, (tmp_path / "secret.yaml").as_posix())
        loader = ConfigLoader(base_dir=str(conf))
        assert loader.load("app.yaml") == {"data": {"secret": "s3cr3t"}}

    def test_include_inside_root_loads(self, tmp_path):
        conf = _tree(tmp_path)
        _including(conf, "inside.yaml")
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        assert loader.load("app.yaml") == {"data": {"inside": True}}

    def test_absolute_include_outside_root_refused(self, tmp_path):
        conf = _tree(tmp_path)
        _including(conf, (tmp_path / "secret.yaml").as_posix())
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        with pytest.raises(ConfinementError):
            loader.load("app.yaml")

    def test_dotdot_escape_refused(self, tmp_path):
        conf = _tree(tmp_path)
        # Relative to the including file (plan `include_path_resolution`), so
        # this really does name <tmp>/secret.yaml.
        _including(conf, "../secret.yaml")
        loader = ConfigLoader(base_dir=str(conf), confine_to=True)
        with pytest.raises(ConfinementError):
            loader.load("app.yaml")

    def test_sibling_prefix_directory_refused(self, tmp_path):
        # The measured prefix trap: "<tmp>/confidential/x.yaml" starts with
        # "<tmp>/conf" as a string while lying outside that directory.
        conf = _tree(tmp_path)
        _write(tmp_path / "confidential" / "x.yaml", "leaked: yes\n")
        _including(conf, (tmp_path / "confidential" / "x.yaml").as_posix())
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        with pytest.raises(ConfinementError):
            loader.load("app.yaml")

    def test_any_of_several_roots_accepts(self, tmp_path):
        conf = _tree(tmp_path)
        shared = _write(tmp_path / "shared" / "extra.yaml", "extra: 1\n")
        _including(conf, shared.as_posix())
        loader = ConfigLoader(
            base_dir=str(conf), confine_to=[str(conf), str(shared.parent)]
        )
        assert loader.load("app.yaml") == {"data": {"extra": 1}}

    def test_pathsep_string_is_split(self, tmp_path):
        conf = _tree(tmp_path)
        shared = _write(tmp_path / "shared" / "extra.yaml", "extra: 1\n")
        _including(conf, shared.as_posix())
        # The PATH spelling, so one environment variable can carry the roots.
        # os.pathsep, never a literal ":": that would split "C:".
        roots = os.pathsep.join([str(conf), str(shared.parent)])
        loader = ConfigLoader(base_dir=str(conf), confine_to=roots)
        assert loader.load("app.yaml") == {"data": {"extra": 1}}

    def test_env_var_is_read_when_argument_absent(self, tmp_path, monkeypatch):
        conf = _tree(tmp_path)
        _including(conf, (tmp_path / "secret.yaml").as_posix())
        monkeypatch.setenv("YACONFIGLIB_CONFINE_TO", str(conf))
        loader = ConfigLoader(base_dir=str(conf))
        with pytest.raises(ConfinementError):
            loader.load("app.yaml")

    def test_argument_overrides_env_var(self, tmp_path, monkeypatch):
        conf = _tree(tmp_path)
        _including(conf, (tmp_path / "secret.yaml").as_posix())
        # The environment names a root that WOULD accept the include; the
        # argument names one that refuses. An env var that could widen an
        # in-code allowlist would be an escalation, so the argument wins.
        monkeypatch.setenv("YACONFIGLIB_CONFINE_TO", str(tmp_path))
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        with pytest.raises(ConfinementError):
            loader.load("app.yaml")

    def test_empty_allowlist_refuses_everything(self, tmp_path):
        # "The allowlist is empty" means nothing is permitted — including the
        # top-level source. Treating it as "off" would silently disable a
        # security control.
        conf = _tree(tmp_path)
        _including(conf, "inside.yaml")
        loader = ConfigLoader(base_dir=str(conf), confine_to=[])
        with pytest.raises(ConfinementError):
            loader.load("app.yaml")

    def test_top_level_source_is_checked(self, tmp_path):
        # The documented cost of one rule for every source: confine_to=True
        # refuses the caller's own path outside base_dir, which is what
        # asking to confine to base_dir means.
        conf = _tree(tmp_path)
        loader = ConfigLoader(base_dir=str(conf), confine_to=True)
        with pytest.raises(ConfinementError):
            loader.load(str(tmp_path / "secret.yaml"))

    def test_in_memory_and_command_sources_are_exempt(self, tmp_path):
        # Exempt by type: neither has a location to confine. The command
        # source stays governed by allow_commands.
        loader = ConfigLoader(base_dir=str(tmp_path), confine_to=[])
        assert loader.load("#!doc.yaml\nk: v\n") == {"k": "v"}
        cmd = f'cmd://"{sys.executable}" -c "print(\'k: v\')"'
        assert loader.load(cmd, timeout=60) == {"k": "v"}

    def test_symlink_inside_root_to_outside_loads(self, tmp_path):
        # Directive 5, and the reason the check is lexical: a link inside a
        # root was put there by whoever administers the root. A
        # realpath-based check would refuse this load, which is exactly the
        # behaviour that was rejected.
        conf = _tree(tmp_path)
        outside = tmp_path / "secret.yaml"
        link = conf / "linked.yaml"
        try:
            os.symlink(str(outside), str(link))
        except (OSError, NotImplementedError):
            # Windows needs Developer Mode or elevation for a file symlink;
            # mklink is the fallback, and a refusal is a skip, not a failure.
            completed = subprocess.run(
                ["cmd", "/d", "/c", "mklink", str(link), str(outside)],
                capture_output=True,
            )
            if completed.returncode != 0 or not os.path.islink(str(link)):
                pytest.skip("cannot create a file symlink here")
        _including(conf, "linked.yaml")
        loader = ConfigLoader(base_dir=str(conf), confine_to=True)
        assert loader.load("app.yaml") == {"data": {"secret": "s3cr3t"}}

    def test_refusal_names_target_and_roots(self, tmp_path):
        conf = _tree(tmp_path)
        other = tmp_path / "shared"
        other.mkdir()
        _including(conf, (tmp_path / "secret.yaml").as_posix())
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf), str(other)])
        with pytest.raises(ConfinementError) as caught:
            loader.load("app.yaml")
        message = str(caught.value)
        # The resolved target, and every root it was checked against: a
        # refusal nobody can diagnose gets switched off.
        assert os.path.normcase(str(tmp_path / "secret.yaml")) in os.path.normcase(
            message
        )
        for root in (conf, other):
            assert os.path.normcase(str(root)) in os.path.normcase(message)
        # An OSError for a CLI, a ConfigError for this library's own handling.
        assert isinstance(caught.value, (ConfigError, PermissionError))
