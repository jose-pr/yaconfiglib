"""Source resolution: which objects can be a source, and how globs expand."""

import json
import os
import pathlib
import subprocess
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


class TestStdlibPathFactory:
    """`path_factory=pathlib.Path` gets pathlib-next's expansion too.

    Expansion used to fall to `path.parent.glob(path.name)` for a stdlib path,
    which cannot expand ``**`` — it looks inside a literal ``**`` directory —
    so a `recursive=` source expanded to **nothing**, silently, and `on_error`
    and `bound_loops` were inert. The path is converted for the walk and the
    matches converted back, so the factory decides the *type* a caller sees and
    not which glob they get.
    """

    def test_recursive_glob_works(self, tmp_path):
        _write(tmp_path / "a.json", {"a": 1})
        _write(tmp_path / "sub" / "b.json", {"b": 2})
        loader = ConfigLoader(
            base_dir=str(tmp_path), path_factory=pathlib.Path, recursive=True
        )
        assert loader.load("**/*.json") == {"a": 1, "b": 2}

    def test_matches_keep_the_callers_path_type(self, tmp_path):
        _write(tmp_path / "a.json", {"a": 1})
        _write(tmp_path / "sub" / "b.json", {"b": 2})
        matches = list(
            parse_sources(
                ["**/*.json"],
                base_dir=pathlib.Path(tmp_path),
                path_factory=pathlib.Path,
                recursive=True,
            )
        )
        assert [p.name for p in matches] == ["a.json", "b.json"]
        # The caller asked for stdlib paths; how expansion is implemented is
        # not their concern, so no LocalPath may leak out of it.
        assert all(isinstance(p, pathlib.Path) for p in matches)
        assert not any(type(p).__name__ == "LocalPath" for p in matches)

    def test_simple_glob_still_works(self, tmp_path):
        _write(tmp_path / "a.json", {"a": 1})
        _write(tmp_path / "b.json", {"b": 2})
        loader = ConfigLoader(base_dir=str(tmp_path), path_factory=pathlib.Path)
        assert loader.load("*.json") == {"a": 1, "b": 2}

    def test_on_error_reaches_a_stdlib_factory_walk(self, locked_tree):
        # Impossible before: stdlib glob swallows a listing failure, so the
        # predicate was never called and the layer vanished silently.
        seen = []

        def record(error, **context):
            seen.append((context["phase"], context["path"].name))
            return True

        loader = ConfigLoader(
            base_dir=str(locked_tree),
            path_factory=pathlib.Path,
            ignore_error=record,
        )
        assert loader.load("lock/**/*.json", recursive=True) == {"a": 1}
        assert seen == [("glob", "locked")]


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


#: Only a **junction** is walked by ``**``. A POSIX directory symlink is not:
#: pathlib-next defaults to ``recurse_symlinks=False`` and raises
#: `NotImplementedError` for ``True`` (measured on 0.9.8), so a symlinked loop
#: is never entered and there is nothing for `bound_loops` to bound. Junctions
#: are the reverse case — they report ``is_symlink() == False``, so no symlink
#: check sees them and ``**`` walks straight into them.
_LINKS_ARE_DESCENDED = sys.platform == "win32"

requires_descended_links = pytest.mark.skipif(
    not _LINKS_ARE_DESCENDED,
    reason="only a Windows junction is descended by **; a POSIX symlink is not",
)


def _make_dir_link(link, target):
    """Point *link* at the directory *target*, or skip the calling test.

    A junction (``mklink /J``) and a POSIX directory symlink both need no
    privilege, so this normally runs; a filesystem or container that refuses
    one is a reason to skip rather than to fail.
    """
    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        if completed.returncode != 0:
            pytest.skip("cannot create a directory junction here")
    else:
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("cannot create a directory symlink here")

    from yaconfiglib.utils.source import Path as SourcePath

    is_junction = getattr(SourcePath(str(link)), "is_junction", None)
    # A fixture that quietly made a plain directory would make every assertion
    # below pass without ever crossing a link.
    assert os.path.islink(str(link)) or (is_junction is not None and is_junction())


@pytest.fixture
def looped_tree(tmp_path):
    """A tree whose ``deep/loop`` points back at its own root."""
    conf = tmp_path / "conf"
    _write(conf / "10-base.json", {"a": 1})
    _write(conf / "deep" / "20-extra.json", {"b": 2})
    _make_dir_link(conf / "deep" / "loop", conf)
    return conf


