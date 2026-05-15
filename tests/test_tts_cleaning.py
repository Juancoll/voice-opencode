"""Tests for tts.clean_for_tts — markdown stripping."""
from __future__ import annotations


def test_strips_code_fences():
    from voice_opencode.tts import clean_for_tts
    out = clean_for_tts("aqui codigo:\n```python\nprint(1)\n```\nfin")
    assert "print(1)" not in out
    assert "código omitido" in out


def test_strips_inline_code():
    from voice_opencode.tts import clean_for_tts
    assert clean_for_tts("usa `git status` ya") == "usa git status ya"


def test_strips_md_prefixes():
    from voice_opencode.tts import clean_for_tts
    out = clean_for_tts("# Titulo\n- uno\n* dos\n> cita")
    assert "Titulo" in out
    assert "#" not in out
    assert ">" not in out


def test_collapses_whitespace():
    from voice_opencode.tts import clean_for_tts
    assert clean_for_tts("hola    mundo\n\n  hoy") == "hola mundo hoy"


def test_empty_after_clean():
    from voice_opencode.tts import clean_for_tts
    assert clean_for_tts("") == ""
    assert clean_for_tts("   \n\n  ") == ""
