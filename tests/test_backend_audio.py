"""Tests for the wpctl audio backend and the playerctl media backend.

Both backends are exercised entirely via mocked ``subprocess.run`` so
the test suite never touches the real desktop audio stack.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from voice_opencode.backends.linux_audio_pipewire import (
    playerctl_backend as pctl,
)
from voice_opencode.backends.linux_audio_pipewire import (
    wpctl_backend as wp,
)
from voice_opencode.platform import capabilities as cap
from voice_opencode.platform.base import BackendError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0,
                                       stdout=stdout, stderr=stderr)


def _fail(rc: int = 1, stderr: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc,
                                       stdout="", stderr=stderr)


# ---------------------------------------------------------------------------
# WpctlAudioBackend
# ---------------------------------------------------------------------------
class TestWpctlAudioBackend:
    def test_init_raises_if_wpctl_missing(self, monkeypatch):
        monkeypatch.setattr(wp.shutil, "which", lambda _: None)
        with pytest.raises(BackendError, match="wpctl not installed"):
            wp.WpctlAudioBackend()

    def test_capabilities(self, monkeypatch):
        monkeypatch.setattr(wp.shutil, "which", lambda _: "/usr/bin/wpctl")
        b = wp.WpctlAudioBackend()
        assert b.capabilities() == frozenset({
            cap.AUDIO_VOLUME_GET, cap.AUDIO_VOLUME_SET,
            cap.AUDIO_MUTE_TOGGLE, cap.AUDIO_MIC_MUTE_TOGGLE,
        })

    def test_volume_get_parses_float(self, monkeypatch):
        monkeypatch.setattr(wp.shutil, "which", lambda _: "/usr/bin/wpctl")
        b = wp.WpctlAudioBackend()
        with patch.object(wp.subprocess, "run",
                          return_value=_ok("Volume: 0.42\n")) as run:
            assert b.volume_get() == pytest.approx(0.42)
        # Right argv (queries default sink, not source).
        assert run.call_args.args[0][-1] == "@DEFAULT_AUDIO_SINK@"

    def test_volume_get_parses_muted(self, monkeypatch):
        """The [MUTED] suffix must not break level parsing."""
        monkeypatch.setattr(wp.shutil, "which", lambda _: "/usr/bin/wpctl")
        b = wp.WpctlAudioBackend()
        with patch.object(wp.subprocess, "run",
                          return_value=_ok("Volume: 1.00 [MUTED]\n")):
            assert b.volume_get() == pytest.approx(1.0)

    def test_volume_set_clamps_and_formats(self, monkeypatch):
        monkeypatch.setattr(wp.shutil, "which", lambda _: "/usr/bin/wpctl")
        b = wp.WpctlAudioBackend()
        with patch.object(wp.subprocess, "run", return_value=_ok()) as run:
            b.volume_set(1.5)   # over the cap
            b.volume_set(-0.5)  # under the cap
        assert run.call_args_list[0].args[0] == [
            "wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.00",
        ]
        assert run.call_args_list[1].args[0] == [
            "wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.00",
        ]

    def test_mute_toggle_returns_new_state(self, monkeypatch):
        monkeypatch.setattr(wp.shutil, "which", lambda _: "/usr/bin/wpctl")
        b = wp.WpctlAudioBackend()
        # First call: the toggle itself (no stdout needed). Second call:
        # the get-volume that follows, reporting the new state.
        with patch.object(wp.subprocess, "run", side_effect=[
            _ok(),
            _ok("Volume: 1.00 [MUTED]\n"),
        ]):
            assert b.mute_toggle() is True
        with patch.object(wp.subprocess, "run", side_effect=[
            _ok(),
            _ok("Volume: 0.50\n"),
        ]):
            assert b.mute_toggle() is False

    def test_mic_mute_toggle_targets_source(self, monkeypatch):
        monkeypatch.setattr(wp.shutil, "which", lambda _: "/usr/bin/wpctl")
        b = wp.WpctlAudioBackend()
        with patch.object(wp.subprocess, "run", side_effect=[
            _ok(),
            _ok("Volume: 0.52\n"),
        ]) as run:
            b.mic_mute_toggle()
        # Both calls target the source, not the sink.
        for call in run.call_args_list:
            assert "@DEFAULT_AUDIO_SOURCE@" in call.args[0]

    def test_failure_raises_backend_error(self, monkeypatch):
        monkeypatch.setattr(wp.shutil, "which", lambda _: "/usr/bin/wpctl")
        b = wp.WpctlAudioBackend()
        with patch.object(wp.subprocess, "run", return_value=_fail(2, "no node")), \
             pytest.raises(BackendError, match="rc=2"):
            b.volume_get()

    def test_timeout_raises_backend_error(self, monkeypatch):
        monkeypatch.setattr(wp.shutil, "which", lambda _: "/usr/bin/wpctl")
        b = wp.WpctlAudioBackend()
        with patch.object(wp.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("wpctl", 3)), \
             pytest.raises(BackendError, match="timed out"):
            b.volume_get()

    def test_unparseable_output_raises(self, monkeypatch):
        monkeypatch.setattr(wp.shutil, "which", lambda _: "/usr/bin/wpctl")
        b = wp.WpctlAudioBackend()
        with patch.object(wp.subprocess, "run",
                          return_value=_ok("garbage\n")), \
             pytest.raises(BackendError, match="unexpected output"):
            b.volume_get()


# ---------------------------------------------------------------------------
# PlayerctlMediaBackend
# ---------------------------------------------------------------------------
class TestPlayerctlMediaBackend:
    def test_init_raises_if_playerctl_missing(self, monkeypatch):
        monkeypatch.setattr(pctl.shutil, "which", lambda _: None)
        with pytest.raises(BackendError, match="playerctl not installed"):
            pctl.PlayerctlMediaBackend()

    def test_capabilities(self, monkeypatch):
        monkeypatch.setattr(pctl.shutil, "which", lambda _: "/usr/bin/playerctl")
        b = pctl.PlayerctlMediaBackend()
        assert b.capabilities() == frozenset({
            cap.MEDIA_PLAY_PAUSE, cap.MEDIA_NEXT,
            cap.MEDIA_PREV, cap.MEDIA_STATUS,
        })

    @pytest.mark.parametrize("method,verb", [
        ("play_pause", "play-pause"),
        ("next", "next"),
        ("prev", "previous"),
    ])
    def test_transport_argv(self, monkeypatch, method, verb):
        monkeypatch.setattr(pctl.shutil, "which", lambda _: "/usr/bin/playerctl")
        b = pctl.PlayerctlMediaBackend()
        with patch.object(pctl.subprocess, "run", return_value=_ok()) as run:
            getattr(b, method)()
        assert run.call_args.args[0] == ["playerctl", verb]

    def test_status_parses_all_fields(self, monkeypatch):
        monkeypatch.setattr(pctl.shutil, "which", lambda _: "/usr/bin/playerctl")
        b = pctl.PlayerctlMediaBackend()
        out = f"chromium{pctl._SEP}Playing{pctl._SEP}Song A{pctl._SEP}Artist B\n"
        with patch.object(pctl.subprocess, "run", return_value=_ok(out)):
            d = b.status()
        assert d == {
            "player": "chromium", "status": "Playing",
            "title": "Song A", "artist": "Artist B",
        }

    def test_status_tolerates_pipes_in_title(self, monkeypatch):
        """A title containing literal '|' must not steal from the artist field."""
        monkeypatch.setattr(pctl.shutil, "which", lambda _: "/usr/bin/playerctl")
        b = pctl.PlayerctlMediaBackend()
        # The separator is \x1f, so '|' in the title is harmless.
        title = "Foo | bar | baz - YouTube"
        out = f"chromium{pctl._SEP}Playing{pctl._SEP}{title}{pctl._SEP}\n"
        with patch.object(pctl.subprocess, "run", return_value=_ok(out)):
            d = b.status()
        assert d["title"] == title
        assert d["artist"] == ""

    def test_status_pads_missing_fields(self, monkeypatch):
        """Partial metadata (no artist at all) must not crash."""
        monkeypatch.setattr(pctl.shutil, "which", lambda _: "/usr/bin/playerctl")
        b = pctl.PlayerctlMediaBackend()
        out = f"mpv{pctl._SEP}Playing{pctl._SEP}only-a-title"
        with patch.object(pctl.subprocess, "run", return_value=_ok(out)):
            d = b.status()
        assert d == {"player": "mpv", "status": "Playing",
                     "title": "only-a-title", "artist": ""}

    def test_no_player_raises_backend_error(self, monkeypatch):
        monkeypatch.setattr(pctl.shutil, "which", lambda _: "/usr/bin/playerctl")
        b = pctl.PlayerctlMediaBackend()
        with patch.object(pctl.subprocess, "run",
                          return_value=_fail(1, "No players found")), \
             pytest.raises(BackendError, match="No players found"):
            b.status()

    def test_timeout_raises(self, monkeypatch):
        monkeypatch.setattr(pctl.shutil, "which", lambda _: "/usr/bin/playerctl")
        b = pctl.PlayerctlMediaBackend()
        with patch.object(pctl.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("playerctl", 3)), \
             pytest.raises(BackendError, match="timed out"):
            b.play_pause()
