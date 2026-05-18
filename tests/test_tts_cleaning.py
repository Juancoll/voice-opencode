"""Tests for tts.clean_for_tts — markdown stripping."""
from __future__ import annotations

from voice_opencode.tts import clean_for_tts


# ---------------------------------------------------------------------------
# Original cases (kept verbatim — guard against regressions)
# ---------------------------------------------------------------------------
def test_strips_code_fences():
    out = clean_for_tts("aqui codigo:\n```python\nprint(1)\n```\nfin")
    assert "print(1)" not in out
    assert "código omitido" in out


def test_strips_inline_code():
    assert clean_for_tts("usa `git status` ya") == "usa git status ya"


def test_strips_md_prefixes():
    out = clean_for_tts("# Titulo\n- uno\n* dos\n> cita")
    assert "Titulo" in out
    assert "#" not in out
    assert ">" not in out


def test_collapses_whitespace():
    assert clean_for_tts("hola    mundo\n\n  hoy") == "hola mundo hoy"


def test_empty_after_clean():
    assert clean_for_tts("") == ""
    assert clean_for_tts("   \n\n  ") == ""


# ---------------------------------------------------------------------------
# Bold / italic / strike — the bug that motivated this round.
# ---------------------------------------------------------------------------
def test_unwraps_bold_stars():
    assert clean_for_tts("hola **mundo** chau") == "hola mundo chau"


def test_unwraps_bold_underscores():
    assert clean_for_tts("hola __mundo__ chau") == "hola mundo chau"


def test_unwraps_italic_stars():
    assert clean_for_tts("esto es *importante* hoy") == "esto es importante hoy"


def test_unwraps_italic_underscores():
    assert clean_for_tts("esto es _importante_ hoy") == "esto es importante hoy"


def test_bold_inside_sentence_no_stray_chars():
    out = clean_for_tts("Veo tu sesión en **voice-opencode** ahora")
    assert out == "Veo tu sesión en voice-opencode ahora"
    assert "*" not in out


def test_nested_bold_italic():
    # Bold runs first; what is left ('*x*') is then italic-unwrapped.
    assert clean_for_tts("muy ***fuerte*** test") == "muy fuerte test"


def test_strikethrough():
    assert clean_for_tts("antes ~~viejo~~ nuevo") == "antes viejo nuevo"


def test_italic_does_not_eat_list_bullet():
    # The leading '* ' is a bullet, not italic. Should be stripped as prefix
    # and not pulled into an italic pair with a later '*'.
    out = clean_for_tts("* uno *dos* tres")
    assert out == "uno dos tres"


# ---------------------------------------------------------------------------
# Links and images.
# ---------------------------------------------------------------------------
def test_strips_inline_link_keeps_text():
    assert clean_for_tts("mirá [esto](https://x.com/y)") == "mirá esto"


def test_strips_image_keeps_alt():
    assert clean_for_tts("![logo](https://x.com/l.png) abajo") == "logo abajo"


def test_strips_image_with_empty_alt():
    out = clean_for_tts("ver ![](https://x.com/l.png) fin")
    assert "x.com" not in out
    assert "fin" in out


def test_strips_reference_link_definition():
    out = clean_for_tts("texto\n[ref]: https://x.com/y\nmás texto")
    assert "x.com" not in out
    assert "ref" not in out
    assert "texto" in out and "más texto" in out


def test_bare_url_becomes_enlace():
    assert clean_for_tts("entra a https://example.com/foo ya") == "entra a enlace ya"


# ---------------------------------------------------------------------------
# Headings, lists, blockquotes — extended.
# ---------------------------------------------------------------------------
def test_ordered_list_marker_stripped():
    out = clean_for_tts("1. primero\n2. segundo\n10) tercero")
    assert out == "primero segundo tercero"


def test_multilevel_heading_stripped():
    assert clean_for_tts("### Sub título") == "Sub título"


def test_blockquote_with_text_keeps_text():
    assert clean_for_tts("> cita importante") == "cita importante"


def test_plus_bullet_stripped():
    assert clean_for_tts("+ uno\n+ dos") == "uno dos"


# ---------------------------------------------------------------------------
# Tables and HTML.
# ---------------------------------------------------------------------------
def test_table_separator_row_removed():
    md = "| col1 | col2 |\n| --- | --- |\n| a | b |"
    out = clean_for_tts(md)
    assert "---" not in out
    assert "col1" in out and "col2" in out and "a" in out and "b" in out


def test_table_pipes_become_commas():
    out = clean_for_tts("| a | b | c |")
    # Leading/trailing pipes become commas, then whitespace collapses.
    assert "a" in out and "b" in out and "c" in out
    assert "|" not in out


def test_html_tags_stripped():
    assert clean_for_tts("hola <br/> mundo <b>fuerte</b>") == "hola mundo fuerte"


# ---------------------------------------------------------------------------
# Real-world payload from logs/voice.log — the actual trigger.
# ---------------------------------------------------------------------------
def test_real_world_payload_from_logs():
    raw = (
        "Veo tu sesión OpenCode en Ghostty trabajando en el proyecto "
        "**voice-opencode**. "
        "El archivo abierto es `src/voice_opencode/tts.py`. "
        "Mirá [el commit](https://github.com/Juancoll/voice-opencode/commit/abc)."
    )
    out = clean_for_tts(raw)
    assert "*" not in out
    assert "`" not in out
    assert "[" not in out and "]" not in out
    assert "github.com" not in out
    assert "voice-opencode" in out
    assert "tts.py" in out
    assert "el commit" in out
