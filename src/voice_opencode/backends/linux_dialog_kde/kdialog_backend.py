"""``kdialog`` blocking dialogs.

KDE's ``kdialog`` is a thin wrapper around Qt's standard dialogs and is
available on any Plasma install. It's also picked up by DankMaterialShell
and most KDE-flavoured environments.

Exit codes (POSIX-y):

* ``0``  — user confirmed / picked something.
* ``1``  — user cancelled / closed the dialog.
* ``2+`` — invocation error (bad args, missing display, …).

For ``ask_text`` / ``ask_choice`` we treat exit 1 as "user said no" and
return ``None``; any other non-zero exit becomes ``BackendError``.

``--inputbox`` writes the answer on stdout. ``--menu`` writes the
selected key on stdout. We strip the trailing newline kdialog appends.
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

# Default timeout: dialogs are interactive, so we allow plenty of time.
# A caller that wants a tighter bound can wrap the call themselves.
_TIMEOUT_S = 300.0


class KdialogBackend:
    def __init__(self) -> None:
        if not shutil.which("kdialog"):
            raise BackendError("kdialog not installed")

    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {DIALOG_CONFIRM, DIALOG_ASK_TEXT, DIALOG_ASK_CHOICE}
        )

    # -- helpers -------------------------------------------------------------
    def _run(self, cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else _TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise BackendError(f"kdialog timeout after {timeout or _TIMEOUT_S}s") from e

    # -- DialogBackend protocol ---------------------------------------------
    def confirm(self, message: str, title: str = "Confirm") -> bool:
        """Yes/No dialog. Returns True iff user clicked Yes."""
        r = self._run(["kdialog", "--title", title, "--yesno", message])
        if r.returncode == 0:
            return True
        if r.returncode == 1:
            return False
        raise BackendError(f"kdialog --yesno failed (rc={r.returncode}): {r.stderr.strip()}")

    def ask_text(
        self,
        prompt: str,
        default: str = "",
        title: str = "Input",
    ) -> str | None:
        """Text input dialog. Returns the text or None if user cancelled."""
        r = self._run(["kdialog", "--title", title, "--inputbox", prompt, default])
        if r.returncode == 0:
            # kdialog appends a trailing newline.
            return r.stdout.rstrip("\n")
        if r.returncode == 1:
            return None
        raise BackendError(f"kdialog --inputbox failed (rc={r.returncode}): {r.stderr.strip()}")

    def ask_choice(
        self,
        prompt: str,
        choices: list[str],
        title: str = "Choose",
    ) -> str | None:
        """Menu of choices. Returns the chosen string or None if cancelled.

        We pass each choice twice (kdialog wants ``<tag> <description>``
        pairs); using the choice as both keeps the result trivial to map.
        """
        if not choices:
            raise BackendError("ask_choice needs at least one option")
        cmd = ["kdialog", "--title", title, "--menu", prompt]
        for c in choices:
            cmd.extend([c, c])
        r = self._run(cmd)
        if r.returncode == 0:
            return r.stdout.rstrip("\n")
        if r.returncode == 1:
            return None
        raise BackendError(f"kdialog --menu failed (rc={r.returncode}): {r.stderr.strip()}")
