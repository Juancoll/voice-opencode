# Skill: debug the pipeline

When F9 release doesn't produce a spoken answer, walk the stages.

## 1. Did recording happen?

```bash
ls -la $XDG_RUNTIME_DIR/voice-opencode/rec.wav
file $XDG_RUNTIME_DIR/voice-opencode/rec.wav   # must say RIFF (little-endian) data, WAVE audio
aplay $XDG_RUNTIME_DIR/voice-opencode/rec.wav  # listen back
```

If size < 4 KB or empty → `arecord` problem. Check default source:
```bash
pactl get-default-source
```
Switch with `pactl set-default-source <name>`.

## 2. Did STT produce text?

Re-run by hand to see whisper's output:
```bash
whisper-cli -m models/ggml-small.bin -l es -nt -np \
    -f $XDG_RUNTIME_DIR/voice-opencode/rec.wav
```
Empty output is normal for silent or noise-only recordings.

## 3. Did opencode answer?

```bash
voice ask "hola, di solo OK" --no-tts
```

Errors here:
- `not reachable` → `systemctl --user status opencode-serve`
- HTTP 4xx → check `journalctl --user -u opencode-serve -f`

## 4. Did TTS speak?

```bash
voice tts say "prueba"
tail logs/piper.log logs/player.log
```

If piper hangs: maybe the .onnx file is corrupt — re-download.

## Common gotchas

- **`[BLANK_AUDIO]` only**: too quiet or too short. Hold F9 longer.
- **Spanish but model in English**: check `whisper_lang` in config.
- **Garbled TTS**: the voice's sample rate doesn't match `paplay`'s rate.
  We read it from the sidecar, so this only happens if the .onnx.json is
  missing — re-download.
