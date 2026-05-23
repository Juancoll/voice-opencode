"""Out-of-process key watchdog for dictation mode.

Background
----------
``voice dictate start`` is fired by a Hyprland ``bind`` and exits as
soon as ``audio.start()`` returns — the matching ``bindr`` is
supposed to fire ``voice dictate stop`` on key release. If the
release event is lost (suspend, workspace switch, modifier released
before the main key, compositor hiccup), the recorder stays hot
until the kernel-side ``arecord -d 120`` finally times out.

This module spawns a detached child that polls the kernel directly
via ``ioctl(EVIOCGKEY)`` on ``/dev/input/event*`` keyboard nodes.
When it sees the configured key transition from *pressed* to
*released* it shells out to ``voice dictate stop`` and exits. If it
can't open any device (permissions, no keyboard found), it logs and
exits — the ``-d 120`` cap remains as the fallback safety net.

The child reuses the standard CLI entry point on shutdown rather
than importing :mod:`pipeline` so its dependency surface stays
tiny (no PyQt, no whisper, no requests pulled in by accident).
"""

from __future__ import annotations

import array
import contextlib
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

from .logging import log
from .paths import DICTATION_WATCHDOG_PID_FILE, PROJECT_ROOT

# evdev constants. Pulled from linux/input-event-codes.h. We only
# need the bits the watchdog actually touches; no python-evdev
# dependency.
_EVIOCGBIT_EV = 0x80084520  # EVIOCGBIT(0, len) — capability bitmap
_EVIOCGNAME_LEN = 256
_EV_KEY = 0x01

# Minimal map of human-friendly key names → evdev keycodes. Extend
# on demand; unknown names raise ValueError at watchdog startup so
# misconfiguration fails loudly instead of silently doing nothing.
KEY_CODES: dict[str, int] = {
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64,
    "F7": 65, "F8": 66, "F9": 67, "F10": 68, "F11": 87, "F12": 88,
    "F13": 183, "F14": 184, "F15": 185, "F16": 186, "F17": 187,
    "F18": 188, "F19": 189, "F20": 190, "F21": 191, "F22": 192,
    "F23": 193, "F24": 194,
    "PAUSE": 119,
    "SCROLLLOCK": 70,
    "INSERT": 110,
    "HOME": 102,
    "END": 107,
    "PAGEUP": 104,
    "PAGEDOWN": 109,
    "MENU": 127,
    "PRINT": 99,
}


def _eviocgkey(nr_bits: int) -> int:
    """Build the ``EVIOCGKEY(len)`` ioctl number for ``nr_bits`` keys.

    ``len`` is the *byte* length of the bitmap the kernel will write
    back, i.e. ``ceil(nr_bits / 8)``. We always pass 768 bits (= 96
    bytes), which covers every key the kernel knows about.
    """
    nbytes = (nr_bits + 7) // 8
    # _IOC(_IOC_READ, 'E', 0x18, nbytes)
    return (2 << 30) | (nbytes << 16) | (ord("E") << 8) | 0x18


def _device_has_key_capability(fd: int) -> bool:
    """Return True if the device exposes ``EV_KEY`` events.

    A pointer-only or absolute-only device (mouse, touchpad) will
    not, so we skip it to avoid noisy polling.
    """
    # EVIOCGBIT(0) returns the EV_* bitmap. 4 bytes is enough — we
    # only need bit 1 (EV_KEY).
    buf = array.array("B", [0] * 4)
    try:
        fcntl.ioctl(fd, _EVIOCGBIT_EV, buf)
    except OSError:
        return False
    return bool(buf[0] & (1 << _EV_KEY))


