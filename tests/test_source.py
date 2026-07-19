"""Tests for parse_sources edge cases: streams, in-memory docs, defaults."""
import io

from yaconfiglib.utils.source import parse_sources


class TestStreamAndMemorySources:
    def test_streams_get_unique_paths(self):
        s1 = io.StringIO("a: 1\n")
        s2 = io.StringIO("b: 2\n")
        paths = list(parse_sources([s1, s2], encoding="utf-8"))
        assert len(paths) == 2
        # Regression: both streams used to materialize to the SAME virtual
        # path ("stream"), so resolving up front left every path holding the
        # last stream's content.
        assert str(paths[0]) != str(paths[1])
        assert paths[0].read_text(encoding="utf-8") == "a: 1\n"
        assert paths[1].read_text(encoding="utf-8") == "b: 2\n"

    def test_unnamed_memory_docs_get_unique_paths(self):
        docs = ["#!\nx: 1\n", "#!\ny: 2\n"]
        paths = list(parse_sources(docs, encoding="utf-8"))
        assert len(paths) == 2
        assert str(paths[0]) != str(paths[1])
        assert paths[0].read_text(encoding="utf-8") == "x: 1\n"
        assert paths[1].read_text(encoding="utf-8") == "y: 2\n"

    def test_bytes_doc_without_encoding_defaults_utf8(self):
        # Previously "#!".encode(None) raised TypeError before any parsing.
        paths = list(parse_sources([b"#!\nk: v\n"]))
        assert len(paths) == 1
        assert b"k: v" in paths[0].read_bytes()


class TestMemoNormalization:
    def test_list_memo_still_dedupes(self, tmp_path):
        # Backward-compat: a caller passing a list is accepted (normalized to a
        # set); duplicate detection still works.
        (tmp_path / "a.yaml").write_text("x: 1\n")
        seen = ["already-there"]
        paths = list(parse_sources(["a.yaml", "a.yaml"], base_dir=tmp_path, memo=seen))
        assert len(paths) == 1  # second occurrence deduped
