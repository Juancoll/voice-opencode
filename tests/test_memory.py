"""Tests for the plain-Markdown memory module (Phase G).

We monkeypatch ``MEMORY_DIR`` to ``tmp_path`` per test so the suite never
touches the real ``memory/`` directory and tests stay hermetic.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from voice_opencode import memory as mem


@pytest.fixture(autouse=True)
def _isolate_memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every read/write to a fresh tmp dir."""
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# append
# ---------------------------------------------------------------------------
class TestAppend:
    def test_creates_file_and_writes_header(self, tmp_path: Path) -> None:
        when = datetime(2026, 5, 18, 14, 32, 11)
        entry = mem.append("hello world", when=when)
        f = tmp_path / "2026-05-18.md"
        assert f.exists()
        content = f.read_text()
        assert "## 2026-05-18 14:32:11" in content
        assert "hello world" in content
        assert entry.ts == when
        assert entry.body == "hello world"
        assert entry.tags == ()
        assert entry.file == f

    def test_tags_render_in_header(self, tmp_path: Path) -> None:
        when = datetime(2026, 5, 18, 9, 0, 0)
        mem.append("body", tags=("phase-g", "memory"), when=when)
        content = (tmp_path / "2026-05-18.md").read_text()
        assert "## 2026-05-18 09:00:00  [phase-g, memory]" in content

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        d = datetime(2026, 5, 18, 9, 0, 0)
        mem.append("first", when=d)
        mem.append("second", when=d.replace(hour=10))
        content = (tmp_path / "2026-05-18.md").read_text()
        assert content.count("## ") == 2
        assert content.index("first") < content.index("second")

    def test_strips_text(self) -> None:
        e = mem.append("   padded   \n", when=datetime(2026, 5, 18, 1, 0, 0))
        assert e.body == "padded"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            mem.append("   ")

    def test_rejects_tag_with_comma(self) -> None:
        with pytest.raises(ValueError, match="invalid tag"):
            mem.append("x", tags=("bad,tag",))

    def test_rejects_tag_with_bracket(self) -> None:
        with pytest.raises(ValueError, match="invalid tag"):
            mem.append("x", tags=("bad]tag",))

    def test_rejects_body_with_header_like_line(self) -> None:
        """A body line starting with '## ' would split the entry on re-parse."""
        with pytest.raises(ValueError, match="split the entry"):
            mem.append("ok line\n## fake header\nmore")

    def test_default_when_uses_now_without_microseconds(self) -> None:
        before = datetime.now().replace(microsecond=0)
        e = mem.append("x")
        after = datetime.now().replace(microsecond=0)
        assert e.ts.microsecond == 0
        assert before <= e.ts <= after


# ---------------------------------------------------------------------------
# parse round-trip
# ---------------------------------------------------------------------------
class TestParse:
    def test_round_trip_single_entry(self, tmp_path: Path) -> None:
        when = datetime(2026, 5, 18, 14, 32, 11)
        mem.append("hello", tags=("a", "b"), when=when)
        out = mem._parse_file(tmp_path / "2026-05-18.md")
        assert len(out) == 1
        assert out[0].ts == when
        assert out[0].tags == ("a", "b")
        assert out[0].body == "hello"

    def test_round_trip_multi_entry(self, tmp_path: Path) -> None:
        d = datetime(2026, 5, 18, 0, 0, 0)
        for h in range(3):
            mem.append(f"entry {h}", when=d.replace(hour=h))
        out = mem._parse_file(tmp_path / "2026-05-18.md")
        assert [e.body for e in out] == ["entry 0", "entry 1", "entry 2"]

    def test_multi_line_body_preserved(self, tmp_path: Path) -> None:
        body = "line one\nline two\n\nline four"
        when = datetime(2026, 5, 18, 12, 0, 0)
        mem.append(body, when=when)
        out = mem._parse_file(tmp_path / "2026-05-18.md")
        assert out[0].body == body

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert mem._parse_file(tmp_path / "no-such-file.md") == []

    def test_skips_lines_before_first_header(self, tmp_path: Path) -> None:
        path = tmp_path / "2026-05-18.md"
        path.write_text("stray junk\nmore junk\n\n## 2026-05-18 09:00:00\nbody\n")
        out = mem._parse_file(path)
        assert len(out) == 1
        assert out[0].body == "body"

    def test_malformed_timestamp_drops_block(self, tmp_path: Path) -> None:
        path = tmp_path / "2026-05-18.md"
        path.write_text(
            "## 2026-05-18 99:99:99\nbad\n## 2026-05-18 09:00:00\ngood\n"
        )
        out = mem._parse_file(path)
        assert [e.body for e in out] == ["good"]

    def test_empty_body_is_ok(self, tmp_path: Path) -> None:
        path = tmp_path / "2026-05-18.md"
        path.write_text("## 2026-05-18 09:00:00\n\n## 2026-05-18 10:00:00\nbody\n")
        out = mem._parse_file(path)
        assert len(out) == 2
        assert out[0].body == ""
        assert out[1].body == "body"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
