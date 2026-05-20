"""Pytest fixtures shared across the suite."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `import voice_opencode` work without installing the package.
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """
    Redirect runtime + repo paths into a temp directory so tests don't
    touch the real $XDG_RUNTIME_DIR or config.json. Reloads modules
    that captured paths at import time.
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    # Force module reload so paths.STATE_DIR picks up the new env var.
    import importlib

    import voice_opencode.paths as paths_mod
    importlib.reload(paths_mod)

    import voice_opencode.state as state_mod
    importlib.reload(state_mod)

    import voice_opencode.agent as agent_mod
    importlib.reload(agent_mod)

    # opencode_client captures SESSION_FILE at import time; reload so
    # it picks up the redirected path.
    import voice_opencode.opencode_client as oc_mod
    importlib.reload(oc_mod)

    # Drop any cached LLM backend so get_backend() rebuilds against
    # the freshly reloaded modules.
    import voice_opencode.llm as llm_mod
    importlib.reload(llm_mod)
    llm_mod.reset_backend_cache()

    paths_mod.ensure_dirs()
    return paths_mod