def _iter_keyboard_devices() -> list[Path]:
    """Return ``/dev/input/event*`` nodes that look like keyboards.

    Prefers ``by-path/*-event-kbd`` symlinks because they're
    deterministic and udev-managed; falls back to scanning
    ``event*`` nodes and probing for ``EV_KEY`` capability.
    """
    by_path = Path("/dev/input/by-path")
    if by_path.is_dir():
        kbds = sorted(by_path.glob("*-event-kbd"))
        if kbds:
            return [p.resolve() for p in kbds]
    out: list[Path] = []
    root = Path("/dev/input")
    if not root.is_dir():
        return out
    for node in sorted(root.glob("event*")):
        try:
            fd = os.open(str(node), os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            if _device_has_key_capability(fd):
                out.append(node)
        finally:
            os.close(fd)
    return out


def _key_is_pressed(fd: int, keycode: int) -> bool:
    """Read the current pressed-state of ``keycode`` on device ``fd``.

    Uses ``EVIOCGKEY`` which returns the kernel's current view of
    every key on the device as a bitmap. Read-only, no side effects.
    """
    nbytes = 96  # covers up to KEY_MAX (~767)
    buf = array.array("B", [0] * nbytes)
    fcntl.ioctl(fd, _eviocgkey(nbytes * 8), buf)
    byte_idx = keycode // 8
    bit_idx = keycode % 8
    return bool(buf[byte_idx] & (1 << bit_idx))


def _trigger_stop() -> None:
    """Invoke ``voice dictate stop`` via the project shim.

    Running the shim (not ``python -m``) guarantees we hit the same
    interpreter, env and PYTHONPATH the user's binds use.
    """
    shim = PROJECT_ROOT / "voice"
    try:
        subprocess.Popen(
            [str(shim), "dictate", "stop"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:  # pragma: no cover — shim missing is fatal
        log(f"dictation watchdog: failed to spawn stop: {e}")


def run_watchdog(
    key_name: str,
    poll_ms: int,
    max_seconds: float = 130.0,
) -> int:
    """Block until the configured key is released, then trigger stop.

    Returns an exit code suitable for ``sys.exit``:

    * 0  — key released cleanly, ``voice dictate stop`` spawned.
    * 1  — no usable input device (no perms, no keyboard).
    * 2  — unknown key name.
    * 3  — timed out (``max_seconds``) without seeing a press.

    ``max_seconds`` defaults to 130 s — slightly above the
    ``arecord -d 120`` cap so we never linger after the recorder
    has self-terminated.
    """
    key_upper = key_name.strip().upper()
    keycode = KEY_CODES.get(key_upper)
    if keycode is None:
        log(f"dictation watchdog: unknown key {key_name!r}; disabled.")
        return 2

    devices = _iter_keyboard_devices()
    if not devices:
        log("dictation watchdog: no keyboard devices accessible; disabled.")
        return 1

    fds: list[int] = []
    try:
        for path in devices:
            try:
                fds.append(os.open(str(path), os.O_RDONLY | os.O_NONBLOCK))
            except OSError as e:
                log(f"dictation watchdog: cannot open {path} ({e}); skipping.")
        if not fds:
            log("dictation watchdog: no devices opened; disabled.")
            return 1

        log(
            f"dictation watchdog: armed for {key_upper} (code {keycode}) "
            f"on {len(fds)} device(s)."
        )

        # Phase 1: wait until we observe the key actually pressed.
        # Avoids racing with the Hyprland bind that just fired
        # ``dictate start`` — on a fast bind chain we may sample
        # while the key is already released.
        deadline = time.monotonic() + max_seconds
        saw_press = False
        poll_s = max(poll_ms, 50) / 1000.0
        while time.monotonic() < deadline:
            for fd in fds:
                try:
                    if _key_is_pressed(fd, keycode):
                        saw_press = True
                        break
                except OSError:
                    continue
            if saw_press:
                break
            time.sleep(poll_s)
        if not saw_press:
            log(
                "dictation watchdog: never saw key pressed within "
                f"{max_seconds:.0f}s; exiting."
            )
            return 3

        # Phase 2: wait for the release.
        while time.monotonic() < deadline:
            still_pressed = False
            for fd in fds:
                try:
                    if _key_is_pressed(fd, keycode):
                        still_pressed = True
                        break
                except OSError:
                    continue
            if not still_pressed:
                log(f"dictation watchdog: {key_upper} released; triggering stop.")
                _trigger_stop()
                return 0
            time.sleep(poll_s)

        log(
            "dictation watchdog: deadline reached while still pressed; "
            "letting arecord -d cap finalise."
        )
        return 3
    finally:
        for fd in fds:
            with contextlib.suppress(OSError):
                os.close(fd)


def spawn(key_name: str, poll_ms: int) -> int | None:
    """Fork a detached watchdog child. Returns its pid (parent) or
    ``None`` if the feature is disabled.

    Idempotent: if a previous watchdog is still running, signal it
    away first so we don't end up with two children racing for the
    same release event.
    """
    if not key_name:
        return None
    # Kill any stale watchdog from a previous turn that didn't clean up.
    stop()

    pid = os.fork()
    if pid > 0:
        # Parent — let the child detach.
        DICTATION_WATCHDOG_PID_FILE.write_text(str(pid))
        return pid

    # Child: become a session leader, drop tty, run watchdog.
    try:
        os.setsid()
    except OSError:
        pass
    try:
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        os.close(devnull)
    except OSError:
        pass
    try:
        rc = run_watchdog(key_name, poll_ms)
    except Exception as e:
        log(f"dictation watchdog: crashed ({e}).")
        rc = 1
    finally:
        with contextlib.suppress(OSError):
            DICTATION_WATCHDOG_PID_FILE.unlink()
    os._exit(rc)


def stop() -> None:
    """Terminate the running watchdog, if any. Idempotent."""
    try:
        raw = DICTATION_WATCHDOG_PID_FILE.read_text().strip()
    except FileNotFoundError:
        return
    except OSError:
        return
    try:
        pid = int(raw)
    except ValueError:
        DICTATION_WATCHDOG_PID_FILE.unlink(missing_ok=True)
        return
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        pass
    except OSError as e:
        log(f"dictation watchdog: stop kill({pid}) failed: {e}")
    DICTATION_WATCHDOG_PID_FILE.unlink(missing_ok=True)


def _main() -> int:  # pragma: no cover — manual invocation only
    """``python -m voice_opencode.dictation_watchdog F9 200`` for debugging."""
    key = sys.argv[1] if len(sys.argv) > 1 else "F9"
    poll = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    return run_watchdog(key, poll)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