class TestSearch:
    def _seed(self) -> None:
        mem.append("First note about audio", tags=("audio",),
                   when=datetime(2026, 5, 17, 9, 0, 0))
        mem.append("Second note about OCR", tags=("phase-f",),
                   when=datetime(2026, 5, 18, 9, 0, 0))
        mem.append("Third note about audio again",
                   when=datetime(2026, 5, 18, 10, 0, 0))

    def test_finds_body_substring(self) -> None:
        self._seed()
        out = mem.search("OCR")
        assert len(out) == 1
        assert "Second" in out[0].body

    def test_case_insensitive(self) -> None:
        self._seed()
        assert len(mem.search("ocr")) == 1
        assert len(mem.search("OCR")) == 1
        assert len(mem.search("OcR")) == 1

    def test_matches_tag(self) -> None:
        self._seed()
        out = mem.search("phase-f")
        assert len(out) == 1
        assert "Second" in out[0].body

    def test_results_newest_first(self) -> None:
        self._seed()
        out = mem.search("audio")
        assert len(out) == 2
        # Newest match comes first.
        assert out[0].ts > out[1].ts
        assert "Third" in out[0].body
        assert "First" in out[1].body

    def test_empty_query_returns_empty(self) -> None:
        self._seed()
        assert mem.search("") == []
        assert mem.search("   ") == []

    def test_no_matches(self) -> None:
        self._seed()
        assert mem.search("nothing-like-this") == []

    def test_limit_truncates(self) -> None:
        for h in range(5):
            mem.append("same word here", when=datetime(2026, 5, 18, h, 0, 0))
        assert len(mem.search("same", limit=3)) == 3

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        # Point at a non-existent path.
        import voice_opencode.memory as m
        m.MEMORY_DIR = tmp_path / "does-not-exist"
        assert m.search("anything") == []


# ---------------------------------------------------------------------------
# recent
# ---------------------------------------------------------------------------
class TestRecent:
    def test_returns_newest_first_across_files(self) -> None:
        mem.append("old", when=datetime(2026, 5, 17, 9, 0, 0))
        mem.append("mid", when=datetime(2026, 5, 18, 9, 0, 0))
        mem.append("new", when=datetime(2026, 5, 18, 10, 0, 0))
        out = mem.recent(2)
        assert [e.body for e in out] == ["new", "mid"]

    def test_n_larger_than_corpus(self) -> None:
        mem.append("only one", when=datetime(2026, 5, 18, 9, 0, 0))
        assert len(mem.recent(99)) == 1

    def test_zero_or_negative(self) -> None:
        mem.append("x", when=datetime(2026, 5, 18, 9, 0, 0))
        assert mem.recent(0) == []
        assert mem.recent(-1) == []

    def test_no_files(self) -> None:
        assert mem.recent(10) == []


# ---------------------------------------------------------------------------
# list_days
# ---------------------------------------------------------------------------
class TestListDays:
    def test_returns_iso_dates_newest_first(self) -> None:
        mem.append("a", when=datetime(2026, 5, 17, 9, 0, 0))
        mem.append("b", when=datetime(2026, 5, 19, 9, 0, 0))
        mem.append("c", when=datetime(2026, 5, 18, 9, 0, 0))
        assert mem.list_days() == ["2026-05-19", "2026-05-18", "2026-05-17"]

    def test_ignores_non_iso_files(self, tmp_path: Path) -> None:
        mem.append("x", when=datetime(2026, 5, 18, 9, 0, 0))
        (tmp_path / "README.md").write_text("# notes")
        (tmp_path / "scratch.txt").write_text("hello")
        (tmp_path / "2026-13-99.md").write_text("garbage")  # invalid date stem
        days = mem.list_days()
        assert days == ["2026-05-18"]

    def test_no_files(self) -> None:
        assert mem.list_days() == []
