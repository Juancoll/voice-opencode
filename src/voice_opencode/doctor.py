"""
Diagnostic suite invoked by ``voice doctor``.

This is the place where every "is everything wired correctly?" check
lives. The CLI command renders a green/yellow/red report so the user
can fix the host without reading source code.

Design:

* **Checks are pure functions** returning a ``Check`` dataclass.
  No prints, no exits — that's the CLI's job. Easy to unit-test.
* **Every check has an ``advice`` field** the user can act on.
  A red check without advice is a documentation bug.
* **No autofix.** Doctor diagnoses, the user (or installer) fixes.
  The one exception, ``opencode-serve`` restart, is gated behind a
  ``--fix`` flag passed through from the CLI.
* **Fast.** A typical run hits ~5 subprocesses + 1 HTTP probe.
  Hard cap each subprocess at 3s; total run < 15s worst case.

Severity convention:

* ``ok``    — green, nothing to do.
* ``warn``  — yellow; the user *might* care (optional feature off, old
              version). Doctor still exits 0.
* ``fail``  — red; the pipeline will not work reliably. Doctor exits 1.

The CLI groups checks and prints them in a stable order so the output
diffs cleanly between runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import requests

from . import host_info
from . import platform as _platform
from .config import settings
from .paths import CONFIG_FILE, SESSION_FILE

# How long to wait for any single subprocess invoked by a check.
_TIMEOUT_S: float = 3.0


@dataclass(frozen=True)
class Check:
    """One diagnostic result.

    ``name``     — short label, "opencode-serve health".
    ``severity`` — "ok" / "warn" / "fail".
    ``detail``   — one-line description of what was observed.
    ``advice``   — actionable hint when severity != "ok"; empty string OK
                   when nothing else to say.
    """

    name: str
    severity: str
    detail: str
    advice: str = ""


# ---------------------------------------------------------------------------
# Individual checks (one per concern)
# ---------------------------------------------------------------------------
def check_binaries() -> Iterable[Check]:
    """Yield one Check per binary the pipeline depends on at runtime.

    Each one ⇒ ``ok`` (present + version known), ``warn`` (present but
    version probe failed — usually wrong --version flag), or ``fail``
    (not on PATH).
    """
    info = host_info.get()
    required: list[tuple[str, str]] = [
        ("opencode",     info.opencode_version),
        ("whisper-cli",  info.whisper_version),
        ("piper-tts",    info.piper_version),
    ]
    optional: list[tuple[str, str]] = [
        ("hyprctl",      info.hyprland_version),
        ("wpctl",        ""),     # presence-only; no version field
        ("ydotool",      ""),
        ("grim",         ""),
        ("paplay",       ""),
        ("arecord",      ""),
    ]
    for name, version in required:
        path = shutil.which(name)
        if not path:
            yield Check(
                name=f"binary: {name}",
                severity="fail",
                detail=f"{name!r} not on PATH",
                advice=f"Install {name} (see _ai/STATE.md for the expected location).",
            )
        elif not version:
            yield Check(
                name=f"binary: {name}",
                severity="warn",
                detail=f"{name} found at {path} but version probe returned nothing",
                advice="The pipeline still works; voice doctor just can't tell you "
                       "which version is installed.",
            )
        else:
            yield Check(
                name=f"binary: {name}",
                severity="ok",
                detail=f"{name} {version} at {path}",
            )
    for name, _ in optional:
        path = shutil.which(name)
        if not path:
            yield Check(
                name=f"binary: {name}",
                severity="warn",
                detail=f"{name!r} not on PATH (optional)",
                advice=f"Some capabilities will be disabled until {name} is installed.",
            )
        else:
            yield Check(
                name=f"binary: {name}",
                severity="ok",
                detail=f"{name} present at {path}",
            )


def check_opencode_health() -> Check:
    """Probe ``GET /global/health`` on the configured opencode URL.

    Failure modes we surface separately because they suggest different
    fixes:

    * connection refused / timeout → server isn't running
    * 2xx                          → ok
    * other status                  → server replied something weird
    """
    url = f"{settings.opencode_url}/global/health"
    try:
        r = requests.get(url, timeout=_TIMEOUT_S)
    except requests.ConnectionError:
        return Check(
            name="opencode-serve",
            severity="fail",
            detail=f"cannot connect to {settings.opencode_url}",
            advice="Start it: systemctl --user start opencode-serve",
        )
    except requests.Timeout:
        return Check(
            name="opencode-serve",
            severity="fail",
            detail=f"timed out connecting to {settings.opencode_url}",
            advice="Server is unresponsive; restart it: "
                   "systemctl --user restart opencode-serve",
        )
    if r.ok:
        return Check(
            name="opencode-serve",
            severity="ok",
            detail=f"healthy at {settings.opencode_url} (status {r.status_code})",
        )
    return Check(
        name="opencode-serve",
        severity="fail",
        detail=f"{url} returned HTTP {r.status_code}",
        advice="Check the server logs: journalctl --user -u opencode-serve",
    )


def check_opencode_mcp_config() -> Check:
    """Verify that the user's opencode config declares the voice MCP server.

    This is the root cause of the 2026-05-21 incident where the model
    refused to use desktop tools — opencode-serve was started before the
    ``mcp`` block was added to its config file. We can't easily ask the
    running server what MCPs it loaded (no API for it), but we can:

    1. Parse ``~/.config/opencode/opencode.json``.
    2. Compare its mtime to the opencode-serve process start time, when
       both are available.

    The mtime comparison only runs on Linux (``/proc/<pid>``); on other
    OSes we degrade to "config looks correct, can't verify freshness".
    """
    cfg = Path.home() / ".config/opencode/opencode.json"
    if not cfg.exists():
        return Check(
            name="opencode mcp config",
            severity="warn",
            detail=f"{cfg} does not exist",
            advice="If you rely on voice_desktop MCP tools, run ./install.sh "
                   "to create the config.",
        )
    try:
        data = json.loads(cfg.read_text())
    except json.JSONDecodeError as e:
        return Check(
            name="opencode mcp config",
            severity="fail",
            detail=f"{cfg} is not valid JSON: {e}",
            advice="Fix the JSON syntax or delete the file and re-run install.sh.",
        )
    mcp = data.get("mcp") or {}
    vd = mcp.get("voice_desktop") or {}
    if not vd or not vd.get("enabled", True):
        return Check(
            name="opencode mcp config",
            severity="warn",
            detail="voice_desktop MCP not declared or disabled",
            advice='Add {"mcp":{"voice_desktop":{"type":"local",'
                   '"command":["/path/to/voice","mcp","serve"],"enabled":true}}} '
                   "to your opencode config.",
        )

    # Compare mtimes if we can find the server PID. Best-effort only.
    cfg_mtime = cfg.stat().st_mtime
    server_started = _opencode_serve_started_at()
    if server_started is not None and cfg_mtime > server_started + 1:
        # +1s slack for clock drift / fs granularity.
        return Check(
            name="opencode mcp config",
            severity="fail",
            detail=f"{cfg} was edited after opencode-serve started — "
                   "the server is running with a stale config",
            advice="Restart opencode-serve so it picks up the new MCP block: "
                   "systemctl --user restart opencode-serve",
        )

    return Check(
        name="opencode mcp config",
        severity="ok",
        detail="voice_desktop MCP declared and (where checkable) loaded",
    )


def _opencode_serve_started_at() -> float | None:
    """Return the unix start time of ``opencode serve`` if discoverable.

    Linux only: parses ``/proc/<pid>/stat`` after locating the PID with
    ``pgrep``. Returns ``None`` on any failure or non-Linux host.
    """
    try:
        r = subprocess.run(
            ["pgrep", "-fx", "opencode serve.*"],
            check=False, capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    pid = r.stdout.split()[0]
    try:
        # /proc/<pid>/stat field 22 = starttime in clock ticks since boot.
        # Easier: stat /proc/<pid> — its ctime IS the process start.
        return Path(f"/proc/{pid}").stat().st_ctime
    except OSError:
        return None


def check_session_file() -> Check:
    """The cached opencode session id, if present, must point to a
    session the server still knows about."""
    if not SESSION_FILE.exists():
        return Check(
            name="opencode session",
            severity="ok",
            detail="no cached session — next turn will create one",
        )
    sid = SESSION_FILE.read_text().strip()
    if not sid:
        return Check(
            name="opencode session",
            severity="warn",
            detail=f"{SESSION_FILE} is empty",
            advice="Run: voice session reset",
        )
    url = f"{settings.opencode_url}/session/{sid}"
    try:
        r = requests.get(url, timeout=_TIMEOUT_S)
    except requests.RequestException as e:
        return Check(
            name="opencode session",
            severity="warn",
            detail=f"cannot verify session: {e}",
            advice="opencode-serve health probably failed too; fix that first.",
        )
    if r.ok:
        return Check(
            name="opencode session",
            severity="ok",
            detail=f"session {sid} valid",
        )
    return Check(
        name="opencode session",
        severity="warn",
        detail=f"cached session {sid} returned HTTP {r.status_code}",
        advice="Run: voice session reset",
    )


def check_platform_backends() -> Check:
    """Ensure ``platform.<backend>`` resolved to a real backend, not Null."""
    info = _platform.platform_info()
    null_count = 0
    for cap in ("wm", "input", "screen", "clipboard", "tts", "stt"):
        backend = getattr(_platform, cap, None)
        if backend is None:
            null_count += 1
            continue
        if type(backend).__name__.startswith("Null"):
            null_count += 1
    if null_count == 0:
        return Check(
            name="platform backends",
            severity="ok",
            detail=f"all 6 core capabilities have a real backend "
                   f"(platform={info.platform})",
        )
    if null_count >= 4:
        return Check(
            name="platform backends",
            severity="fail",
            detail=f"{null_count}/6 core capabilities fell through to NullBackend",
            advice="Check that you're on a supported platform and required tools "
                   "are installed (voice doctor binaries section).",
        )
    return Check(
        name="platform backends",
        severity="warn",
        detail=f"{null_count}/6 core capabilities are NullBackend",
        advice="Some pipeline operations will fail loudly. "
               "See _ai/CAPABILITY_MATRIX.md for your platform's expected coverage.",
    )


def check_config_file() -> Check:
    """Make sure ``config.json`` parses (if present)."""
    if not CONFIG_FILE.exists():
        return Check(
            name="config.json",
            severity="ok",
            detail=f"{CONFIG_FILE} absent; defaults in effect",
        )
    try:
        json.loads(CONFIG_FILE.read_text())
    except json.JSONDecodeError as e:
        return Check(
            name="config.json",
            severity="fail",
            detail=f"{CONFIG_FILE} is not valid JSON: {e}",
            advice="Fix the JSON or delete the file (defaults will be used).",
        )
    return Check(
        name="config.json",
        severity="ok",
        detail=f"{CONFIG_FILE} parses cleanly",
    )


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
def run_all() -> list[Check]:
    """Run every check in a stable order. Return the flat list of results."""
    out: list[Check] = []
    out.append(check_config_file())
    out.append(check_platform_backends())
    out.extend(check_binaries())
    out.append(check_opencode_health())
    out.append(check_opencode_mcp_config())
    out.append(check_session_file())
    return out


def restart_opencode_serve() -> Check:
    """``--fix`` action: ``systemctl --user restart opencode-serve``.

    Only invoked by the CLI when ``voice doctor --fix`` is run AND the
    MCP config check came back red. The CLI is responsible for the
    gating; this function just shells out and reports the outcome.
    """
    try:
        r = subprocess.run(
            ["systemctl", "--user", "restart", "opencode-serve"],
            check=False, capture_output=True, text=True, timeout=10.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return Check(
            name="fix: restart opencode-serve",
            severity="fail",
            detail=f"systemctl call failed: {e}",
            advice="Restart the server by hand or check that the service unit "
                   "exists (see install.sh).",
        )
    if r.returncode == 0:
        return Check(
            name="fix: restart opencode-serve",
            severity="ok",
            detail="opencode-serve restarted",
        )
    return Check(
        name="fix: restart opencode-serve",
        severity="fail",
        detail=f"systemctl exited {r.returncode}: {r.stderr.strip()}",
        advice="Inspect: journalctl --user -u opencode-serve",
    )
