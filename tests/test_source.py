"""Tests for parse_sources edge cases: streams, in-memory docs, defaults."""

import io

import pytest

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


@pytest.mark.usefixtures("needs_yaml")
class TestStreamsThroughLoad:
    def test_stringio_and_bytesio_load(self):
        import io

        from yaconfiglib import ConfigLoader

        loader = ConfigLoader()
        assert loader.load(io.StringIO("x: 1\n")) == {"x": 1}
        assert loader.load(io.BytesIO(b"y: 2\n")) == {"y": 2}

    def test_named_bytes_memory_doc(self):
        from yaconfiglib import ConfigLoader

        loader = ConfigLoader()
        assert loader.load(b"#!doc.yaml\nk: v\n") == {"k": "v"}


LF = chr(10)
CRLF = chr(13) + LF

#: Multi-line YAML in Windows line endings: a block scalar, a multi-line
#: quoted value and a folded scalar. Assembled from parts rather than written
#: with escapes, because the exact line endings ARE the fixture.
DOC = CRLF.join(
    [
        "msg: |",
        "  hello",
        "  world",
        'q: "a',
        "  b" + chr(34),
        "f: >",
        "  a",
        "  b",
        "",
    ]
)


@pytest.mark.usefixtures("needs_yaml")
class TestInMemoryTextMaterialization:
    """In-memory text is stored as bytes, in a codec that can hold it.

    Writing it through a text-mode file was both silently lossy (Windows line
    endings gained a blank line per break, and no ``encoding=`` meant the
    locale codec) and loudly fatal (text the requested codec could not
    represent raised, though it was already decoded).
    """

    def test_crlf_multiline_scalars_via_str_doc(self):
        import yaml

        from yaconfiglib import ConfigLoader

        assert ConfigLoader().load("#!" + LF + DOC) == yaml.safe_load(DOC)

    def test_crlf_multiline_scalars_via_text_stream(self):
        import yaml

        from yaconfiglib import ConfigLoader

        assert ConfigLoader().load(io.StringIO(DOC)) == yaml.safe_load(DOC)

    def test_materialized_text_keeps_bytes_verbatim(self):
        path = list(parse_sources(["#!d.yaml" + LF + DOC]))[0]
        assert path.read_bytes() == DOC.encode("utf-8")

    def test_str_doc_text_outside_loader_codec(self):
        from yaconfiglib import ConfigLoader

        # Already-decoded text: the loader's codec says how to read FILES, and
        # cannot make this document unrepresentable.
        loader = ConfigLoader(encoding="ascii")
        assert loader.load("#!" + LF + "a: café" + LF) == {"a": "café"}

    def test_text_stream_outside_loader_codec(self):
        from yaconfiglib import ConfigLoader

        loader = ConfigLoader(encoding="cp1252")
        assert loader.load(io.StringIO("name: 日本" + LF)) == {"name": "日本"}

    def test_parse_sources_default_codec_is_utf8(self):
        from yaconfiglib.backends.yaml import YamlConfig

        # No encoding= at all: storage is UTF-8 on every platform, where it
        # used to be the locale codec (cp1252 here).
        path = list(parse_sources(["#!d.yaml" + LF + "k: 日本 café" + LF]))[0]
        assert YamlConfig().load(path) == {"k": "日本 café"}

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("#!x.json" + CRLF + '{"a": 1}', id="named_str"),
            pytest.param(
                ("#!x.json" + CRLF + '{"a": 1}').encode("utf-8"), id="named_bytes"
            ),
            pytest.param("#!" + CRLF + "a: 1", id="unnamed_str"),
        ],
    )
    def test_marker_name_whitespace_is_stripped(self, source):
        from yaconfiglib import ConfigLoader

        # A CRLF marker line used to leave "x.json\r" as the virtual name, so
        # backend detection failed with "Not reader for name".
        assert ConfigLoader().load(source) == {"a": 1}

    @pytest.mark.parametrize("codec", ["utf-8-sig", "utf-16", "utf-32"])
    def test_bytes_doc_marker_in_any_codec(self, codec):
        from yaconfiglib import ConfigLoader

        # These codecs spell "#!" with a BOM or in two bytes per character, so
        # the marker split found no separator and raised ValueError.
        source = ("#!" + LF + "a: 1" + LF).encode(codec)
        assert ConfigLoader().load(source, encoding=codec) == {"a": 1}

    def test_temp_fallback_text_doc(self, monkeypatch):
        import yaconfiglib.utils.source as source_mod
        from yaconfiglib import ConfigLoader

        monkeypatch.setattr(source_mod, "MemPath", None)
        doc = "#!" + LF + "msg: |" + CRLF + "  日本" + CRLF
        assert ConfigLoader(encoding="cp1252").load(doc) == {"msg": "日本" + LF}

    def test_bytes_doc_decoded_with_loader_codec(self):
        from yaconfiglib import ConfigLoader

        source = ("#!" + LF + "a: café" + LF).encode("cp1252")
        assert ConfigLoader(encoding="cp1252").load(source) == {"a": "café"}

    def test_bytes_source_without_marker_raises_type_error(self):
        from yaconfiglib import ConfigLoader

        with pytest.raises(TypeError):
            ConfigLoader().load(b"a: 1" + LF.encode("ascii"))

    def test_named_doc_keeps_virtual_name(self):
        path = list(parse_sources(["#!x.yaml" + LF + "a: 1" + LF]))[0]
        assert path.name == "x.yaml"
        assert str(path) == "x.yaml"

    def test_parse_sources_yields_paths(self):
        sources = [
            "#!" + LF + "a: 1" + LF,
            ("#!" + LF + "b: 2" + LF).encode("utf-8"),
            io.StringIO("c: 3" + LF),
        ]
        items = list(parse_sources(sources))
        assert len(items) == 3
        # The public generator keeps yielding bare items; only the private one
        # carries the read codec alongside.
        assert not any(isinstance(item, tuple) for item in items)
        assert all(hasattr(item, "read_bytes") for item in items)

    @pytest.mark.parametrize("where", ["constructor", "per_call"])
    def test_include_inside_text_stream_uses_loader_codec(self, tmp_path, where):
        from yaconfiglib import ConfigLoader

        (tmp_path / "sub.yaml").write_bytes(("inner: café" + LF).encode("cp1252"))
        stream = io.StringIO("sub: !include sub.yaml" + LF)
        if where == "constructor":
            loader = ConfigLoader(base_dir=str(tmp_path), encoding="cp1252")
            result = loader.load(stream)
        else:
            loader = ConfigLoader(base_dir=str(tmp_path))
            result = loader.load(stream, encoding="cp1252")
        assert result == {"sub": {"inner": "café"}}

    def test_bytes_doc_content_stored_verbatim(self):
        payload = bytes([0xFF, 0x00, 0xFE])
        path = list(parse_sources([b"#!x.bin" + LF.encode("ascii") + payload]))[0]
        # An ASCII-compatible codec must not decode a byte payload: a
        # byte-oriented backend reads these bytes back as they were given.
        assert path.read_bytes() == payload
