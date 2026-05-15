# Skill: build a desktop tool for opencode (Phase 3)

> Status: planned. Not implemented yet.

## Goal

Let opencode invoke `type_text`, `press_key`, `move_mouse`, `click_mouse`,
`focused_window`, `capture` as MCP tools. With these, voice prompts like
"rellena el formulario de la ventana de la derecha con mis datos" can
trigger a multi-step plan: capture → identify inputs → click → type →
capture again → validate → next field.

## Approach

1. **Stand up an MCP server** in `src/voice_opencode/mcp_server.py` (or
   a sibling package) using the official Python SDK
   (`pip install mcp`). One tool per primitive in `desktop.py` / `screenshot.py`.

2. **Tool schemas**: keep them small and opinionated. Example:

   ```python
   @server.tool()
   async def type_text(text: str, delay_ms: int = 12) -> str:
       desktop.type_text(text, delay_ms=delay_ms)
       return f"typed {len(text)} chars"
   ```

3. **Register with opencode** via `~/.config/opencode/` MCP config
   pointing to a stdio command that runs our server.

4. **Safety rails**:
   - Confirm-before-act mode (env flag) for `click_mouse` and any
     destructive key combo.
   - Hard-coded blocklist of dangerous combos (Ctrl+Alt+Backspace, etc.).
   - Per-tool rate limit so a runaway loop can't hose the desktop.

5. **Loop pattern** (the model decides this; we just enable it):
   ```
   capture → describe screen → plan → act → capture → diff → ...
   ```

## Open questions

- Do we want one big tool (`do(action, args)`) or many small ones?
  Many small tools have better autocompletion in the model's prompt.
- Should the tray show "agent in control"? Probably yes — block F9 while
  the agent is acting, and add a kill-switch.

## Don't do this without

- A clear way for the user to abort: panic key or hard tray kill.
- Logging every tool call to `logs/agent.log` with timestamp + args.
- A safe sandbox for first tests (a Firefox window with `about:blank`).
