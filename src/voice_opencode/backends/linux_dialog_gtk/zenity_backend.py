"""``zenity`` blocking dialogs (GTK fallback when kdialog isn't available).

Same Protocol as the kdialog backend; same exit-code convention:

* ``0``  — user confirmed / picked.
* ``1``  — user cancelled.
* ``5``  — dialog timed out (we don't pass ``--timeout`` here but keep
            the mapping for completeness).
* other — invocation error → ``BackendError``.

zenity's ``--entry`` writes the answer on stdout; ``--list`` writes the
selected row. We strip the trailing newline.
"""

from __future__ import annotations

import shutil
import subprocess

from ...platform.base import BackendError
from ...platform.capabilities import (
    DIALOG_ASK_CHOICE,
    DIALOG_ASK_TEXT,
    DIALOG_CONFIRM,
)

_TIMEOUT_S = 300.0


class ZenityBackend:
    def __init__(self) -> None:
        if not shutil.which("zenity"):
            raise BackendError("zenity not installed")

    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {DIALOG_CONFIRM, DIALOG_ASK_TEXT, DIALOG_ASK_CHOICE}
        )

    def _run(self, cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else _TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise BackendError(f"zenity timeout after {timeout or _TIMEOUT_S}s") from e

    def confirm(self, message: str, title: str = "Confirm") -> bool:
        r = self._run(
            ["zenity", "--question", "--title", title, "--text", message],
        )
        if r.returncode == 0:
            return True
        if r.returncode == 1:
            return False
        raise BackendError(f"zenity --question failed (rc={r.returncode}): {r.stderr.strip()}")

    def ask_text(
        self,
        prompt: str,
        default: str = "",
        title: str = "Input",
    ) -> str | None:
        cmd = [
            "zenity", "--entry",
            "--title", title,
            "--text", prompt,
            "--entry-text", default,
        ]
        r = self._run(cmd)
        if r.returncode == 0:
            return r.stdout.rstrip("\n")
        if r.returncode == 1:
            return None
        raise BackendError(f"zenity --entry failed (rc={r.returncode}): {r.stderr.strip()}")

    def ask_choice(
        self,
        prompt: str,
        choices: list[str],
        title: str = "Choose",
    ) -> str | None:
        if not choices:
            raise BackendError("ask_choice needs at least one option")
        cmd = [
            "zenity", "--list",
            "--title", title,
            "--text", prompt,
            "--column", "Option",
            *choices,
        ]
        r = self._run(cmd)
        if r.returncode == 0:
            return r.stdout.rstrip("\n")
        if r.returncode == 1:
            return None
        raise BackendError(f"zenity --list failed (rc={r.returncode}): {r.stderr.strip()}")
