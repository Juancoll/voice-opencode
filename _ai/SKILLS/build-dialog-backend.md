# Skill: add a new dialog backend

> When to use: the user wants ``confirm`` / ``ask_text`` / ``ask_choice``
> on a new toolkit (rofi, wofi, yad, AppleScript, PowerShell-WPF, …).
> See ADR-0013 for the contract this skill enforces.

## Pre-flight

1. The new tool ships dialogs that map to all three operations:
   yes/no, single-line input, single-pick menu. If one is missing, file
   the gap and skip — partial backends confuse the capability check.
2. The tool returns:
   - rc 0 on confirm (and prints the answer to stdout for input/menu).
   - rc 1 on cancel.
   - any other rc on toolkit error (with stderr context).
   If the real tool disagrees (e.g. AppleScript returns rc 1 with
   "User cancelled" on stderr), normalise it in the backend — don't
   leak the disagreement to callers.
3. Confirm the binary is available with ``shutil.which(...)`` in
   ``__init__``, raising ``BackendError("<name> not installed")``.

## Steps

1. **Create the package.** ``src/voice_opencode/backends/<os>_dialog_<name>/``
   with an empty ``__init__.py`` and ``<name>_backend.py``.

2. **Implement ``DialogBackend``.** Copy ``kdialog_backend.py`` as a
   starting point. Keep the signatures and exit-code mapping byte-for-byte
   identical. The only thing that should change is the argv shape and
   maybe how you encode the menu choices.

   ```python
   class MyBackend:
       def capabilities(self) -> frozenset[str]:
           return frozenset({
               cap.DIALOG_CONFIRM,
               cap.DIALOG_ASK_TEXT,
               cap.DIALOG_ASK_CHOICE,
           })

       def confirm(self, question: str, *, title: str | None = None) -> bool:
           argv = ["mytool", "--yesno", question]
           if title:
               argv += ["--title", title]
           rc = _run(argv).returncode
           if rc == 0: return True
           if rc == 1: return False
           raise BackendError(f"mytool failed (rc={rc}): {stderr}")
   ```

3. **Empty-choice guard.** First line of ``ask_choice``:
   ``if not choices: raise BackendError("ask_choice needs at least one option")``.
   Fail-fast is the contract; never open an empty menu.

4. **Strip trailing ``\n``** from stdout for ``ask_text`` and
   ``ask_choice``. Most CLI dialogs append it; the caller doesn't want it.

5. **Timeout.** Use ``_TIMEOUT_S = 300.0`` unless the toolkit imposes
   its own. Wrap ``subprocess.run`` with ``timeout=_TIMEOUT_S`` and let
   ``TimeoutExpired`` bubble as ``BackendError``.

6. **Wire it.** Edit ``src/voice_opencode/platform/__init__.py``,
   ``_wire_common_linux()`` (or the equivalent for your OS). Add it to
   the ``_try()`` chain in priority order:

   ```python
   _try("dialog", lambda: KDialogBackend()) \
       or _try("dialog", lambda: MyNewBackend()) \
       or _try("dialog", lambda: ZenityBackend()) \
       or _set_null("dialog")
   ```

   Reorder if your backend should be preferred (e.g. on a non-KDE box
   you might want yad before zenity). One-line change.

7. **Test it.** Copy ``tests/test_backend_dialog.py`` patterns:
   monkeypatch ``subprocess.run`` to return canned
   ``CompletedProcess(returncode=…, stdout=…, stderr=…)``. Cover:
   - ``__init__`` raises when binary missing (``shutil.which`` → None).
   - ``capabilities()`` returns the right frozenset.
   - argv shape for each op (use ``call_args`` assertions).
   - rc 0 → answer/True (and stdout stripped).
   - rc 1 → None/False.
   - rc other → ``BackendError`` containing stderr text.
   - empty ``choices`` → ``BackendError``.
   - timeout → ``BackendError``.

   No real window must pop. The CI box has no display.

8. **Smoke test live.** Once tests pass:
   ```bash
   ./voice dialog confirm "Phase X smoke" "Test"
   echo $?   # 0 or 2
   ./voice dialog ask "Name?" "default" "Test"
   ./voice dialog choose "Pick" red green blue
   ./voice windows find <toolname>   # confirm window class
   ```

9. **Docs.** Update:
   - ``_ai/ARCHITECTURE.md`` module map (add the package under backends).
   - ``_ai/STATE.md`` pacman/brew/apt runtime table.
   - ``_ai/CHANGELOG.md`` newest-first entry.
   - ``_ai/SESSION.md`` "What just shipped" if mid-phase, or just the
     test count + commit hash.
   - **No new ADR needed** unless you're changing the contract itself.
     The contract lives in ADR-0013; adding a backend that obeys it
     is just an implementation detail.

## Common pitfalls

- **Daemonising tools** (rofi -dmenu can spawn helpers). If
  ``subprocess.run`` hangs, route stdout/stderr to ``DEVNULL`` —
  same bug we hit with ``wl-copy`` in Phase B (see ADR-0013 / Phase B
  changelog). Lose stderr context but stay responsive.
- **Toolkits that print to stderr on cancel** (zenity does this for
  some versions). Filter it out — cancel is rc 1, not an error.
- **Menus with duplicate labels.** kdialog and zenity disambiguate by
  tag-vs-description; your toolkit may not. Document the limitation in
  the backend docstring rather than silently dropping duplicates.
- **Encoding.** Use ``text=True`` in ``subprocess.run`` and rely on the
  system locale. Don't try to be clever with ``errors="replace"``
  unless you have a concrete bug — opaque encoding bugs are worse than
  loud ones.

## Out of scope

- **Non-blocking notifications**. Those are ``NotifyBackend``, not
  ``DialogBackend``. See ``backends/linux_dialog_kde/knotify_backend.py``
  for the libnotify pattern. The two Protocols are deliberately
  separate (ADR-0013).
- **Rich dialogs** (file pickers, color pickers, multi-select). Not in
  the Protocol. If you need one, add a new capability constant +
  Protocol method + ADR first, then implement.
