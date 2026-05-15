# Skill: add a tray menu item

## Steps

1. Open `src/voice_opencode/tray.py`, find `_build_menu()`.

2. For an action: `self._action(m, "Etiqueta", lambda: voice_cmd("group", "sub"))`.

   For a checkbox synced with a config flag:
   ```python
   self.foo_action = QAction("Texto", m, checkable=True)
   self.foo_action.toggled.connect(
       lambda v: voice_cmd("config", "set", "my_key", "true" if v else "false")
   )
   m.addAction(self.foo_action)
   ```

3. If the new action depends on state, sync it in `refresh()`:
   ```python
   if self.foo_action.isChecked() != st.get("my_key"):
       self.foo_action.blockSignals(True)
       self.foo_action.setChecked(bool(st.get("my_key")))
       self.foo_action.blockSignals(False)
   ```
   And add `my_key` to the JSON returned by `voice state` (in
   `cli.cmd_state`).

4. Restart the tray:
   ```bash
   pkill -f voice_opencode.tray
   nohup voice tray >/tmp/voice-tray.log 2>&1 & disown
   ```

## Tip

Always shell out to the `voice` wrapper rather than calling Python
functions directly. Keeps the tray decoupled from package internals
(see ADR-0005).
