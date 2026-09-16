"""Source resolution: which objects can be a source, and how globs expand."""

import json
import os
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


class TestConcretePathDedup:
    """One file loads once, however it was named."""

    def _layered(self, tmp_path):
        _write(tmp_path / "conf" / "app.json", {"n": 1})
        _write(tmp_path / "conf" / "local.json", {"n": 2})
        return ConfigLoader(base_dir=str(tmp_path), merge=ConfigLoaderMergeMethod.List)

    def test_same_file_two_spellings_loads_once(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/app.json", "./conf/app.json") == [{"n": 1}]

    def test_dot_dot_spelling_loads_once(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/app.json", "conf/../conf/app.json") == [{"n": 1}]

    @pytest.mark.skipif(
        os.path.normcase("A") != "a", reason="case-insensitive filesystem only"
    )
    def test_case_variant_loads_once(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/app.json", "conf/App.json") == [{"n": 1}]

    def test_explicit_name_after_a_glob_wins_its_own_position(self, tmp_path):
        loader = self._layered(tmp_path)
        # local.json is named explicitly, so the glob must not also yield it.
        assert loader.load("conf/*.json", "conf/local.json") == [{"n": 1}, {"n": 2}]

    def test_explicit_name_before_a_glob_keeps_its_position(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/app.json", "conf/*.json") == [{"n": 1}, {"n": 2}]

    def test_overlapping_globs_yield_each_file_once(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/*.json", "conf/a*.json") == [{"n": 1}, {"n": 2}]

    def test_unsupported_source_raises_before_anything_loads(self, tmp_path):
        loaded = []
        loader = ConfigLoader(base_dir=str(tmp_path))
        _write(tmp_path / "app.json", {"n": 1})

        with pytest.raises(ValueError, match="unable to handle arg"):
            for path in parse_sources(["app.json", object()], base_dir=loader.base_dir):
                loaded.append(path)
        assert loaded == []

    def test_caller_memo_still_dedupes_across_calls(self, tmp_path):
        _write(tmp_path / "app.json", {"n": 1})
        loader = ConfigLoader(base_dir=str(tmp_path))
        memo = set()
        first = list(parse_sources(["app.json"], base_dir=loader.base_dir, memo=memo))
        second = list(parse_sources(["*.json"], base_dir=loader.base_dir, memo=memo))
        assert len(first) == 1
        assert second == []


class TestHashKeyCollisions:
    """`Hash` keys on the filename stem, which a directory glob repeats."""

    def _services(self, tmp_path):
        for name in ("api", "billing", "web"):
            _write(tmp_path / "services" / name / "config.json", {"service": name})
        return tmp_path

    def test_repeated_key_warns_and_keeps_the_last_document(self, tmp_path, caplog):
        import logging

        self._services(tmp_path)
        loader = ConfigLoader(
            base_dir=str(tmp_path), merge=ConfigLoaderMergeMethod.Hash
        )
        with caplog.at_level(logging.WARNING, logger="yaconfiglib"):
            result = loader.load("services/*/config.json")
        # Every stem is "config", so only the last document survives.
        assert result == {"config": {"service": "web"}}
        collisions = [r for r in caplog.records if "config" in r.getMessage()]
        assert len(collisions) == 2, [r.getMessage() for r in caplog.records]

    def test_parent_directory_key_keeps_every_source(self, tmp_path, caplog):
        import logging

        self._services(tmp_path)
        loader = ConfigLoader(
            base_dir=str(tmp_path),
            merge=ConfigLoaderMergeMethod.Hash,
            key_factory=lambda path, value: path.parent.name,
        )
        with caplog.at_level(logging.WARNING, logger="yaconfiglib"):
            result = loader.load("services/*/config.json")
        assert set(result) == {"api", "billing", "web"}
        assert [
            r.getMessage() for r in caplog.records if "Hash merge" in r.getMessage()
        ] == []


@pytest.fixture
def locked_tree(tmp_path, monkeypatch):
    """A tree with one directory whose listing raises `PermissionError`.

    Patches `_scandir`, **not** `iterdir`: pathlib-next 0.9.4+ lists through
    `_scandir`, so an `iterdir` patch is never reached and makes an unreadable
    directory look readable. A monkeypatch rather than real ACLs, so this runs
    the same on Windows and in a root-owned CI container.
    """
    (tmp_path / "lock" / "ok").mkdir(parents=True)
    (tmp_path / "lock" / "locked").mkdir(parents=True)
    (tmp_path / "lock" / "ok" / "a.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "lock" / "locked" / "b.json").write_text('{"b": 2}', encoding="utf-8")

    from yaconfiglib.utils.source import Path as SourcePath

    original = SourcePath._scandir

    def patched(self, *args, **kwargs):
        if self.name == "locked":
            raise PermissionError(13, "Permission denied", str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(SourcePath(str(tmp_path))), "_scandir", patched)
    return tmp_path


class TestGlobExpansionErrors:
    """A directory glob expansion cannot list is reported, not silently dropped.

    pathlib skips an unreadable directory without a word, so a whole
    configuration layer could disappear. pathlib-next 0.9.7 added
    `glob(on_error=)`, which is what lets `ignore_error` see it.
    """

    def test_unreadable_directory_raises_by_default(self, locked_tree):
        from yaconfiglib import ConfigLoader

        loader = ConfigLoader(base_dir=str(locked_tree))
        with pytest.raises(PermissionError):
            loader.load("lock/**/*.json", recursive=True)

    def test_ignore_error_true_skips_only_that_directory(self, locked_tree):
        from yaconfiglib import ConfigLoader

        loader = ConfigLoader(base_dir=str(locked_tree), ignore_error=True)
        # The readable sibling still loads: one locked subdirectory must not
        # drop the whole layer.
        assert loader.load("lock/**/*.json", recursive=True) == {"a": 1}

    def test_predicate_receives_error_directory_and_loader(self, locked_tree):
        from yaconfiglib import ConfigLoader

        seen = []

        def record(error, **context):
            seen.append((error, context))
            return True

        loader = ConfigLoader(base_dir=str(locked_tree), ignore_error=record)
        loader.load("lock/**/*.json", recursive=True)
        # Once per unreadable directory, whatever the pattern visits.
        assert len(seen) == 1
        error, context = seen[0]
        assert isinstance(error, PermissionError)
        assert context["path"].name == "locked"
        assert context["loader"] is loader

    def test_glob_expansion_offer_uses_glob_phase(self, locked_tree):
        from yaconfiglib import ConfigLoader

        seen = []

        def record(error, **context):
            seen.append((context["phase"], context["path"].name))
            return True

        loader = ConfigLoader(base_dir=str(locked_tree), ignore_error=record)
        result = loader.load("lock/**/*.json", recursive=True)
        assert seen == [("glob", "locked")]
        assert result == {"a": 1}

    def test_load_all_skips_unreadable_directory(self, locked_tree):
        from yaconfiglib import ConfigLoader

        loader = ConfigLoader(base_dir=str(locked_tree), ignore_error=True)
        assert list(loader.load_all("lock/**/*.json", recursive=True)) == [{"a": 1}]

    def test_parse_sources_on_error_true_continues(self, locked_tree):
        from yaconfiglib.utils.source import Path as SourcePath
        from yaconfiglib.utils.source import parse_sources

        matches = list(
            parse_sources(
                ["lock/**/*.json"],
                base_dir=SourcePath(str(locked_tree)),
                recursive=True,
                on_error=lambda error, directory: True,
            )
        )
        assert [p.name for p in matches] == ["a.json"]

    def test_parse_sources_on_error_false_reraises(self, locked_tree):
        from yaconfiglib.utils.source import Path as SourcePath
        from yaconfiglib.utils.source import parse_sources

        with pytest.raises(PermissionError):
            list(
                parse_sources(
                    ["lock/**/*.json"],
                    base_dir=SourcePath(str(locked_tree)),
                    recursive=True,
                    on_error=lambda error, directory: False,
                )
            )
