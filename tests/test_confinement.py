"""`confine_to=`: refusing a file read that resolves outside the allowed roots.

Without confinement a document can read any file the process can, through an
`!include` with an absolute path or ``..`` traversal — which is what
`docs/guide/security.md` documented as uncovered. These tests pin both that the
control works and that it stays off unless asked for.
"""

import io
import os
import subprocess
import sys

import pytest

import yaconfiglib
from yaconfiglib import ConfigError, ConfigLoader, ConfinementError
from yaconfiglib.errors import ConfigTypeError


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


@pytest.mark.usefixtures("needs_yaml")
class TestConfinementCannotFailOpenSilently:
    """The ways a caller could *believe* confinement is on and have none.

    Every case here read the outside file before 2026-09-17. A control that
    fails open without a word is worse than one that is absent, because the
    absent one is visible — so each of these now raises or is enforced.
    """

    def test_per_call_confine_to_is_refused(self, tmp_path):
        # It is a constructor setting, so it used to land in **reader_args,
        # reach the backend and be dropped: the caller asked for confinement
        # and got none. Every OTHER unknown keyword is still ignored by
        # design (backends are pluggable); this one is special-cased because
        # silence is the wrong answer for a security option.
        conf = _tree(tmp_path)
        loader = ConfigLoader(base_dir=str(conf))
        with pytest.raises(ConfigTypeError) as caught:
            loader.load("inside.yaml", confine_to=[str(conf)])
        message = str(caught.value)
        assert "ConfigLoader" in message
        # The message has to say where the keyword does work.
        assert "load" in message

    def test_per_call_confine_to_is_refused_by_load_all(self, tmp_path):
        conf = _tree(tmp_path)
        loader = ConfigLoader(base_dir=str(conf))
        with pytest.raises(ConfigTypeError):
            list(loader.load_all("inside.yaml", confine_to=[str(conf)]))

    def test_module_level_confine_to_still_reaches_the_loader(self, tmp_path):
        # The counterpart: yaconfiglib.load() routes constructor names to the
        # loader, so the same keyword must keep working there.
        conf = _tree(tmp_path)
        with pytest.raises(ConfinementError):
            yaconfiglib.load(
                str(tmp_path / "secret.yaml"),
                base_dir=str(conf),
                confine_to=[str(conf)],
            )

    def test_assigning_confine_to_takes_effect(self, tmp_path):
        # The attribute used to report a root set that nothing enforced.
        conf = _tree(tmp_path)
        loader = ConfigLoader(base_dir=str(conf))
        assert loader.load(str(tmp_path / "secret.yaml")) == {"secret": "s3cr3t"}
        loader.confine_to = [str(conf)]
        assert loader.confine_to == [str(conf)]
        with pytest.raises(ConfinementError):
            loader.load(str(tmp_path / "secret.yaml"))

    def test_assigning_confine_to_none_turns_it_off(self, tmp_path):
        # And the other direction, which used to stay confined.
        conf = _tree(tmp_path)
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        with pytest.raises(ConfinementError):
            loader.load(str(tmp_path / "secret.yaml"))
        loader.confine_to = None
        assert loader.load(str(tmp_path / "secret.yaml")) == {"secret": "s3cr3t"}

    def test_stream_on_an_outside_file_is_refused(self, tmp_path):
        # A stream materializes into a MemPath, which has no location, so the
        # check downstream saw nothing to confine while the caller's own
        # `open()` had already handed us the file.
        conf = _tree(tmp_path)
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        with open(tmp_path / "secret.yaml", encoding="utf-8") as handle:
            with pytest.raises(ConfinementError):
                loader.load(handle)

    def test_stream_inside_a_root_still_loads(self, tmp_path):
        conf = _tree(tmp_path)
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        with open(conf / "inside.yaml", encoding="utf-8") as handle:
            assert loader.load(handle) == {"inside": True}

    def test_a_nameless_stream_stays_exempt(self, tmp_path):
        # There is no location to confine, and treating "<stdin>" as a
        # filename would resolve it under the working directory and refuse a
        # perfectly ordinary pipe. Both spellings must stay loadable.
        conf = _tree(tmp_path)
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        assert loader.load(io.StringIO("k: v\n")) == {"k": "v"}

        class _Pipe(io.StringIO):
            name = "<stdin>"

        assert loader.load(_Pipe("k: v\n")) == {"k": "v"}

    def test_command_output_includes_are_confined(self, tmp_path):
        # The output is parsed by a NEW loader, so the roots have to be handed
        # over: the same !include was refused in a file and honoured here.
        conf = _tree(tmp_path)
        emit = tmp_path / "emit.py"
        emit.write_text(
            "print(\"data: !include '%s'\")\n" % (tmp_path / "secret.yaml").as_posix(),
            encoding="utf-8",
        )
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        cmd = f'cmd://"{sys.executable}" "{emit}"'
        with pytest.raises(ConfinementError):
            loader.load(cmd, timeout=60)

    def test_command_output_includes_inside_a_root_still_load(self, tmp_path):
        conf = _tree(tmp_path)
        emit = tmp_path / "emit_ok.py"
        emit.write_text(
            "print(\"data: !include '%s'\")\n" % (conf / "inside.yaml").as_posix(),
            encoding="utf-8",
        )
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        cmd = f'cmd://"{sys.executable}" "{emit}"'
        assert loader.load(cmd, timeout=60) == {"data": {"inside": True}}


