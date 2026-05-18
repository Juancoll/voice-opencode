"""Tesseract OCR backend (linux_ocr_tesseract).

Wraps the ``tesseract`` CLI in TSV mode so we get per-word bounding
boxes, line/block grouping, and confidence in a single pass. The
backend is intentionally pure: it takes a PNG path and returns
``OcrMatch`` objects. The "find it on screen *now*" convenience lives
in the MCP server (Phase F), composed as
``screen.capture_monitor()`` + ``ocr.find_text()``.

Design notes:

* TSV is the only Tesseract output that gives us bboxes + confidence
  per word *and* the line/block grouping we need to chain consecutive
  words into multi-word matches.
* We invoke tesseract once per call. No process pool — Tesseract
  warm-start is < 100 ms and screenshots are not bursty.
* Failures (missing binary, exit ≠ 0) raise ``BackendError`` with the
  stderr tail. A successful run that produces zero matches returns
  an empty list — not an error.
* Multi-word matching: we walk every line and look for the needle in
  the joined word sequence; when found, we take the union of the
  matched words' rects and the minimum of their confidences. This
  way "Save As..." matches even if Tesseract split it into three
  separate tokens.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
from pathlib import Path

from ... import config
from ...platform import capabilities as cap
from ...platform.base import BackendError
from ...platform.types import OcrMatch, Rect

_TSV_TIMEOUT_S = 60.0          # Tesseract on a 4K screenshot ≈ 5–30 s
                               # depending on hardware. Cap conservatively;
                               # callers in a hurry should capture a region.
_PSM = "6"                     # "Assume a single uniform block of text"
                               # — good middle ground for full screens.


class TesseractOCRBackend:
    """``tesseract`` wrapper. See module docstring."""

    def __init__(
        self,
        languages: tuple[str, ...] | None = None,
        min_confidence: float | None = None,
    ) -> None:
        if not shutil.which("tesseract"):
            raise BackendError("tesseract binary not found in PATH")
        self._default_langs = tuple(languages
                                    or config.settings.ocr_languages)
        self._default_min_conf = float(min_confidence
                                       if min_confidence is not None
                                       else config.settings.ocr_min_confidence)

    def capabilities(self) -> frozenset[str]:
        return frozenset({cap.OCR_FIND_TEXT, cap.OCR_DUMP_TEXT})

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    def find_text(
        self,
        image_path: Path,
        needle: str,
        languages: tuple[str, ...] | None = None,
        min_confidence: float = 50.0,
    ) -> list[OcrMatch]:
        words = self._run_tesseract(image_path, languages)
        words = [w for w in words if w.confidence >= min_confidence]
        if not needle:
            return words
        return _match_needle(words, needle)

    def dump_text(
        self,
        image_path: Path,
        languages: tuple[str, ...] | None = None,
        min_confidence: float = 50.0,
    ) -> list[OcrMatch]:
        words = self._run_tesseract(image_path, languages)
        return [w for w in words if w.confidence >= min_confidence]

    # ------------------------------------------------------------------
    # Tesseract invocation
    # ------------------------------------------------------------------
    def _run_tesseract(
        self,
        image_path: Path,
        languages: tuple[str, ...] | None,
    ) -> list[OcrMatch]:
        if not image_path.exists():
            raise BackendError(f"image not found: {image_path}")
        langs = "+".join(languages or self._default_langs)
        # `-` as output base = stdout; `tsv` config asks for TSV format.
        cmd = [
            "tesseract",
            str(image_path),
            "-",
            "-l", langs,
            "--psm", _PSM,
            "tsv",
        ]
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_TSV_TIMEOUT_S,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},  # avoid CPU storm
            )
        except subprocess.TimeoutExpired:
            raise BackendError(
                f"tesseract timed out after {_TSV_TIMEOUT_S}s on {image_path}"
            ) from None
        except OSError as e:
            raise BackendError(f"tesseract spawn failed: {e}") from e
        if r.returncode != 0:
            tail = (r.stderr or "").strip().splitlines()[-3:]
            raise BackendError(
                f"tesseract rc={r.returncode}: {' | '.join(tail)}"
            )
        return _parse_tsv(r.stdout)


# ---------------------------------------------------------------------------
# TSV parsing
# ---------------------------------------------------------------------------
# Tesseract TSV columns (header line):
#   level page_num block_num par_num line_num word_num
#   left top width height conf text
# `level` is 1..5; level 5 = word. We only care about words.
_LEVEL_WORD = "5"


def _parse_tsv(tsv: str) -> list[OcrMatch]:
    """Parse Tesseract TSV stdout into word-level ``OcrMatch`` list.

    Empty/whitespace-only words are dropped. Confidence ``-1`` (which
    Tesseract emits for non-word rows) is filtered by the level check.
    Rows are returned in Tesseract's own reading order (which is
    block → paragraph → line → word, i.e. top-down + left-to-right).
    """
    if not tsv.strip():
        return []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    out: list[OcrMatch] = []
    for row in reader:
        if row.get("level") != _LEVEL_WORD:
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row["conf"])
            x = int(row["left"])
            y = int(row["top"])
            w = int(row["width"])
            h = int(row["height"])
            block = int(row["block_num"])
            line = int(row["line_num"])
            wnum = int(row["word_num"])
        except (KeyError, ValueError):
            # Malformed row — skip rather than crash the whole parse.
            continue
        if conf < 0:
            continue
        out.append(OcrMatch(
            text=text,
            rect=Rect(x=x, y=y, w=w, h=h),
            confidence=conf,
            line=block * 1000 + line,
            word_index=wnum,
        ))
    return out


# ---------------------------------------------------------------------------
# Multi-word substring matching
# ---------------------------------------------------------------------------
def _match_needle(words: list[OcrMatch], needle: str) -> list[OcrMatch]:
    """Find every span of consecutive words on the same line whose joined
    (space-separated) text contains ``needle`` (case-insensitive).

    Returns one ``OcrMatch`` per occurrence with the union of the matched
    words' rects and the minimum of their confidences. Greedy: takes
    the shortest span (fewest words) that satisfies the match — this
    avoids inflating bboxes with trailing unrelated words on the line.
    """
    needle_lc = needle.lower()
    if not needle_lc:
        return list(words)
    # Group by line first.
    by_line: dict[int, list[OcrMatch]] = {}
    for w in words:
        by_line.setdefault(w.line, []).append(w)
    for line_words in by_line.values():
        line_words.sort(key=lambda w: w.word_index)

    out: list[OcrMatch] = []
    seen: set[tuple[int, int, int]] = set()       # (line, first_i, span_len)
    for line_words in by_line.values():
        n = len(line_words)
        for i in range(n):
            matched_j = -1
            for j in range(i, n):
                joined = " ".join(w.text for w in line_words[i:j + 1]).lower()
                if needle_lc in joined:
                    matched_j = j
                    break
                # Pruning: if the joined text is already much longer
                # than the needle and still doesn't match, stop growing.
                if len(joined) > len(needle_lc) * 4:
                    break
            if matched_j < 0:
                continue
            # Trim: drop leading/trailing words that aren't needed.
            # This keeps "Chrome" instead of "E ~ 88 MQ Chrome".
            span = list(line_words[i:matched_j + 1])
            # Trim leading words while still matching.
            while len(span) > 1:
                trimmed = " ".join(w.text for w in span[1:]).lower()
                if needle_lc in trimmed:
                    span = span[1:]
                else:
                    break
            # Trim trailing words while still matching.
            while len(span) > 1:
                trimmed = " ".join(w.text for w in span[:-1]).lower()
                if needle_lc in trimmed:
                    span = span[:-1]
                else:
                    break
            key = (span[0].line, span[0].word_index, len(span))
            if key in seen:
                continue
            seen.add(key)
            out.append(OcrMatch(
                text=" ".join(w.text for w in span),
                rect=_union_rects(w.rect for w in span),
                confidence=min(w.confidence for w in span),
                line=span[0].line,
                word_index=span[0].word_index,
            ))
    # Sort matches in reading order: by line, then by first word index.
    out.sort(key=lambda m: (m.line, m.word_index))
    return out


def _union_rects(rects: object) -> Rect:
    """Bounding box union. ``rects`` is any iterable of ``Rect``."""
    rs = list(rects)                              # type: ignore[call-overload]
    x = min(r.x for r in rs)
    y = min(r.y for r in rs)
    x2 = max(r.x + r.w for r in rs)
    y2 = max(r.y + r.h for r in rs)
    return Rect(x=x, y=y, w=x2 - x, h=y2 - y)
