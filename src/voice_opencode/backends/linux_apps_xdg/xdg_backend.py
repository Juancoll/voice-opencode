"""XDG desktop-entry app launcher.

Discovers installed apps by scanning XDG ``applications`` dirs for
``.desktop`` files, launches them with ``gtk-launch`` (which handles
``StartupNotify``, ``DBusActivatable``, ``%U`` placeholders and env
propagation correctly), enumerates running apps from ``/proc``, and
kills by pid via ``os.kill(SIGTERM)``.

Design notes
------------

* **gtk-launch over xdg-open**: ``xdg-open`` is for URLs/MIME, not for
  *launching apps by id*. ``gtk-launch <app-id>`` is the documented
  freedesktop way; it forks itself out and the launched process is
  reparented to PID 1, so we don't get back a usable pid from the
  ``gtk-launch`` call. We get the pid by scanning ``/proc`` for the
  ``Exec=`` binary just after launch (best-effort; documented).
* **Direct ``Exec=`` fallback**: if ``gtk-launch`` is missing or fails
  (rare; included to keep the backend usable on minimal systems), we
  ``Popen`` the ``Exec=`` line after stripping the ``%f %u %U %F %i``
  field codes per the freedesktop spec. That *does* give a real pid.
* **.desktop parsing**: we parse manually instead of ``configparser``
  because real-world entries have duplicate ``Name[xx]=`` keys
  (i18n) which ``configparser`` rejects in strict mode, and we only
  need 4 fields (``Name``, ``Exec``, ``Icon``, ``NoDisplay``).
* **list_running** uses ``/proc/<pid>/comm`` (15-char basename of the
  argv[0]) — fast, no extra deps. We do a single scan per call; for
  the voice-assistant cadence (a few requests/minute) this is fine.
* ``kill`` accepts either a pid (int / int-string) or an app id
  (string matching an installed ``.desktop``); the latter resolves
  to the first running pid whose ``comm`` matches the binary name.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from ...platform import capabilities as cap
from ...platform.base import BackendError

# Fields from the spec we strip out of Exec lines before running directly.
_EXEC_FIELD_CODES: Final[frozenset[str]] = frozenset({
    "%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N",
    "%i", "%c", "%k", "%v", "%m",
})

_LAUNCH_PROBE_DELAY_S: Final[float] = 0.25
_KILL_TIMEOUT_S: Final[float] = 3.0


def _xdg_app_dirs() -> list[Path]:
    """Return the XDG application search path, most-specific first."""
    home = Path(os.environ.get("XDG_DATA_HOME") or
                Path.home() / ".local" / "share")
    raw = os.environ.get("XDG_DATA_DIRS")
    system = raw if raw is not None else "/usr/local/share:/usr/share"
    dirs = [home / "applications"]
    dirs.extend(Path(p) / "applications" for p in system.split(":") if p)
    return [d for d in dirs if d.is_dir()]


def _parse_desktop(path: Path) -> dict[str, str] | None:
    """Return the ``[Desktop Entry]`` section as a dict, or None.

    Skips i18n variants (``Name[es]=``) and comments; takes the first
    occurrence of any key. Returns None if the file doesn't have an
    ``[Desktop Entry]`` section.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    in_section = False
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = (line == "[Desktop Entry]")
            continue
        if not in_section or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if "[" in key:                # skip i18n variants
            continue
        if key in out:                # keep first
            continue
        out[key] = val.strip()
    return out if out else None


def _strip_field_codes(exec_line: str) -> list[str]:
    """Tokenise an ``Exec=`` line and drop ``%f``-style field codes."""
    # Real-world Exec lines rarely contain shell quoting; split on whitespace.
    tokens = exec_line.split()
    return [t for t in tokens if t not in _EXEC_FIELD_CODES]


