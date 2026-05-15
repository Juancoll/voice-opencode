# Skill: add a new config key

## Steps

1. **Add the field** to `Settings` in `src/voice_opencode/config.py`
   with a sane default. The default's *type* drives env-var coercion:
   `bool`, `int`, or `str`.

2. **Map an env var** (optional but conventional) in the `ENV_MAP` dict.
   Convention: `VOICE_<UPPER_CASE>` for booleans/strings,
   `OPENCODE_<NAME>` for opencode-related ones.

3. **Use it** from any module via `from .config import settings`. Don't
   read env vars directly — go through settings.

4. **Add a test** in `tests/test_config.py` for default + override paths.

5. **Document it** in the README's "Config" section and in
   `_ai/STATE.md` if it changes the operational model.

## Anti-patterns

- Don't mutate `settings` from app code. Use `config.set_value()` which
  persists to `config.json` and reloads.
- Don't add a key without a default; that breaks fresh installs.
