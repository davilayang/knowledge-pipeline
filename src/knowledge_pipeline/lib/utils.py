# Shared utility functions.

import hashlib
from pathlib import Path

_STRATEGIES_YAML: dict | None = None


def hash_file(path) -> str:
    """SHA-256 hash of a file (first 16 hex chars)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _load_strategies_yaml() -> dict:
    """Load and cache strategies.yaml."""
    global _STRATEGIES_YAML  # noqa: PLW0603
    if _STRATEGIES_YAML is None:
        import yaml

        yaml_path = Path(__file__).resolve().parent.parent / "strategies.yaml"
        with open(yaml_path) as f:
            _STRATEGIES_YAML = yaml.safe_load(f)
    return _STRATEGIES_YAML


def get_strategy(name: str) -> dict:
    """Load an index strategy config by name from strategies.yaml."""
    strategies = _load_strategies_yaml()["index_strategies"]
    if name not in strategies:
        available = ", ".join(sorted(strategies))
        raise ValueError(f"Unknown index strategy: {name!r}. Available: {available}")
    return {"strategy_name": name, **strategies[name]}


def get_eval_combos() -> list[str]:
    """Load eval combos from strategies.yaml."""
    return _load_strategies_yaml()["eval_combos"]


def get_embedding_model_for_collection(collection_name: str) -> str:
    """Look up the embedding model for a collection name."""
    for cfg in _load_strategies_yaml()["index_strategies"].values():
        if cfg["collection_name"] == collection_name:
            return cfg["embedding_model"]
    raise ValueError(f"No strategy found for collection: {collection_name!r}")