@pytest.fixture
def twice_named_tree(tmp_path):
    """One shared directory reachable under two sibling names — not a loop."""
    conf = tmp_path / "conf"
    _write(conf / "own.json", {"a": 1})
    _write(tmp_path / "shared" / "common.json", {"b": 2})
    _make_dir_link(conf / "site-a", tmp_path / "shared")
    _make_dir_link(conf / "site-b", tmp_path / "shared")
    return conf


@requires_descended_links
class TestGlobLoopBounding:
    """A ``**`` bounds a directory loop by default, and costs nothing for it.

    Unbounded, a ``**`` that crosses a loop walks it until the filesystem
    refuses the path — the load **fails** — so "off" was never a useful
    default; it was chosen only because pathlib-next 0.9.7's bound keyed on
    every directory seen and therefore dropped a directory deliberately
    reachable under two names. 0.9.9 keys on the current **descent path**
    instead (this project asked for that rule), so the cost is gone and the
    bound is on. `bound_loops=False` still buys pathlib's own walk.

    Windows only, because only a junction is descended at all — see
    `_LINKS_ARE_DESCENDED` and `TestPosixSymlinksAreNotDescended`.
    """

    def test_loop_is_bounded_by_default(self, looped_tree):
        loader = ConfigLoader(base_dir=str(looped_tree), recursive=True)
        assert loader.load("**/*.json") == {"a": 1, "b": 2}
        assert list(loader.load_all("**/*.json")) == [{"a": 1}, {"b": 2}]

    def test_bound_loops_false_walks_the_loop_until_it_fails(self, looped_tree):
        # The hazard the default exists to avoid, kept under test so it stays
        # measured rather than remembered: WinError 1921 on a ~4000-character
        # path, after merging the loop's files once per lap.
        loader = ConfigLoader(
            base_dir=str(looped_tree), recursive=True, bound_loops=False
        )
        with pytest.raises(OSError):
            loader.load("**/*.json")

    def test_bounding_keeps_a_directory_named_twice(self, twice_named_tree):
        from yaconfiglib.utils.source import Path as SourcePath

        matches = list(
            parse_sources(
                ["**/*.json"],
                base_dir=SourcePath(str(twice_named_tree)),
                recursive=True,
            )
        )
        # Both junctions onto one shared directory are read. This is the test
        # that pins the floor: on pathlib-next 0.9.7/0.9.8 the bound dropped
        # `site-b` and this returned two names, which is why `>=0.9.9` is a
        # load-bearing bound and not housekeeping.
        assert sorted(p.name for p in matches) == [
            "common.json",
            "common.json",
            "own.json",
        ]

    def test_bound_loops_false_keeps_it_too(self, twice_named_tree):
        from yaconfiglib.utils.source import Path as SourcePath

        matches = list(
            parse_sources(
                ["**/*.json"],
                base_dir=SourcePath(str(twice_named_tree)),
                recursive=True,
                bound_loops=False,
            )
        )
        # So the two settings now differ on loops ONLY, which is the whole
        # point of the upstream change.
        assert sorted(p.name for p in matches) == [
            "common.json",
            "common.json",
            "own.json",
        ]


@pytest.mark.skipif(
    _LINKS_ARE_DESCENDED, reason="Windows junctions ARE descended; see the class above"
)
class TestPosixSymlinksAreNotDescended:
    """On POSIX a ``**`` never enters a symlinked directory, so it cannot loop.

    The other half of the platform split, pinned so a change upstream is
    visible here rather than only in the documentation: pathlib-next defaults
    to ``recurse_symlinks=False`` and raises `NotImplementedError` for
    ``True``, which means `bound_loops` has nothing to bound on this side.
    """

    def test_symlinked_directory_contributes_nothing(self, twice_named_tree):
        from yaconfiglib.utils.source import Path as SourcePath

        matches = list(
            parse_sources(
                ["**/*.json"],
                base_dir=SourcePath(str(twice_named_tree)),
                recursive=True,
            )
        )
        # Neither site name is entered, so the shared layer is invisible to a
        # recursive glob — pass the shared directory as its own source instead.
        assert [p.name for p in matches] == ["own.json"]

    def test_symlinked_loop_neither_raises_nor_repeats(self, looped_tree):
        loader = ConfigLoader(base_dir=str(looped_tree), recursive=True)
        assert loader.load("**/*.json") == {"a": 1, "b": 2}
