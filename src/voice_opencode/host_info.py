"""
Host info: expensive, slow-changing facts about the running machine.

``platform.PlatformInfo`` captures the *cheap* host snapshot (OS,
session type, desktop, which tools are on PATH, env vars). That dataclass
is built per-call and used in hot paths; it must stay pure and fast.

This module captures the *expensive* facts that need subprocesses to
discover: kernel + distro release, version strings of the binaries the
pipeline actually shells out to (whisper.cpp, piper, opencode,
hyprctl), system locale, default audio sink/source. These are
collected once and cached for the lifetime of the process — they don't
meaningfully change between two F9 presses, and shelling out 4-5
subprocesses on every turn would add ~50-80ms of pure overhead.

Design:

* ``HostInfo`` is a frozen dataclass; only string fields. Empty
  string ⇒ "could not detect" — never raise, never return ``None`` for
  individual fields. Callers concatenate; an empty string just renders
  as nothing.
* ``get()`` is the public entry point. Lazy + cached. Tests use
  ``reset()`` between cases.
* All collectors swallow ``OSError`` / non-zero exit / timeout and
  return ``""``. The caller's invariant is "I get a HostInfo, never
  an exception" — fail-soft mirrors what we do in ``context.py``.
* Cross-OS: today we only detect Linux thoroughly. macOS/Windows
  branches degrade to empty strings without breaking. ADR-0031
  governs how those get populated.
"""

from __future__ import annotations

import os
import platform as _stdlib_platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from .logging import log

# How long to wait for any single detection subprocess. Generous because
# the cache means we pay it exactly once per process.
_SUBPROC_TIMEOUT_S: float = 3.0


@dataclass(frozen=True)
class HostInfo:
    """Expensive, slow-changing facts about the host. All strings;
    empty string ⇒ unknown / not detected. Never raise on access."""

    # OS + kernel.
    os_name:        str = ""   # "Linux", "Darwin", "Windows"
    os_release:     str = ""   # kernel release, e.g. "6.13.4-zen1-1-zen"
    distro:         str = ""   # "Arch Linux", "CachyOS", macOS version
    arch:           str = ""   # "x86_64", "arm64"

    # Versions of the binaries this pipeline cares about.
    python_version: str = ""
    hyprland_version: str = ""
    whisper_version:  str = ""
    piper_version:    str = ""
    opencode_version: str = ""

    # Locale (LC_ALL > LC_CTYPE > LANG; first non-empty).
    locale:         str = ""

    # Audio.
    audio_sink:     str = ""   # default output sink (e.g. ALSA card name)
    audio_source:   str = ""   # default input source (mic)

    # Diagnostic notes: lines explaining why some field is empty.
    # Not for the LLM — for ``voice doctor``.
    notes:          tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Subprocess helpers (private)
# ---------------------------------------------------------------------------
def _run(argv: list[str]) -> str:
    """Run ``argv``; return stdout stripped, or ``""`` on any failure.

    We deliberately don't surface stderr or exit codes — the caller
    just wants a version string, and absent/broken tools must render
    as empty, not as exceptions.
    """
    try:
        r = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=_SUBPROC_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if r.returncode != 0:
        return ""
    return (r.stdout or "").strip()


def _first_match(text: str, pattern: str) -> str:
    """Return the first regex group match in ``text``, or ``""``.

    Used to slim down ``--version`` output to just the version token.
    Most tools emit something like ``foo 1.2.3 (build deadbeef)``;
    we only want ``1.2.3``.
    """
    m = re.search(pattern, text)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Field collectors (private; one per HostInfo field group)
# ---------------------------------------------------------------------------
def _collect_os() -> tuple[str, str, str, str]:
    """Return (os_name, os_release, distro, arch)."""
    os_name = _stdlib_platform.system() or ""
    os_release = _stdlib_platform.release() or ""
    arch = _stdlib_platform.machine() or ""

    distro = ""
    # /etc/os-release is the freedesktop.org standard; Arch, CachyOS,
    # Debian, Fedora, Ubuntu, even WSL fill it in.
    try:
        with open("/etc/os-release") as f:
            data = f.read()
        m = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', data, re.MULTILINE)
        if m:
            distro = m.group(1).strip()
    except OSError:
        pass
    # macOS fallback.
    if not distro and os_name == "Darwin":
        ver = _stdlib_platform.mac_ver()[0]
        if ver:
            distro = f"macOS {ver}"

    return os_name, os_release, distro, arch


def _collect_locale() -> str:
    """Return the active POSIX locale, or ``""``."""
    for key in ("LC_ALL", "LC_CTYPE", "LANG"):
        val = os.environ.get(key)
        if val:
            return val
    return ""


