"""Source resolution: which objects can be a source, and how globs expand."""

import json
import pathlib
import sys

import pytest

from yaconfiglib import ConfigLoader, ConfigLoaderMergeMethod
from yaconfiglib.utils.source import parse_sources


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _FsPathObject:
    """An arbitrary os.PathLike — not a pathlib class."""

    def __init__(self, value):
        self._value = value

    def __fspath__(self):
        return self._value


class TestPathLikeSources:
    def test_stdlib_path_absolute_source(self, tmp_path):
        _write(tmp_path / "app.json", {"a": 1})
        assert ConfigLoader().load(tmp_path / "app.json") == {"a": 1}

    def test_stdlib_path_relative_source_joins_base_dir(self, tmp_path):
        _write(tmp_path / "conf" / "app.json", {"a": 1})
        loader = ConfigLoader(base_dir=str(tmp_path))
        assert loader.load(pathlib.Path("conf") / "app.json") == {"a": 1}

    def test_stdlib_path_glob_source_expands(self, tmp_path):
        _write(tmp_path / "conf" / "a.json", {"a": 1})
        _write(tmp_path / "conf" / "b.json", {"b": 2})
        loader = ConfigLoader(
            base_dir=str(tmp_path), merge=ConfigLoaderMergeMethod.List
        )
        loaded = loader.load(pathlib.Path("conf") / "*.json")
        # The set, not the order: order is pinned separately.
        assert {next(iter(item)) for item in loaded} == {"a", "b"}

    @pytest.mark.parametrize("kind", ["pure_posix_path", "fspath_object"])
    def test_other_pathlike_sources(self, tmp_path, kind):
        _write(tmp_path / "conf" / "app.json", {"a": 1})
        source = (
            pathlib.PurePosixPath("conf/app.json")
            if kind == "pure_posix_path"
            else _FsPathObject("conf/app.json")
        )
        assert ConfigLoader(base_dir=str(tmp_path)).load(source) == {"a": 1}

    def test_pathlib_next_path_source_passes_through(self, tmp_path):
        mempath = pytest.importorskip("pathlib_next.mempath")
        from pathlib_next import LocalPath

        m = mempath.MemPath("memdir/a.json")
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({"a": 1}), encoding="utf-8")
        # A pathlib-next path is yielded as itself: its __fspath__ may raise.
        assert list(parse_sources([m])) == [m]

        _write(tmp_path / "app.json", {"a": 1})
        assert ConfigLoader().load(LocalPath(str(tmp_path / "app.json"))) == {"a": 1}