@pytest.mark.usefixtures("needs_yaml")
class TestConfinementRootForms:
    """What counts as a root, and what is refused rather than guessed at.

    `os.path.abspath` resolves anything unanchored against the working
    directory, so before 2026-09-17 a typo, a stray space from an environment
    variable or an empty list entry silently became a root under the cwd — a
    directory an attacker may be able to write to. An allowlist is the wrong
    place to guess.
    """

    def test_relative_root_is_refused(self, tmp_path):
        # `abspath` would resolve it against the working directory and hand
        # back a root nobody named — possibly one an attacker can write to.
        conf = _tree(tmp_path)
        with pytest.raises(ConfigTypeError) as caught:
            ConfigLoader(base_dir=str(conf), confine_to=["conf"])
        message = str(caught.value)
        assert "absolute" in message
        # It must say what to do instead.
        assert "confine_to=True" in message

    @pytest.mark.parametrize(
        "root",
        [".", "conf", "~/conf", pytest.param("C:conf", id="drive_relative")],
    )
    def test_unanchored_root_forms_are_refused(self, tmp_path, root):
        if root == "C:conf" and sys.platform != "win32":
            pytest.skip("drive-relative paths are a Windows spelling")
        with pytest.raises(ConfigTypeError):
            ConfigLoader(base_dir=str(tmp_path), confine_to=[root])

    def test_empty_entries_do_not_become_the_working_directory(self, tmp_path):
        # `[""]` is the shape `os.environ.get(X, "").split(os.pathsep)` makes
        # in a caller's own code. It used to open the whole cwd tree; it now
        # means what `""` means — an empty allowlist, which allows nothing.
        conf = _tree(tmp_path)
        loader = ConfigLoader(base_dir=str(conf), confine_to=[""])
        assert loader._confine_roots == ()
        _including(conf, "inside.yaml")
        with pytest.raises(ConfinementError):
            loader.load("app.yaml")

    def test_env_value_with_a_stray_space_is_refused(self, tmp_path, monkeypatch):
        # `abspath` strips a trailing space but keeps a leading one, so
        # " C:\\other" stopped being drive-anchored and landed under the cwd —
        # silently dropping the root the operator actually named.
        conf = _tree(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv("YACONFIGLIB_CONFINE_TO", f"{conf}{os.pathsep} {other}")
        with pytest.raises(ConfigTypeError):
            ConfigLoader(base_dir=str(conf))

    def test_absolute_roots_are_unaffected(self, tmp_path):
        conf = _tree(tmp_path)
        _including(conf, "inside.yaml")
        loader = ConfigLoader(base_dir=str(conf), confine_to=[str(conf)])
        assert loader.load("app.yaml") == {"data": {"inside": True}}

    def test_a_unc_share_root_contains_its_files(self):
        # Lexical, so no share has to exist: `commonpath` treats a bare
        # `\\host\share` as RELATIVE (a drive with no root component) and used
        # to raise, which `_within_roots` read as "not contained" — denying a
        # whole legitimate root.
        from yaconfiglib.utils.source import _confinement_root_key, _within_roots

        if sys.platform != "win32":
            pytest.skip("UNC paths are a Windows spelling")
        roots = (_confinement_root_key(r"\\srv\share"),)
        assert _within_roots(r"\\srv\share\conf\app.yaml", roots)
        assert _within_roots(r"\\srv\share\app.yaml", roots)
        assert not _within_roots(r"\\srv\other\app.yaml", roots)
