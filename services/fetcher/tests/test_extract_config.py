"""The extraction model config the service actually ships.

The endpoint tests patch the loaded model, on purpose — none of them is about
which model runs. That leaves the real `config/extraction.yaml` unexercised, and
a malformed or missing one does not fail at boot: it disables `/v1/extract` and
lets every other endpoint serve, so nothing would be loud about it. These tests
are what makes it loud.
"""

from pathlib import Path

import pytest

from fetcher.extract.model import ExtractionModel, _load, get_model


REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "extraction.yaml"


def test_the_shipped_config_names_a_model_and_a_provider() -> None:
    loaded = _load(REPO_CONFIG)
    assert isinstance(loaded, ExtractionModel)
    assert loaded.model
    assert loaded.provider == "openai"


def test_the_service_loads_that_config_on_import() -> None:
    """Guards the path, not the parse: the default is relative to the working
    directory, so a config that parses in isolation can still be invisible to a
    running service."""
    assert get_model() == _load(REPO_CONFIG)


def test_a_missing_config_disables_extraction_rather_than_raising(tmp_path: Path) -> None:
    """`/v1/extract` reports itself unconfigured; fetching still works. Raising
    here would take out the whole service over one endpoint's config."""
    assert _load(tmp_path / "absent.yaml") is None


@pytest.mark.parametrize(
    "body, reason",
    [
        pytest.param("provider: openai\n", "no model", id="model_missing"),
        pytest.param("model: gpt-5-mini\n", "no provider", id="provider_missing"),
        pytest.param(
            "model: claude-opus-5\nprovider: anthropic\n",
            "a provider with no lane behind it",
            id="unimplemented_provider",
        ),
    ],
)
def test_a_config_that_could_not_run_fails_at_load(tmp_path: Path, body, reason) -> None:
    """Named at load, where the message can point at the file. Letting it through
    would surface inside a request, as an error about a client that was never
    going to be built."""
    path = tmp_path / "extraction.yaml"
    path.write_text(body)
    with pytest.raises(ValueError):
        _load(path)