class XdgAppLauncher:
    """Launch / enumerate / kill desktop apps via XDG + ``/proc``."""

    def __init__(self) -> None:
        # We require *something* to launch with. gtk-launch is preferred.
        if shutil.which("gtk-launch") is None and shutil.which("sh") is None:
            raise BackendError("no launcher available (need gtk-launch or sh)")
        if not _xdg_app_dirs():
            raise BackendError("no XDG application directories found")

    def capabilities(self) -> frozenset[str]:
        return frozenset({
            cap.APP_LAUNCH,
            cap.APP_LIST_INSTALLED,
            cap.APP_LIST_RUNNING,
            cap.APP_KILL,
        })

    # -- enumerate -----------------------------------------------------
    def list_installed(self) -> list[dict[str, Any]]:
        """Return installed apps: ``[{id, name, exec, icon, no_display}]``."""
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for d in _xdg_app_dirs():
            for entry in sorted(d.glob("*.desktop")):
                app_id = entry.stem
                if app_id in seen:
                    continue
                fields = _parse_desktop(entry)
                if not fields or fields.get("Type") != "Application":
                    continue
                seen.add(app_id)
                out.append({
                    "id":         app_id,
                    "name":       fields.get("Name", app_id),
                    "exec":       fields.get("Exec", ""),
                    "icon":       fields.get("Icon", ""),
                    "no_display": fields.get("NoDisplay", "").lower() == "true",
                })
        return out

    def list_running(self) -> list[dict[str, Any]]:
        """Return running processes whose comm matches a known app binary.

        Returns ``[{pid, comm, app_id?}]``; ``app_id`` is filled in when
        we can match the comm to a ``.desktop`` Exec basename.
        """
        comm_to_app: dict[str, str] = {}
        for app in self.list_installed():
            tokens = _strip_field_codes(app["exec"])
            if not tokens:
                continue
            binary = Path(tokens[0]).name
            # Linux comm is the first 15 chars of the basename.
            comm_to_app.setdefault(binary[:15], app["id"])
        out: list[dict[str, Any]] = []
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                comm = (pid_dir / "comm").read_text().strip()
            except OSError:
                continue
            entry: dict[str, Any] = {"pid": int(pid_dir.name), "comm": comm}
            if (app_id := comm_to_app.get(comm)):
                entry["app_id"] = app_id
            out.append(entry)
        return out

    # -- launch --------------------------------------------------------
    def launch(self, app_id_or_cmd: str) -> int:
        """Launch ``app_id_or_cmd``. Returns the new pid (best-effort, may be 0).

        Resolution order:
          1. If a ``.desktop`` file with that id exists, use ``gtk-launch``
             (or fall back to spawning ``Exec=`` directly).
          2. Otherwise treat the input as a raw command and spawn it.
        """
        if not app_id_or_cmd or not app_id_or_cmd.strip():
            raise BackendError("launch: empty command")
        installed = {a["id"]: a for a in self.list_installed()}
        if app_id_or_cmd in installed:
            app = installed[app_id_or_cmd]
            return self._launch_desktop(app_id_or_cmd, app["exec"])
        # Raw command path.
        tokens = app_id_or_cmd.split()
        return self._spawn(tokens)

    def _launch_desktop(self, app_id: str, exec_line: str) -> int:
        """Launch a known .desktop. Prefer gtk-launch (correct env);
        fall back to direct Exec= if it's missing or fails."""
        if shutil.which("gtk-launch") is not None:
            try:
                # gtk-launch detaches; we don't get its child pid.
                # Probe /proc shortly after to find it.
                subprocess.run(
                    ["gtk-launch", app_id],
                    capture_output=True, text=True,
                    timeout=_KILL_TIMEOUT_S, check=True,
                )
                tokens = _strip_field_codes(exec_line)
                if not tokens:
                    return 0
                return self._probe_pid(Path(tokens[0]).name)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass        # fall through to direct spawn
        tokens = _strip_field_codes(exec_line)
        if not tokens:
            raise BackendError(f"launch: empty Exec for {app_id}")
        return self._spawn(tokens)

    def _spawn(self, tokens: list[str]) -> int:
        """Detached Popen. Returns the new pid."""
        if not tokens:
            raise BackendError("launch: nothing to spawn")
        try:
            proc = subprocess.Popen(
                tokens,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, FileNotFoundError) as e:
            raise BackendError(f"launch: {e}") from e
        return proc.pid

    def _probe_pid(self, comm_basename: str) -> int:
        """After a gtk-launch, try to find the new pid by comm match."""
        time.sleep(_LAUNCH_PROBE_DELAY_S)
        target = comm_basename[:15]
        candidates: list[int] = []
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                comm = (pid_dir / "comm").read_text().strip()
            except OSError:
                continue
            if comm == target:
                candidates.append(int(pid_dir.name))
        # Return the highest pid (most recently created on Linux).
        return max(candidates) if candidates else 0

    # -- kill ----------------------------------------------------------
    def kill(self, pid_or_app_id: int | str) -> None:
        """Send SIGTERM to a pid or to all pids matching an app id."""
        targets = list(self._resolve_targets(pid_or_app_id))
        if not targets:
            raise BackendError(f"kill: no process for {pid_or_app_id!r}")
        for pid in targets:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except PermissionError as e:
                raise BackendError(f"kill: {e}") from e

    def _resolve_targets(self, target: int | str) -> Iterable[int]:
        if isinstance(target, int):
            yield target
            return
        s = target.strip()
        if s.isdigit():
            yield int(s)
            return
        # Treat as app id: match running comm via list_running().
        for entry in self.list_running():
            if entry.get("app_id") == s:
                yield entry["pid"]
