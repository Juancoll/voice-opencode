"""Tests for the Tesseract OCR backend (linux_ocr_tesseract).

We mock ``subprocess.run`` so tests don't need a real ``tesseract``
binary on the test host. The TSV parser and the multi-word match
algorithm are exercised in detail — those are the parts that have a
non-trivial chance of regressing.

One integration test exercises the live backend (skipped if
``tesseract`` is not on PATH) so we know the wiring + invocation
keep working end-to-end.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from voice_opencode.backends.linux_ocr_tesseract import tesseract_backend as tb
from voice_opencode.platform import capabilities as cap
from voice_opencode.platform.base import BackendError
from voice_opencode.platform.types import OcrMatch, Rect


# ---------------------------------------------------------------------------
# init + capabilities
# ---------------------------------------------------------------------------
class TestInit:
    def test_missing_binary_raises(self, monkeypatch):
        monkeypatch.setattr(tb.shutil, "which", lambda _: None)
        with pytest.raises(BackendError, match="not found"):
            tb.TesseractOCRBackend()

    def test_capabilities(self):
        if not shutil.which("tesseract"):
            pytest.skip("tesseract not installed")
        b = tb.TesseractOCRBackend()
        assert b.capabilities() == frozenset(
            {cap.OCR_FIND_TEXT, cap.OCR_DUMP_TEXT}
        )

    def test_default_languages_from_config(self, monkeypatch):
        monkeypatch.setattr(tb.shutil, "which", lambda _: "/usr/bin/tesseract")
        # Default Settings come from config.settings.ocr_languages.
        b = tb.TesseractOCRBackend()
        from voice_opencode import config
        assert b._default_langs == config.settings.ocr_languages

    def test_explicit_languages(self, monkeypatch):
        monkeypatch.setattr(tb.shutil, "which", lambda _: "/usr/bin/tesseract")
        b = tb.TesseractOCRBackend(languages=("fra",))
        assert b._default_langs == ("fra",)


# ---------------------------------------------------------------------------
# TSV parsing
# ---------------------------------------------------------------------------
class TestParseTsv:
    def test_empty_input(self):
        assert tb._parse_tsv("") == []
        assert tb._parse_tsv("   \n  ") == []

    def test_header_only_no_rows(self):
        hdr = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
               "left\ttop\twidth\theight\tconf\ttext\n")
        assert tb._parse_tsv(hdr) == []

    def test_single_word(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
            "left\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t95.5\tHello\n"
        )
        out = tb._parse_tsv(tsv)
        assert len(out) == 1
        assert out[0].text == "Hello"
        assert out[0].rect == Rect(x=10, y=20, w=30, h=40)
        assert out[0].confidence == pytest.approx(95.5)
        assert out[0].line == 1 * 1000 + 1

    def test_non_word_levels_skipped(self):
        """Tesseract emits level 1..5 — only level 5 is a word."""
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
            "left\ttop\twidth\theight\tconf\ttext\n"
            "1\t1\t1\t0\t0\t0\t0\t0\t1000\t1000\t-1\t\n"
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t95\tword\n"
            "4\t1\t1\t1\t1\t0\t10\t20\t30\t40\t-1\t\n"
        )
        out = tb._parse_tsv(tsv)
        assert [w.text for w in out] == ["word"]

    def test_empty_text_skipped(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
            "left\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t90\t   \n"
            "5\t1\t1\t1\t1\t2\t10\t20\t30\t40\t90\treal\n"
        )
        assert [w.text for w in tb._parse_tsv(tsv)] == ["real"]

    def test_negative_confidence_skipped(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
            "left\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t-1\tnoisy\n"
            "5\t1\t1\t1\t1\t2\t10\t20\t30\t40\t90\tgood\n"
        )
        assert [w.text for w in tb._parse_tsv(tsv)] == ["good"]

    def test_malformed_row_skipped(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
            "left\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\tx\t1\t1\t1\t10\t20\t30\t40\t90\toops\n"
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t90\tok\n"
        )
        assert [w.text for w in tb._parse_tsv(tsv)] == ["ok"]

    def test_line_encoding_block_x_1000_plus_line(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
            "left\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t2\t1\t3\t1\t0\t0\t1\t1\t90\ta\n"
            "5\t1\t5\t1\t1\t1\t0\t0\t1\t1\t90\tb\n"
        )
        out = tb._parse_tsv(tsv)
        # block 2, line 3 → 2003; block 5, line 1 → 5001
        assert [w.line for w in out] == [2003, 5001]


# ---------------------------------------------------------------------------
# union helper
# ---------------------------------------------------------------------------
class TestUnionRects:
    def test_single_rect(self):
        r = Rect(x=10, y=20, w=30, h=40)
        assert tb._union_rects([r]) == r

    def test_two_disjoint_rects(self):
        r1 = Rect(x=0,  y=0,  w=10, h=10)
        r2 = Rect(x=50, y=20, w=10, h=10)
        # Union: x=0, y=0, w=60, h=30 (right edge=60, bottom=30)
        u = tb._union_rects([r1, r2])
        assert u == Rect(x=0, y=0, w=60, h=30)

    def test_nested_rect_doesnt_grow(self):
        big   = Rect(x=0, y=0, w=100, h=100)
        small = Rect(x=10, y=10, w=10, h=10)
        assert tb._union_rects([big, small]) == big


# ---------------------------------------------------------------------------
# Substring matching (the meat of the backend)
# ---------------------------------------------------------------------------
def _w(text, x=0, y=0, w=10, h=10, conf=90.0, line=1001, idx=0):
    return OcrMatch(
        text=text, rect=Rect(x=x, y=y, w=w, h=h),
        confidence=conf, line=line, word_index=idx,
    )


class TestMatchNeedle:
    def test_empty_needle_returns_all(self):
        words = [_w("a"), _w("b")]
        assert tb._match_needle(words, "") == words

    def test_no_match_empty_list(self):
        words = [_w("hello"), _w("world")]
        assert tb._match_needle(words, "xyz") == []

    def test_single_word_match(self):
        words = [_w("Chrome", idx=0, conf=92),
                 _w("|", idx=1, conf=50),
                 _w("Google", idx=2, conf=88)]
        out = tb._match_needle(words, "Chrome")
        assert len(out) == 1
        assert out[0].text == "Chrome"
        assert out[0].confidence == 92

    def test_case_insensitive(self):
        words = [_w("CHROME", idx=0)]
        assert tb._match_needle(words, "chrome")[0].text == "CHROME"

    def test_substring_inside_word(self):
        words = [_w("opencode", idx=0)]
        assert tb._match_needle(words, "code")[0].text == "opencode"

    def test_multi_word_needle_trims_leading(self):
        """The trimming pass should drop unrelated leading words."""
        words = [
            _w("Foo",     idx=0),
            _w("Bar",     idx=1),
            _w("Save",    idx=2),
            _w("As...",   idx=3),
        ]
        out = tb._match_needle(words, "Save As")
        assert len(out) == 1
        assert out[0].text == "Save As..."

    def test_multi_word_needle_trims_trailing(self):
        words = [
            _w("Save",  idx=0),
            _w("As",    idx=1),
            _w("File",  idx=2),
            _w("Now",   idx=3),
        ]
        out = tb._match_needle(words, "Save As")
        assert out[0].text == "Save As"

    def test_each_line_separate(self):
        """Matches cannot span across lines."""
        words = [
            _w("Save",  line=1001, idx=0),
            _w("As",    line=1002, idx=0),
        ]
        assert tb._match_needle(words, "Save As") == []

    def test_dedup_same_span(self):
        """Matching the same span starting from different i must not dup."""
        words = [
            _w("the",   idx=0),
            _w("Chrome", idx=1, conf=92),
        ]
        out = tb._match_needle(words, "Chrome")
        assert len(out) == 1

    def test_reading_order(self):
        """Output sorted by line, then word_index."""
        words = [
            _w("foo", line=2001, idx=0),
            _w("foo", line=1001, idx=5),
            _w("foo", line=1001, idx=2),
        ]
        out = tb._match_needle(words, "foo")
        assert [(m.line, m.word_index) for m in out] == [
            (1001, 2), (1001, 5), (2001, 0),
        ]

    def test_confidence_is_minimum_of_span(self):
        words = [
            _w("Save", idx=0, conf=95),
            _w("As",   idx=1, conf=60),
        ]
        assert tb._match_needle(words, "Save As")[0].confidence == 60

    def test_pruning_does_not_hang(self):
        """The 'needle*4' prune must terminate even on pathological input.

        Regression test for an early version that looped forever on
        long lines that never contained the needle.
        """
        words = [_w(f"w{i}", idx=i) for i in range(500)]
        # Needle never appears.
        out = tb._match_needle(words, "needle-not-present")
        assert out == []


# ---------------------------------------------------------------------------
# End-to-end with mocked subprocess
# ---------------------------------------------------------------------------
class TestRunTesseract:
    def _mk(self, monkeypatch):
        monkeypatch.setattr(tb.shutil, "which", lambda _: "/usr/bin/tesseract")
        return tb.TesseractOCRBackend()

    def test_missing_image_raises(self, monkeypatch):
        b = self._mk(monkeypatch)
        with pytest.raises(BackendError, match="image not found"):
            b.find_text(Path("/nonexistent.png"), "x")

    def test_subprocess_invocation(self, monkeypatch, tmp_path):
        b = self._mk(monkeypatch)
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        captured = {}
        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["kw"] = kw
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=(
                    "level\tpage_num\tblock_num\tpar_num\tline_num\t"
                    "word_num\tleft\ttop\twidth\theight\tconf\ttext\n"
                    "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t95\tHello\n"
                ),
                stderr="",
            )
        monkeypatch.setattr(tb.subprocess, "run", fake_run)
        out = b.find_text(img, "Hello", languages=("eng",))
        assert len(out) == 1
        assert out[0].text == "Hello"
        # Check the command line
        assert captured["cmd"][0] == "tesseract"
        assert str(img) in captured["cmd"]
        assert "-l" in captured["cmd"]
        assert "eng" in captured["cmd"]
        assert "tsv" in captured["cmd"]
        # OMP_THREAD_LIMIT to keep CPU sane
        assert captured["kw"]["env"]["OMP_THREAD_LIMIT"] == "1"

    def test_subprocess_nonzero_raises(self, monkeypatch, tmp_path):
        b = self._mk(monkeypatch)
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="boom\nfailed to load image\n",
            )
        monkeypatch.setattr(tb.subprocess, "run", fake_run)
        with pytest.raises(BackendError, match="rc=1"):
            b.find_text(img, "anything")

    def test_subprocess_timeout_raises(self, monkeypatch, tmp_path):
        b = self._mk(monkeypatch)
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        def fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
        monkeypatch.setattr(tb.subprocess, "run", fake_run)
        with pytest.raises(BackendError, match="timed out"):
            b.find_text(img, "x")

    def test_min_confidence_filter(self, monkeypatch, tmp_path):
        b = self._mk(monkeypatch)
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=(
                    "level\tpage_num\tblock_num\tpar_num\tline_num\t"
                    "word_num\tleft\ttop\twidth\theight\tconf\ttext\n"
                    "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t30\tnoisy\n"
                    "5\t1\t1\t1\t1\t2\t10\t20\t30\t40\t80\tclean\n"
                ),
                stderr="",
            )
        monkeypatch.setattr(tb.subprocess, "run", fake_run)
        # Default min_confidence=50 → only "clean" survives
        out = b.dump_text(img)
        assert [w.text for w in out] == ["clean"]
        # Lower threshold → both survive
        out2 = b.dump_text(img, min_confidence=10)
        assert [w.text for w in out2] == ["noisy", "clean"]
