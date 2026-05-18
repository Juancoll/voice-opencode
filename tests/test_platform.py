"""Tests for the platform detection / wiring layer."""

from __future__ import annotations

from voice_opencode import platform as plat
from voice_opencode.platform import (
    PLATFORM_LINUX_GENERIC,
    PLATFORM_LINUX_HYPRLAND,
    PLATFORM_LINUX_KDE_WAYLAND,
    PLATFORM_LINUX_WLROOTS,
    PLATFORM_LINUX_X11,
    PLATFORM_MACOS,
    PLATFORM_WINDOWS,
    PlatformInfo,
    detect_platform,
    platform_info,
)


# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------
def test_detect_hyprland_via_env():
    assert detect_platform({"HYPRLAND_INSTANCE_SIGNATURE": "abc"}) == \
        PLATFORM_LINUX_HYPRLAND


def test_detect_kde_wayland():
    assert detect_platform({
        "XDG_SESSION_TYPE": "wayland",
        "XDG_CURRENT_DESKTOP": "KDE",
    }) == PLATFORM_LINUX_KDE_WAYLAND


def test_detect_plasma_wayland():
    assert detect_platform({
        "XDG_SESSION_TYPE": "wayland",
        "XDG_CURRENT_DESKTOP": "plasma",
    }) == PLATFORM_LINUX_KDE_WAYLAND


def test_detect_generic_wlroots():
    assert detect_platform({
        "XDG_SESSION_TYPE": "wayland",
        "XDG_CURRENT_DESKTOP": "sway",
    }) == PLATFORM_LINUX_WLROOTS


def test_detect_x11_via_session_type():
    assert detect_platform({"XDG_SESSION_TYPE": "x11"}) == PLATFORM_LINUX_X11


def test_detect_x11_via_display():
    assert detect_platform({"DISPLAY": ":0"}) == PLATFORM_LINUX_X11


def test_detect_falls_through_to_linux_generic(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "linux")
    assert detect_platform({}) == PLATFORM_LINUX_GENERIC


