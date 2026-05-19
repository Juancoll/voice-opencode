"""
Thin client for the local ``opencode serve`` HTTP API.

Two surfaces:

* ``health()`` — quick liveness check used by the tray and the pipeline.
* ``Session`` — wraps ``GET/POST /session`` and ``POST /session/<id>/message``.

Why a class? The session id needs to persist across CLI invocations
(``REC stop`` runs in a new process), so we read/write the id from
``SESSION_FILE`` rather than holding it in memory.
"""

from __future__ import annotations

from pathlib import Path

import requests

from .config import settings
from .logging import log
from .paths import SESSION_FILE
from .screenshot import to_data_url


def health() -> bool:
    """True if the opencode HTTP server answers 2xx within 2s."""
    try:
        r = requests.get(f"{settings.opencode_url}/global/health", timeout=2)
        return r.ok
    except Exception:
        return False


# ---------------------------------------------------------------------------
class Session:
    """Persistent opencode chat session."""

    def __init__(self, sid: str) -> None:
        self.id = sid

    # -- factories ----------------------------------------------------------
    @classmethod
    def get_or_create(cls) -> Session:
        """
        Return a usable session. Honours ``settings.keep_context``:
        if disabled, drops the cached id and starts fresh.
        """
        if not health():
            raise RuntimeError(
                f"opencode server not reachable at {settings.opencode_url}. "
                "Start it with: systemctl --user start opencode-serve"
            )
        if not settings.keep_context:
            SESSION_FILE.unlink(missing_ok=True)

        if SESSION_FILE.exists():
            sid = SESSION_FILE.read_text().strip()
            try:
                r = requests.get(f"{settings.opencode_url}/session/{sid}", timeout=5)
                if r.ok:
                    return cls(sid)
            except Exception:
                pass

        r = requests.post(
            f"{settings.opencode_url}/session",
            json={"title": "voice"},
            timeout=10,
        )
        r.raise_for_status()
        sid = r.json()["id"]
        SESSION_FILE.write_text(sid)
        log(f"Created opencode session: {sid}")
        return cls(sid)

    @staticmethod
    def forget() -> None:
        """Delete the cached session id; next call creates a fresh one."""
        SESSION_FILE.unlink(missing_ok=True)

    @staticmethod
    def current_id() -> str | None:
        if SESSION_FILE.exists():
            return SESSION_FILE.read_text().strip() or None
        return None

    # -- messages -----------------------------------------------------------
    def ask(self, prompt: str, screenshot: Path | None = None) -> str:
        """
        Send a user message; return the concatenated text reply.

        The screenshot, if given, is attached as a base64 data URL so we
        don't have to host a file server.
        """
        parts: list[dict] = [{"type": "text", "text": prompt}]
        if screenshot is not None and screenshot.exists():
            parts.append({
                "type": "file",
                "mime": "image/png",
                "filename": "screen.png",
                "url": to_data_url(screenshot),
            })
            log(f"Attaching screenshot ({screenshot.stat().st_size} bytes).")

        log(f"POST /session/{self.id}/message")
        r = requests.post(
            f"{settings.opencode_url}/session/{self.id}/message",
            json={"parts": parts},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        chunks: list[str] = [
            p["text"] for p in data.get("parts", [])
            if p.get("type") == "text" and p.get("text")
        ]
        return "\n".join(chunks).strip()