def _collect_versions(which=shutil.which) -> dict[str, str]:
    """Return {binary_name: version_string} for the tools we ship around.

    ``which`` injectable for tests so we can simulate "tool not on PATH"
    without monkey-patching shutil.
    """
    versions = {
        "hyprland_version": "",
        "whisper_version":  "",
        "piper_version":    "",
        "opencode_version": "",
        "python_version":   _stdlib_platform.python_version(),
    }
    if which("hyprctl"):
        # hyprctl version → first line like "Hyprland, built from branch ..."
        # then a "Tag: vX.Y.Z" line. We want the tag.
        out = _run(["hyprctl", "version"])
        versions["hyprland_version"] = _first_match(out, r"Tag:\s*([^\s,]+)")
    if which("whisper-cli"):
        out = _run(["whisper-cli", "--version"])
        # whisper.cpp prints "whisper.cpp version: 1.5.4" or similar.
        versions["whisper_version"] = _first_match(out, r"(\d+\.\d+(?:\.\d+)?)")
    if which("piper-tts"):
        out = _run(["piper-tts", "--version"])
        versions["piper_version"] = _first_match(out, r"(\d+\.\d+(?:\.\d+)?)")
    if which("opencode"):
        out = _run(["opencode", "--version"])
        versions["opencode_version"] = _first_match(out, r"(\d+\.\d+(?:\.\d+)?)")
    return versions


def _collect_audio(which=shutil.which) -> tuple[str, str]:
    """Return (default_sink, default_source).

    PipeWire/PulseAudio via ``wpctl``: ``wpctl status`` prints a tree
    with "*" markers on the defaults. We don't fully parse the tree;
    we grep for ``*`` on a non-header line under "Sinks:" / "Sources:".
    """
    if not which("wpctl"):
        return "", ""
    out = _run(["wpctl", "status"])
    if not out:
        return "", ""
    sink = ""
    source = ""
    section = ""
    for line in out.splitlines():
        s = line.strip()
        if s.endswith("Sinks:"):
            section = "sink"
            continue
        if s.endswith("Sources:"):
            section = "source"
            continue
        if s.endswith("Sink endpoints:") or s.endswith("Source endpoints:") or s.endswith("Streams:") or s.endswith("Filters:"):
            section = ""
            continue
        # Default markers look like "│  *   42. Built-in Audio  [vol: 0.50]".
        if "*" in s and section in ("sink", "source"):
            # Grab the human-readable name between the id and the metadata.
            m = re.search(r"\*\s*\d+\.\s*([^\[]+?)(?:\s*\[|$)", s)
            if m:
                name = m.group(1).strip()
                if section == "sink" and not sink:
                    sink = name
                elif section == "source" and not source:
                    source = name
    return sink, source


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_cache: HostInfo | None = None


def get(*, force_refresh: bool = False) -> HostInfo:
    """Return the cached host snapshot, building it on first call.

    Never raises. Cached forever within the process (cheap is the
    whole point); call :func:`reset` if you need to re-detect (e.g.
    after the user updated piper).
    """
    global _cache
    if _cache is not None and not force_refresh:
        return _cache

    notes: list[str] = []
    os_name, os_release, distro, arch = _collect_os()
    locale = _collect_locale()
    try:
        versions = _collect_versions()
    except Exception as e:  # pragma: no cover — defensive
        log(f"host_info: version probe failed: {e}")
        versions = {
            "hyprland_version": "", "whisper_version": "",
            "piper_version": "", "opencode_version": "",
            "python_version": _stdlib_platform.python_version(),
        }
        notes.append(f"version probe error: {e}")
    try:
        audio_sink, audio_source = _collect_audio()
    except Exception as e:  # pragma: no cover — defensive
        log(f"host_info: audio probe failed: {e}")
        audio_sink, audio_source = "", ""
        notes.append(f"audio probe error: {e}")

    _cache = HostInfo(
        os_name=os_name,
        os_release=os_release,
        distro=distro,
        arch=arch,
        python_version=versions["python_version"],
        hyprland_version=versions["hyprland_version"],
        whisper_version=versions["whisper_version"],
        piper_version=versions["piper_version"],
        opencode_version=versions["opencode_version"],
        locale=locale,
        audio_sink=audio_sink,
        audio_source=audio_source,
        notes=tuple(notes),
    )
    return _cache


def reset() -> None:
    """Drop the cached snapshot. Tests and ``config.reload()`` call this."""
    global _cache
    _cache = None
