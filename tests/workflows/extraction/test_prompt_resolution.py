"""Pin the ExtractorRegistry prompt-resolution contract.

The registry resolves repo-root prompts/extraction/ via KP_PROMPTS_ROOT
env var (default: anchored relative path). Changing the path-anchor
math silently is a real risk — these tests catch that.
"""

import os
from pathlib import Path

import pytest


@pytest.fixture
def reload_resources():
    """Reload resources module so env-var changes take effect at import time.

    On teardown, unconditionally clears `KP_PROMPTS_ROOT` and reloads so any
    later test that imports `resources` sees `_PROMPTS_DIR` resolved against
    the real repo-root prompts/, not a tmp_path left over from this fixture.
    """
    import importlib
    import orchestrators.defs.extract_complex_contents.resources as r

    def _reload():
        importlib.reload(r)
        return r

    yield _reload

    os.environ.pop("KP_PROMPTS_ROOT", None)
    importlib.reload(r)


def test_default_prompts_dir_resolves_to_repo_root(monkeypatch, reload_resources):
    """With KP_PROMPTS_ROOT unset, _PROMPTS_DIR points at repo-root prompts/extraction/."""
    monkeypatch.delenv("KP_PROMPTS_ROOT", raising=False)
    r = reload_resources()

    assert r._PROMPTS_DIR.is_dir(), f"{r._PROMPTS_DIR} does not exist"
    assert r._PROMPTS_DIR.name == "extraction"
    assert r._PROMPTS_DIR.parent.name == "prompts"
    # Must contain at least one v5 prompt to be the right dir.
    files = list(r._PROMPTS_DIR.glob("v5_*.md"))
    assert files, f"No v5_*.md files in {r._PROMPTS_DIR}; wrong location?"


def test_kp_prompts_root_env_var_overrides_default(monkeypatch, tmp_path, reload_resources):
    """KP_PROMPTS_ROOT=/some/path makes _PROMPTS_DIR resolve under that path."""
    custom_root = tmp_path / "custom_prompts"
    (custom_root / "extraction").mkdir(parents=True)
    monkeypatch.setenv("KP_PROMPTS_ROOT", str(custom_root))

    r = reload_resources()

    assert r._PROMPTS_DIR == custom_root / "extraction"
