# Skill: add a Piper voice

## Steps

1. Browse https://huggingface.co/rhasspy/piper-voices/tree/main and pick
   a `<lang>/<region>/<name>/<quality>` path.

2. Run:

   ```bash
   ./download-voice.sh es/es_AR/daniela/high
   ```

   This downloads `<region>-<name>-<quality>.onnx` and the matching
   `.onnx.json` sidecar into `voices/`.

3. Verify:

   ```bash
   ./voice tts voices                           # should appear in the list
   VOICE=es_AR-daniela-high voice tts say "prueba"
   ```

4. Multi-speaker models (e.g. `es_ES-sharvard-medium`) carry a
   `speaker_id_map` in the JSON. Pick the index:

   ```bash
   ./voice config set voice es_ES-sharvard-medium
   ./voice config set speaker_id 1   # 0=M, 1=F for sharvard
   ```

5. The tray menu auto-discovers new voices on next start.

## How the system finds them

`tts.resolve_voice(name)` in `src/voice_opencode/tts.py`:

- exact match `voices/<name>.onnx` first
- fallback: glob `*<name>*.onnx`, return first sorted

So partial names work: `VOICE=daniela voice tts say "hola"` is fine.
