"""
Plain-Markdown agent memory (Phase G).

One file per day under ``memory/YYYY-MM-DD.md``. Each entry is a single
``##`` section:

    ## 2026-05-18 14:32:11  [tag-a, tag-b]
    Free-form Markdown body until the next ``## `` header.

Design choices (see ADR-0019):

* Files are plain Markdown so the user can ``cat`` / edit them by hand.
* No database. Search is stdlib substring; if the corpus ever grows past
  a few MB we'll swap in ripgrep — same shape of result.
* Entries are appended atomically (open + write + flush + close in a
  single ``with`` block); the file is not locked because at most one
  MCP server runs at a time and Markdown append is monotonic.
* Headers are the *only* parse-relevant lines. A body line that starts
  with ``## `` would split an entry; we therefore reject leading
  ``## `` in ``text`` at append time rather than escaping at parse time
  (parse stays trivial; the failure is loud and immediate).

The module is dependency-free (stdlib only) and never imports from
``platform/`` — memory is just text on disk, not a desktop capability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .paths import MEMORY_DIR

# A header line: "## YYYY-MM-DD HH:MM:SS  [tag, tag]" or without tags.
# Capture: 1=timestamp, 2=tags blob (may be None).
_HEADER_RE = re.compile(
    r"^##\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\s+\[([^\]]*)\])?\s*$"
)

# Reject body text that would itself look like a header (would split the
# entry on next parse). We catch ``## `` at the start of any line.
_BAD_BODY_RE = re.compile(r"(?m)^##\s")


@dataclass(frozen=True)
class MemoryEntry:
    """One ``##`` block in a memory file.

    ``ts`` is the parsed timestamp; ``tags`` is the parsed tag tuple
    (possibly empty); ``body`` is everything after the header, stripped
    of leading/trailing whitespace; ``file`` is the absolute path of the
    source ``.md`` so callers can show the user where it came from.
    """

    ts: datetime
    tags: tuple[str, ...]
    body: str
    file: Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _ensure_dir() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _file_for(day: datetime) -> Path:
    return MEMORY_DIR / f"{day:%Y-%m-%d}.md"


def _format_header(ts: datetime, tags: tuple[str, ...]) -> str:
    base = f"## {ts:%Y-%m-%d %H:%M:%S}"
    if tags:
        return f"{base}  [{', '.join(tags)}]"
    return base


def _parse_tags(blob: str | None) -> tuple[str, ...]:
    if not blob:
        return ()
    parts = [t.strip() for t in blob.split(",")]
    return tuple(p for p in parts if p)


def _iter_files(*, newest_first: bool = True) -> list[Path]:
    """Return every ``YYYY-MM-DD.md`` in ``MEMORY_DIR``.

    Sort is alphabetical, which on the ISO date naming scheme is also
    chronological. Missing dir → empty list (never raises).
    """
    if not MEMORY_DIR.exists():
        return []
    files = [p for p in MEMORY_DIR.iterdir() if _is_day_file(p)]
    files.sort(reverse=newest_first)
    return files


def _is_day_file(p: Path) -> bool:
    if not p.is_file() or p.suffix != ".md":
        return False
    # Strict YYYY-MM-DD stem AND a real calendar date so we ignore
    # stray notes like "2026-13-99.md".
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem):
        return False
    try:
        datetime.strptime(p.stem, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _parse_file(path: Path) -> list[MemoryEntry]:
    """Parse all entries from a single ``.md`` file in file order.

    Lines that don't match the header pattern before the first valid
    header are dropped (they can't belong to any entry). Empty bodies
    are allowed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    entries: list[MemoryEntry] = []
    current_ts: datetime | None = None
    current_tags: tuple[str, ...] = ()
    body_lines: list[str] = []

    def flush() -> None:
        if current_ts is None:
            return
        entries.append(
            MemoryEntry(
                ts=current_ts,
                tags=current_tags,
                body="\n".join(body_lines).strip("\n"),
                file=path,
            )
        )

    for line in text.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            flush()
            try:
                current_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                # Malformed timestamp: skip this header, keep collecting
                # the *previous* entry (which we already flushed) — so
                # reset state and drop subsequent lines until next header.
                current_ts = None
                current_tags = ()
                body_lines = []
                continue
            current_tags = _parse_tags(m.group(2))
            body_lines = []
        else:
            if current_ts is not None:
                body_lines.append(line)
    flush()
    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def append(
    text: str,
    *,
    tags: tuple[str, ...] = (),
    when: datetime | None = None,
) -> MemoryEntry:
    """Append a new entry to today's memory file.

    Raises ``ValueError`` on:

    * empty / whitespace-only ``text``;
    * a tag containing ``,`` or ``]`` (would break the header parse);
    * a body line that itself starts with ``## `` (would split the
      entry on next read).

    Returns the parsed ``MemoryEntry`` for the caller's convenience.
    Atomic enough for our single-writer use: ``open(..., 'a')`` with
    a context manager flushes on close.
    """
    body = text.strip()
    if not body:
        raise ValueError("memory.append: text is empty after strip()")
    for t in tags:
        if "," in t or "]" in t:
            raise ValueError(f"memory.append: invalid tag {t!r} (no ',' or ']')")
    if _BAD_BODY_RE.search(body):
        raise ValueError(
            "memory.append: body contains a line starting with '## ' which "
            "would split the entry; indent it or use a different prefix"
        )

    ts = when or datetime.now().replace(microsecond=0)
    path = _file_for(ts)
    _ensure_dir()

    header = _format_header(ts, tags)
    # Always lead with a blank line so consecutive entries stay readable
    # when concatenated. New files get one stray blank at the top —
    # harmless and Markdown-correct.
    chunk = f"\n{header}\n{body}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(chunk)

    return MemoryEntry(ts=ts, tags=tuple(tags), body=body, file=path)


def search(query: str, *, limit: int = 20) -> list[MemoryEntry]:
    """Substring search over every entry, newest first.

    Case-insensitive. Matches if ``query`` appears in either the body
    or any tag. Empty query → ``[]`` (no "return everything" footgun).
    """
    q = query.strip().lower()
    if not q:
        return []

    out: list[MemoryEntry] = []
    for path in _iter_files(newest_first=True):
        # Within a file, walk newest first too.
        for entry in reversed(_parse_file(path)):
            if q in entry.body.lower() or any(q in t.lower() for t in entry.tags):
                out.append(entry)
                if len(out) >= limit:
                    return out
    return out


def recent(n: int = 10) -> list[MemoryEntry]:
    """Return the most recent ``n`` entries across all days, newest first."""
    if n <= 0:
        return []
    out: list[MemoryEntry] = []
    for path in _iter_files(newest_first=True):
        for entry in reversed(_parse_file(path)):
            out.append(entry)
            if len(out) >= n:
                return out
    return out


def list_days() -> list[str]:
    """Return every day that has at least one ``.md`` file, newest first.

    Strings are ``YYYY-MM-DD``. Useful for the agent to orient itself
    ("what days do I have notes for?") without slurping bodies.
    """
    return [p.stem for p in _iter_files(newest_first=True)]
