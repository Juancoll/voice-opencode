# Skill: add a new MCP tool to the desktop server

> When to use: you're adding a new operation that opencode (or any
> other MCP client) should be able to invoke. E.g. ``audio.set_volume``
> in Phase H, ``launch_app`` in Phase I, ``run_shell`` in Phase E.
>
> Read [`build-mcp-tool.md`](./build-mcp-tool.md) for the original
> Phase 3 design notes (lock, rate limit, blocklist). This skill is
> the operational checklist that supersedes it.

## Pre-flight

The new tool must already be backed by:

1. **A capability constant** in ``platform/capabilities.py``
   (stable string — renaming = breaking).
2. **A Protocol method** in ``platform/base.py`` on the relevant
   backend (``InputBackend``, ``WMBackend``, …) — or a new Protocol if
   it's a new family.
3. **At least one real backend implementation** that declares the
   capability via ``capabilities()``.

If any of those is missing, add them first. See ARCHITECTURE.md →
"Adding a backend" and ADR-0012.

## Steps

1. **Pick the tier.** Read ADR-0014. The mapping policy:
   * **read-only**: returns information, no side effects. Examples:
     ``list_windows``, ``capture_screen``, ``clipboard_read``,
     ``audio.get_volume``, ``media.status``. Notifications also belong
     here (output-only, non-destructive).
   * **assist**: reversible or user-confirmable side effects. Examples:
     ``type_text``, ``focus_window``, ``clipboard_write``,
     ``audio.set_volume``, ``media.play``, ``ask_user``.
   * **full**: irreversible. Examples: ``close_window``, ``run_shell``,
     ``delete_file`` (hypothetical). When in doubt, pick the more
     restrictive tier — moving down later (assist → full) is a breaking
     change for users on the restricted modes.

2. **Add the entry to ``capacity.TIER_BY_TOOL``**
   (``src/voice_opencode/capacity.py``). Same commit as the
   ``@mcp.tool`` registration. Without this, the tool defaults to
   ``full`` and silently disappears from ``read-only`` / ``assist`` —
   which is intended fail-safe but you almost certainly want it
   visible somewhere.

3. **Register the tool in ``mcp_server.py``** inside the relevant
   ``_register_<group>(mcp)`` function (input / screen / windows /
   clipboard / dialogs / misc — add a new group if none fits). Pattern:

   ```python
   if _expose(cap.MY_NEW_CAP, "my_new_tool"):
       @mcp.tool(description="One-line, model-friendly summary.")
       def my_new_tool(arg1: str, arg2: int = 0) -> str:
           if (e := _guard("my_new_tool", {"arg1": arg1})):
               return e
           with _acting():                  # OMIT for read-only tools
               try:
                   plat.<backend>.<method>(arg1, arg2)
               except (BackendError, NotSupportedError) as exc:
                   return _err(exc)
           agent.audit("my_new_tool", {"arg1": arg1})
           return "ok: did the thing"
   ```

   * ``_expose(cap, name)`` is **the** gate — never use bare
     ``plat.supported(...)`` for MCP tools. It combines capability
     wiring and capacity tier.
   * ``_guard(name, args)`` enforces the rate limit. Always.
   * ``with _acting():`` is for tools that *do something* — it holds
     the agent lock so F9 push-to-talk is silently dropped during the
     call. Read-only tools skip the lock so the user can still talk
     while the agent is reading state.
   * Return a short string the model can read back to the user.
     Errors return ``f"error: {exc}"`` (helper ``_err`` does this).
   * Audit-log every call. ``agent.audit`` writes JSON Lines to
     ``logs/agent.log``.

4. **Tool description matters.** It's what the model sees. Be
   imperative ("Focus a window."), state the unit ("milliseconds"),
   document the return value, and call out side effects ("BLOCKS",
   "Reversible by …"). The model can't read the source.

5. **Safety rails — when to add a new one.**
   * Dangerous shortcuts go in ``_DANGEROUS_KEYS`` (already covers the
     Ctrl+Alt+F* gauntlet).
   * Per-tool argument validation (``if button not in {…}: return
     f"invalid …"``) goes inline at the top of the tool.
   * Anything that could escape the desktop (``run_shell``, file
     deletion) needs its own ADR; don't bolt it on.

6. **Test the registration.** Add to ``tests/test_mcp_server.py``:

   ```python
   def test_my_new_tool_is_in_assist(monkeypatch):
       names = _build_with(monkeypatch, "assist")
       assert "my_new_tool" in names
   ```

   Add per-mode coverage if the tier choice is non-obvious.

7. **Test the backend separately** with mocked subprocess in
   ``tests/test_backend_<name>.py`` — never let the test suite touch
   the real desktop.

8. **Smoke live.** Restart the MCP server (``systemctl --user restart
   opencode-serve``) and verify with a quick prompt to the model, OR
   inspect directly:

   ```bash
   PYTHONPATH=src venv/bin/python -c "
   from voice_opencode import mcp_server
   srv = mcp_server.build_server()
   print('my_new_tool' in {t.name for t in srv._tool_manager.list_tools()})
   "
   ```

9. **Docs to update** (mandatory):
   * ``_ai/CHANGELOG.md`` — one paragraph, newest-first.
   * ``_ai/STATE.md`` — only if you added a new pacman package.
   * ``_ai/SESSION.md`` — only mid-phase; otherwise the next phase's
     handoff catches it.
   * No new ADR needed unless the tool changes a contract (e.g. a
     new tier, a new gate). Adding a tool that obeys the existing
     contract is just a commit.

## Common pitfalls

- **Forgetting ``TIER_BY_TOOL``**. The tool registers in tests
  (caps mocked to True) but vanishes in production under
  ``read-only``/``assist`` because unknown → ``full``. The test
  ``test_tier_mapping_is_total_and_known`` would NOT catch this
  because the dict is the source of truth for "known"; consider
  adding an explicit list of registered tool names and asserting
  the dict covers them all.
- **Acting tools without ``with _acting():``** — F9 would interrupt
  mid-action, racing the agent lock with the pipeline. Always wrap.
- **Returning bare ``None``** — MCP serialises it as a JSON ``null``
  but FastMCP prefers strings. Return ``""`` or a short status.
- **Tool name vs. function name**. The decorator uses the function
  name as the tool name by default. Keep them identical; rename the
  function rather than passing ``name=...`` to ``@mcp.tool``.

## Out of scope

- New transports (HTTP, SSE) — we're stdio-only. ADR required to change.
- New rate-limit strategies — current sliding window is fine for now.
- Auto-discovery of tools (decorate and register magically) — explicit
  registration in ``_register_*`` is intentional, see ADR-0011.
