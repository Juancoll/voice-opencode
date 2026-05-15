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
    detect_platform,
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