class TestGlobExpansion:
    """The rules yaconfiglib owns: order, directories, literal-vs-pattern.

    Expansion itself is pathlib-next's; these pin what this package does with
    what it gets back.
    """

    def test_order_ignores_listing_order(self, tmp_path, monkeypatch):
        for name in ("a.json", "B.json", "c.json"):
            _write(tmp_path / name, {name[0]: name})
        loader = ConfigLoader(
            base_dir=str(tmp_path), merge=ConfigLoaderMergeMethod.List
        )
        real_iterdir = type(loader.base_dir).iterdir

        def reversed_iterdir(self):
            return reversed(sorted(real_iterdir(self), key=str))

        monkeypatch.setattr(type(loader.base_dir), "iterdir", reversed_iterdir)
        loaded = loader.load("*.json")
        # Code-point order: "B" (0x42) before "a" (0x61), whatever the listing said.
        assert [next(iter(item)) for item in loaded] == ["B", "a", "c"]

    def test_recursive_matches_follow_path_components(self, tmp_path):
        _write(tmp_path / "00-base.json", {"n": "00-base"})
        _write(tmp_path / "99-local.json", {"n": "99-local"})
        _write(tmp_path / "10-env" / "prod.json", {"n": "10-env/prod"})
        loader = ConfigLoader(
            base_dir=str(tmp_path),
            merge=ConfigLoaderMergeMethod.List,
            recursive=True,
        )
        loaded = loader.load("**/*.json")
        assert [item["n"] for item in loaded] == ["00-base", "10-env/prod", "99-local"]

    def test_existing_name_with_glob_characters_loads_that_file(self, tmp_path):
        _write(tmp_path / "z1.json", {"which": "z1"})
        _write(tmp_path / "z[1].json", {"which": "bracketed"})
        assert ConfigLoader(base_dir=str(tmp_path)).load("z[1].json") == {
            "which": "bracketed"
        }

    @pytest.mark.parametrize("source", ["app.json", "*.json"])
    def test_base_dir_glob_characters_are_literal(self, tmp_path, source):
        base = tmp_path / "proj [v2]"
        _write(base / "app.json", {"a": 1})
        assert ConfigLoader(base_dir=str(base)).load(source) == {"a": 1}

    def test_absolute_literal_under_bracketed_directory(self, tmp_path):
        base = tmp_path / "proj [v2]"
        _write(base / "app.json", {"a": 1})
        assert ConfigLoader().load(str(base / "app.json")) == {"a": 1}

    def test_has_glob_pattern_ignores_extended_length_anchor(self):
        from yaconfiglib.utils.source import has_glob_pattern

        # The only "?" is the extended-length anchor, which is path syntax.
        assert (
            has_glob_pattern(pathlib.PureWindowsPath(r"\\?\C:\conf\app.json")) is False
        )
        # str input keeps working.
        assert has_glob_pattern("conf/*.json") is True
        assert has_glob_pattern("conf/app.json") is False

    def test_directory_matches_are_skipped(self, tmp_path):
        _write(tmp_path / "envs" / "db.json", {"db": 1})
        (tmp_path / "envs" / "sub").mkdir(parents=True, exist_ok=True)
        assert ConfigLoader(base_dir=str(tmp_path)).load("envs/*") == {"db": 1}

    def test_recursive_double_star_selects_every_file(self, tmp_path):
        _write(tmp_path / "a.json", {"a": 1})
        _write(tmp_path / "sub" / "b.json", {"b": 2})
        loader = ConfigLoader(base_dir=str(tmp_path), recursive=True)
        assert loader.load("**/*") == {"a": 1, "b": 2}
        assert loader.load("**/*.json") == {"a": 1, "b": 2}
        # A bare "**" follows the running interpreter, as pathlib does: files
        # too from 3.13, directories only before — and directory matches are
        # skipped, so on 3.9-3.12 nothing loads. Write "**/*" to be portable.
        if sys.version_info >= (3, 13):
            assert loader.load("**") == {"a": 1, "b": 2}
        else:
            assert loader.load("**") is None

    @pytest.mark.parametrize("pattern", ["nodir/*.json", "file.json/*.json"])
    def test_missing_or_file_parent_matches_nothing(self, tmp_path, pattern):
        _write(tmp_path / "base.json", {"base": 1})
        _write(tmp_path / "file.json", {"file": 1})
        loader = ConfigLoader(base_dir=str(tmp_path))
        assert loader.load("base.json", pattern) == {"base": 1}

    def test_hidden_names_are_matched(self, tmp_path):
        """pathlib-next 0.9.4+ includes hidden entries, as pathlib does."""
        _write(tmp_path / "a.json", {"a": 1})
        _write(tmp_path / ".h.json", {"h": 1})
        loader = ConfigLoader(base_dir=str(tmp_path))
        assert loader.load("*.json") == {"a": 1, "h": 1}
        assert loader.load(".*.json") == {"h": 1}

    def test_wildcard_directory_segment(self, tmp_path):
        _write(tmp_path / "envs" / "prod" / "db.json", {"prod": 1})
        _write(tmp_path / "envs" / "dev" / "db.json", {"dev": 1})
        assert ConfigLoader(base_dir=str(tmp_path)).load("envs/*/db.json") == {
            "dev": 1,
            "prod": 1,
        }

    def test_two_recursive_segments_find_every_file(self, tmp_path):
        _write(tmp_path / "conf" / "a.json", {"a": 1})
        _write(tmp_path / "conf" / "sub" / "c.json", {"c": 1})
        _write(tmp_path / "x" / "conf" / "sub" / "b.json", {"b": 1})
        loader = ConfigLoader(base_dir=str(tmp_path), recursive=True)
        assert loader.load("**/conf/**/*.json") == {"a": 1, "b": 1, "c": 1}

    def test_mempath_glob_without_base_dir_keeps_spelling(self):
        mempath = pytest.importorskip("pathlib_next.mempath")

        # Everything must come off ONE MemPath instance: each constructor call
        # creates its own in-memory filesystem, so a second MemPath("memdir")
        # would see none of these files.
        root = mempath.MemPath("memdir")
        root.mkdir(parents=True, exist_ok=True)
        for name in ("a.json", "b.json"):
            (root / name).write_text("{}", encoding="utf-8")
        got = [str(p) for p in parse_sources([root / "*.json"])]
        assert got == ["memdir/a.json", "memdir/b.json"]
