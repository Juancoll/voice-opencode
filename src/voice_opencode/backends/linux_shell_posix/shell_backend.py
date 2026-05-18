"""POSIX shell backend with default-deny allowlist + safety rails.

This is the most dangerous surface in the project. Three layers
of protection (ADR-0017):

1. **Tier gate.** The MCP tool ``shell_run`` is registered only in
   the ``full`` capacity tier (see ``capacity.TIER_BY_TOOL``). In
   ``read-only`` and ``assist`` the tool simply doesn't exist for
   the model to call.

2. **Default-deny allowlist.** ``run`` parses the command into argv
   (``shlex.split`` for strings; verbatim for lists), takes
   ``Path(argv[0]).name`` as the basename, and demands
   ``re.fullmatch`` against at least one regex in
   ``settings.shell_allowlist``. No match → ``BackendError`` before
   any subprocess is spawned. Empty allowlist = nothing runs.

3. **No shell metacharacters.** We never pass a string to
   ``subprocess.run(shell=True)``. Pipes, redirects, ``$(…)``,
   backticks, ``&&``, ``||`` and ``;`` are rejected by
   ``shlex.split`` itself when they'd create a different argv
   semantic, but as belt-and-suspenders we also scan argv for any
   token containing a shell-special character and reject it.

A failed allowlist check is loud (BackendError) so the model gets a
clear "denied" signal it can adapt to. A timeout returns a partial
result with rc=-1 so the caller distinguishes timeout from "ran
and printed nothing". Output is truncated to ``_MAX_OUTPUT_BYTES``
per stream to keep audit logs sane.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Final

from ...platform import capabilities as cap
from ...platform.base import BackendError

# Tokens containing any of these are rejected — they're only
# meaningful to a shell, never to a direct execve. ``shlex.split``
# already prevents them from acting as operators when given a
# string, but we belt-and-suspenders so list-input is also clean.
_SHELL_METACHARS: Final[frozenset[str]] = frozenset(";|&`$<>")

# Hard cap on captured output per stream. We're shoving these into
# the audit log and into MCP responses; multi-MB blobs are useless
# noise. Tools that need streaming should not use shell_run.
_MAX_OUTPUT_BYTES: Final[int] = 64 * 1024


class PosixShellBackend:
    """Run external commands with rails. POSIX-style (Linux, macOS)."""

    def __init__(self, allowlist: tuple[str, ...] | None = None,
                 timeout_s: float | None = None) -> None:
        # Lazy import of settings to avoid bootstrap cycles when wired
        # into the platform table.
        from ... import config
        self._allowlist: tuple[str, ...] = (
            allowlist if allowlist is not None
            else config.settings.shell_allowlist
        )
        self._timeout_s: float = (
            timeout_s if timeout_s is not None
            else float(config.settings.shell_timeout_s)
        )
        # Pre-compile for the hot path.
        self._patterns: list[re.Pattern[str]] = [
            re.compile(p) for p in self._allowlist
        ]

    def capabilities(self) -> frozenset[str]:
        return frozenset({cap.SHELL_RUN})

    def run(
        self,
        cmd: str | list[str],
        cwd: str | None = None,
        timeout: float | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Run ``cmd``. Returns ``{rc, stdout, stderr, dry_run, cmd, denied?}``.

        ``timeout`` defaults to ``settings.shell_timeout_s`` and is
        capped at 60 s no matter what the caller passes — runaway
        commands should be solved by improving the command, not by
        waiting longer.
        """
        argv = self._parse(cmd)
        self._check_allowed(argv)
        effective_timeout = min(
            float(timeout) if timeout is not None else self._timeout_s,
            60.0,
        )
        if dry_run:
            return {
                "rc":      0,
                "stdout":  "",
                "stderr":  "",
                "dry_run": True,
                "cmd":     argv,
                "cwd":     cwd or "",
            }
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            # TimeoutExpired.stdout/stderr may be bytes or str depending
            # on whether the read managed to decode before the kill.
            partial_out = e.stdout if isinstance(e.stdout, str) else (
                e.stdout.decode("utf-8", errors="replace") if e.stdout else "")
            partial_err = e.stderr if isinstance(e.stderr, str) else (
                e.stderr.decode("utf-8", errors="replace") if e.stderr else "")
            return {
                "rc":      -1,
                "stdout":  _truncate(partial_out),
                "stderr":  _truncate(partial_err) + f"\n[timeout after {effective_timeout}s]",
                "dry_run": False,
                "cmd":     argv,
                "cwd":     cwd or "",
            }
        except (OSError, FileNotFoundError) as e:
            raise BackendError(f"shell: cannot exec {argv[0]!r}: {e}") from e
        return {
            "rc":      proc.returncode,
            "stdout":  _truncate(proc.stdout),
            "stderr":  _truncate(proc.stderr),
            "dry_run": False,
            "cmd":     argv,
            "cwd":     cwd or "",
        }

    # -- internals -----------------------------------------------------
    def _parse(self, cmd: str | list[str]) -> list[str]:
        if isinstance(cmd, str):
            try:
                argv = shlex.split(cmd, posix=True)
            except ValueError as e:
                raise BackendError(f"shell: cannot parse {cmd!r}: {e}") from e
        else:
            argv = [str(x) for x in cmd]
        if not argv:
            raise BackendError("shell: empty command")
        for token in argv:
            if any(c in _SHELL_METACHARS for c in token):
                raise BackendError(
                    f"shell: token {token!r} contains a shell metachar; "
                    f"shell_run never spawns a shell — split into argv yourself"
                )
        return argv

    def _check_allowed(self, argv: list[str]) -> None:
        if not self._patterns:
            raise BackendError(
                "shell: allowlist is empty (configure shell_allowlist "
                "in config.json to allow specific commands)"
            )
        basename = Path(argv[0]).name
        for pat in self._patterns:
            if pat.fullmatch(basename):
                return
        raise BackendError(
            f"shell: {basename!r} not in allowlist "
            f"(allowed patterns: {self._allowlist})"
        )


def _truncate(text: str) -> str:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= _MAX_OUTPUT_BYTES:
        return text
    head = raw[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return head + f"\n[…truncated, {len(raw) - _MAX_OUTPUT_BYTES} bytes elided]"
