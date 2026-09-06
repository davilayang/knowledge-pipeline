"""Which model the extraction lane runs, read from `config/extraction.yaml`.

Declared in a file rather than an environment variable, matching the structurer
and whisper lanes next door: the model is part of what this service *is*, and
should not differ between a laptop and production the way a credential must.
The API key still comes from the environment.

Loaded once at import, like the sibling chain configs — a hot-edited file with
no restart leaves the process running the old model, which keeps the value
consistent with the results already cached under it during that process's life.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionModel:
    """The backend one extraction call runs against."""

    model: str
    provider: str


def _load(path: Path) -> ExtractionModel | None:
    """Parse the extraction model config. Returns None if the file is missing,
    which leaves `/v1/extract` reporting itself unconfigured rather than the
    service failing to boot — every other endpoint still serves."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("extraction config not found at %s; /v1/extract disabled", path)
        return None
    model = data.get("model")
    provider = data.get("provider")
    if not model or not provider:
        raise ValueError(f"{path} must declare both `model` and `provider`")
    if provider != "openai":
        # The only lane implemented. Failing here names the file to fix, where
        # letting it through would fail later inside a request with a message
        # about a client that was never going to be built.
        raise ValueError(f"{path} declares unsupported provider {provider!r}")
    return ExtractionModel(model=str(model), provider=str(provider))


_MODEL: ExtractionModel | None = _load(
    Path(os.environ.get("FETCHER_EXTRACTION_CONFIG_PATH", "config/extraction.yaml"))
)


def get_model() -> ExtractionModel | None:
    return _MODEL