def test_detect_macos(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    # env vars should be ignored on macOS
    assert detect_platform({"HYPRLAND_INSTANCE_SIGNATURE": "x"}) == PLATFORM_MACOS


def test_detect_windows(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "win32")
    assert detect_platform({"DISPLAY": ":0"}) == PLATFORM_WINDOWS


def test_hyprland_takes_priority_over_session_type():
    """If both HYPRLAND_INSTANCE_SIGNATURE and KDE markers are set,
    the Hyprland signature wins."""
    assert detect_platform({
        "HYPRLAND_INSTANCE_SIGNATURE": "x",
        "XDG_SESSION_TYPE": "wayland",
        "XDG_CURRENT_DESKTOP": "KDE",
    }) == PLATFORM_LINUX_HYPRLAND


# ---------------------------------------------------------------------------
# Singletons + capabilities
# ---------------------------------------------------------------------------
def test_active_platform_resolves_to_a_string():
    assert isinstance(plat.active_platform, str)
    assert plat.active_platform


def test_singletons_quack_correctly():
    """All slots resolve to *something* — never None."""
    for name in ("wm", "input", "screen", "clipboard", "notify",
                 "dialog", "audio", "media", "apps", "shell"):
        be = getattr(plat, name)
        assert be is not None
        assert hasattr(be, "capabilities")
        assert isinstance(be.capabilities(), frozenset)


def test_supported_consistent_with_all_capabilities():
    caps = plat.all_capabilities()
    for c in caps:
        assert plat.supported(c) is True
    assert plat.supported("nope.does.not.exist") is False


def test_null_backends_raise_not_supported():
    """Calling a method on a Null backend raises NotSupportedError."""
    from voice_opencode.platform.base import NotSupportedError
    from voice_opencode.platform.null import NullDialogBackend

    null = NullDialogBackend()
    assert null.capabilities() == frozenset()
    try:
        null.confirm("?")
    except NotSupportedError:
        pass
    else:
        raise AssertionError("Null backend should raise NotSupportedError")


# ---------------------------------------------------------------------------
# platform_info (Phase K)
# ---------------------------------------------------------------------------
def _fake_which(present: set[str]):
    """which() stub that returns a fake path for tools in ``present``."""
    return lambda name: f"/usr/bin/{name}" if name in present else None


class TestPlatformInfo:
    def test_hyprland_snapshot(self) -> None:
        info = platform_info(
            env={
                "HYPRLAND_INSTANCE_SIGNATURE": "abc",
                "XDG_SESSION_TYPE":            "wayland",
                "XDG_CURRENT_DESKTOP":         "Hyprland",
                "WAYLAND_DISPLAY":             "wayland-1",
            },
            which=_fake_which({"hyprctl", "grim", "wl-copy", "wl-paste"}),
        )
        assert isinstance(info, PlatformInfo)
        assert info.platform == PLATFORM_LINUX_HYPRLAND
        assert info.session_type == "wayland"
        assert info.desktop == "hyprland"
        assert info.is_hyprland is True
        assert info.is_wayland is True
        assert info.is_x11 is False
        assert info.is_kde is False
        assert "hyprctl" in info.tools
        assert "grim" in info.tools
        assert "kdialog" not in info.tools

    def test_kde_wayland_snapshot(self) -> None:
        info = platform_info(
            env={
                "XDG_SESSION_TYPE":    "wayland",
                "XDG_CURRENT_DESKTOP": "KDE",
                "WAYLAND_DISPLAY":     "wayland-0",
            },
            which=_fake_which({"kdialog", "notify-send", "wl-copy"}),
        )
        assert info.platform == PLATFORM_LINUX_KDE_WAYLAND
        assert info.is_kde is True
        assert info.is_hyprland is False
        assert "kdialog" in info.tools

    def test_x11_snapshot(self) -> None:
        info = platform_info(
            env={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"},
            which=_fake_which({"xclip"}),
        )
        assert info.platform == PLATFORM_LINUX_X11
        assert info.is_x11 is True
        assert info.is_wayland is False
        assert "xclip" in info.tools

    def test_wayland_display_promotes_session_when_xdg_unset(self) -> None:
        """install.sh:80 logic — WAYLAND_DISPLAY alone implies wayland."""
        info = platform_info(
            env={"WAYLAND_DISPLAY": "wayland-1",
                 "XDG_CURRENT_DESKTOP": "sway"},
            which=_fake_which(set()),
        )
        assert info.session_type == "wayland"
        assert info.is_wayland is True

    def test_display_promotes_session_when_xdg_unset(self) -> None:
        info = platform_info(
            env={"DISPLAY": ":1"},
            which=_fake_which(set()),
        )
        assert info.session_type == "x11"
        assert info.is_x11 is True

    def test_env_only_captures_present_keys(self) -> None:
        info = platform_info(
            env={"XDG_SESSION_TYPE": "wayland", "UNRELATED": "x"},
            which=_fake_which(set()),
        )
        # Captured env is restricted to the known XDG/display keys
        # and only includes ones that were actually set.
        assert "XDG_SESSION_TYPE" in info.env
        assert "DISPLAY" not in info.env
        assert "UNRELATED" not in info.env

    def test_tools_empty_when_nothing_on_path(self) -> None:
        info = platform_info(
            env={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"},
            which=_fake_which(set()),
        )
        assert info.tools == frozenset()

    def test_to_dict_is_json_friendly(self) -> None:
        import json
        info = platform_info(
            env={"HYPRLAND_INSTANCE_SIGNATURE": "abc",
                 "XDG_SESSION_TYPE": "wayland"},
            which=_fake_which({"hyprctl"}),
        )
        d = info.to_dict()
        # Round-trips through json.
        text = json.dumps(d)
        again = json.loads(text)
        assert again["platform"] == PLATFORM_LINUX_HYPRLAND
        assert again["tools"] == ["hyprctl"]
        assert again["is_hyprland"] is True
        # Sorted tools for stable output.
        assert d["tools"] == sorted(d["tools"])

    def test_frozen_dataclass(self) -> None:
        info = platform_info(env={}, which=_fake_which(set()))
        try:
            info.platform = "mutated"  # type: ignore[misc]
        except Exception:
            return
        raise AssertionError("PlatformInfo should be frozen")
