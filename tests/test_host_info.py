"""Tests for the expensive-host-info module."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from voice_opencode import host_info


@pytest.fixture(autouse=True)
def _reset():
    host_info.reset()
    yield
    host_info.reset()


# ---------------------------------------------------------------------------
# _run() helper
# ---------------------------------------------------------------------------
def test_run_returns_empty_on_missing_binary():
    """FileNotFoundError ⇒ empty string, never raises."""
    assert host_info._run(["definitely-not-a-real-binary-xyz"]) == ""


def test_run_returns_empty_on_non_zero_exit():
    """A non-zero exit collapses to empty so callers don't see garbage."""
    assert host_info._run(["sh", "-c", "exit 7"]) == ""


def test_run_returns_stripped_stdout_on_success():
    assert host_info._run(["sh", "-c", "echo hello"]) == "hello"


# ---------------------------------------------------------------------------
# _first_match()
# ---------------------------------------------------------------------------
def test_first_match_extracts_version_token():
    assert host_info._first_match("piper 1.2.3 (build x)", r"(\d+\.\d+\.\d+)") == "1.2.3"


def test_first_match_returns_empty_when_no_pattern():
    assert host_info._first_match("no numbers here", r"(\d+)") == ""


# ---------------------------------------------------------------------------
# _collect_locale()
# ---------------------------------------------------------------------------
def test_locale_uses_lc_all_first(monkeypatch):
    monkeypatch.setenv("LC_ALL", "es_AR.UTF-8")
    monkeypatch.setenv("LANG", "C")
    assert host_info._collect_locale() == "es_AR.UTF-8"


def test_locale_falls_back_to_lang(monkeypatch):
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_CTYPE", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert host_info._collect_locale() == "en_US.UTF-8"


def test_locale_empty_when_no_env(monkeypatch):
    for k in ("LC_ALL", "LC_CTYPE", "LANG"):
        monkeypatch.delenv(k, raising=False)
    assert host_info._collect_locale() == ""


# ---------------------------------------------------------------------------
# _collect_versions()
# ---------------------------------------------------------------------------
def test_versions_skip_missing_tools():
    """Tools not on PATH ⇒ empty string for that field, no exception."""
    def fake_which(name):
        return None

    out = host_info._collect_versions(which=fake_which)
    assert out["hyprland_version"] == ""
    assert out["whisper_version"] == ""
    assert out["piper_version"] == ""
    assert out["opencode_version"] == ""
    # python_version always present.
    assert out["python_version"]


def test_versions_extract_hyprland_tag():
    """When hyprctl is on PATH, parse the Tag: line."""
    def fake_which(name):
        return f"/usr/bin/{name}" if name == "hyprctl" else None

    sample = "Hyprland, built from branch (commit deadbeef)\nTag: v0.55.2\nDate: 2026"
    with patch.object(host_info, "_run", return_value=sample):
        out = host_info._collect_versions(which=fake_which)
    assert out["hyprland_version"] == "v0.55.2"


# ---------------------------------------------------------------------------
# _collect_audio()
# ---------------------------------------------------------------------------
def test_audio_empty_when_wpctl_missing():
    def fake_which(name):
        return None
    sink, source = host_info._collect_audio(which=fake_which)
    assert sink == ""
    assert source == ""


def test_audio_parses_wpctl_default_markers():
    """wpctl status output: the default sink/source are tagged with '*'."""
    sample = """\
Audio
 ├─ Sinks:
 │      42. Other Sink                  [vol: 0.30]
 │  *   43. Built-in Audio Analog       [vol: 0.50]
 ├─ Sources:
 │  *   51. Built-in Audio Mic          [vol: 1.00]
 │      52. Other Mic                   [vol: 0.10]
 └─ Streams:
"""

    def fake_which(name):
        return f"/usr/bin/{name}" if name == "wpctl" else None

    with patch.object(host_info, "_run", return_value=sample):
        sink, source = host_info._collect_audio(which=fake_which)
    assert "Built-in Audio Analog" in sink
    assert "Built-in Audio Mic" in source


# ---------------------------------------------------------------------------
# get() + cache
# ---------------------------------------------------------------------------
def test_get_returns_hostinfo_dataclass(monkeypatch):
    """Smoke test: real call, real subprocesses, no exceptions."""
    h = host_info.get()
    # Fields exist and are strings.
    assert isinstance(h.os_name, str)
    assert isinstance(h.os_release, str)
    assert isinstance(h.python_version, str)
    # On any real machine python_version is populated.
    assert h.python_version


def test_get_is_cached():
    h1 = host_info.get()
    h2 = host_info.get()
    assert h1 is h2


def test_force_refresh_rebuilds():
    h1 = host_info.get()
    h2 = host_info.get(force_refresh=True)
    # Same content, new instance — proves we re-ran the probe.
    assert h1 == h2
    assert h1 is not h2


def test_reset_clears_cache():
    host_info.get()
    host_info.reset()
    assert host_info._cache is None
