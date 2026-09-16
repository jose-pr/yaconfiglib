"""Source resolution: which objects can be a source, and how globs expand."""

import json
import pathlib

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
