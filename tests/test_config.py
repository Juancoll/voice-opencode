"""Tests for the config loader."""
from __future__ import annotations

import json

import pytest


def test_defaults_when_no_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "voice_opencode.paths.CONFIG_FILE", tmp_path / "config.json"
    )
    import importlib

    import voice_opencode.config as cfg
    importlib.reload(cfg)
    assert cfg.settings.voice == cfg.DEFAULTS.voice
    assert cfg.settings.opencode_port == 4096


def test_user_json_overrides_defaults(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"voice": "es_ES-sharvard-medium", "speaker_id": 1}))
    monkeypatch.setattr("voice_opencode.paths.CONFIG_FILE", cfg_file)
    import importlib

    import voice_opencode.config as cfg
    importlib.reload(cfg)
    assert cfg.settings.voice == "es_ES-sharvard-medium"
    assert cfg.settings.speaker_id == 1


def test_env_overrides_json(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"voice": "es_ES-sharvard-medium"}))
    monkeypatch.setattr("voice_opencode.paths.CONFIG_FILE", cfg_file)
    monkeypatch.setenv("VOICE", "es_AR-daniela-high")
    monkeypatch.setenv("VOICE_SPEAKER", "3")
    import importlib

    import voice_opencode.config as cfg
    importlib.reload(cfg)
    assert cfg.settings.voice == "es_AR-daniela-high"
    assert cfg.settings.speaker_id == 3


def test_bool_coercion(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("voice_opencode.paths.CONFIG_FILE", cfg_file)
    monkeypatch.setenv("VOICE_SCREENSHOT", "false")
    import importlib

    import voice_opencode.config as cfg
    importlib.reload(cfg)
    assert cfg.settings.screenshot is False


def test_set_value_persists(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("voice_opencode.paths.CONFIG_FILE", cfg_file)
    import importlib

    import voice_opencode.config as cfg
    importlib.reload(cfg)
    cfg.set_value("speaker_id", "2")
    on_disk = json.loads(cfg_file.read_text())
    assert on_disk["speaker_id"] == 2
    assert cfg.settings.speaker_id == 2


def test_set_value_unknown_key_raises(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("voice_opencode.paths.CONFIG_FILE", cfg_file)
    import importlib

    import voice_opencode.config as cfg
    importlib.reload(cfg)
    with pytest.raises(KeyError):
        cfg.set_value("nonexistent", "x")


def test_opencode_url_property():
    from voice_opencode.config import Settings
    s = Settings(opencode_host="example.com", opencode_port=1234)
    assert s.opencode_url == "http://example.com:1234"
