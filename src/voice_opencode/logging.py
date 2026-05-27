"""
Simple stderr+file logger. No third-party dep on purpose.

The standard ``logging`` module is overkill here: we want a one-line tag
and a tail-friendly file. Keep this module dependency-free so every other
module can import it without cycles.
"""

from __future__ import annotations

import sys
import time

from .paths import LOGS_DIR

_LOG_FILE = LOGS_DIR / "voice.log"


def log(msg: str) -> None:
    """Print to stderr and append to ``logs/voice.log`` with HH:MM:SS.mmm prefix.

    Milliseconds matter: streaming dictation events can fire several
    times within the same second and ordering them is the whole point
    of having a log.
    """
    now = time.time()
    ts = time.strftime("%H:%M:%S", time.localtime(now))
    ms = int((now - int(now)) * 1000)
    line = f"[{ts}.{ms:03d}] {msg}"
    print(line, file=sys.stderr, flush=True)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        # Logging must never crash the caller.
        pass
