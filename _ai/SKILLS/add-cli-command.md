# Skill: add a new `voice ...` CLI command

## When to use
You want a new behaviour callable from the shell, the Hyprland binds,
or the tray.

## Steps

1. **Decide the group.** Most commands belong to an existing group:
   `rec`, `session`, `tts`, `desktop`, `config`. If none fits,
   create a new top-level command (sparingly).

2. **Implement the logic** in the right module. The CLI must stay thin:
   parse args, call the function, return an int. If the logic is non-trivial
   it goes into `src/voice_opencode/<module>.py`, never into `cli.py`.

3. **Wire it in** `src/voice_opencode/cli.py`:
   - Add a `cmd_<group>` handler if creating a group, OR
   - Add a new `elif sub == "<name>":` branch in the existing handler.
   - Register top-level commands in `COMMANDS`.
   - Add a legacy alias in `LEGACY` only if migrating an existing flat
     command name; new commands skip this.

4. **Test it.** Add a case in `tests/test_cli.py` that asserts the
   handler is called and the exit code is right. Use `unittest.mock.patch`
   to avoid touching real subprocesses.

5. **Document it.**
   - Update the docstring at the top of `cli.py`.
   - Update the README's "CLI" section.
   - Append a one-line note to `_ai/CHANGELOG.md`.

## Example

Adding `voice rec status`:

```python
# in cli.py, inside cmd_rec:
elif sub == "status":
    print("recording:", audio.is_recording())
```

That's it — no plumbing, no config.

## Anti-patterns

- Don't add subprocess calls directly in `cli.py`. Move them to a module.
- Don't print error text to stdout; use `_eprint()`.
- Don't catch broad exceptions in handlers; let them surface so users
  see the traceback.
