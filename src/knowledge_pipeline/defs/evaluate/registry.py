# Centralized registry of (collection x retrieval_strategy) combos to evaluate.
# Combos are defined in strategies.yaml and loaded via config.get_eval_combos().

from __future__ import annotations

from knowledge_pipeline.config import get_eval_combos

EVAL_COMBOS: list[str] = get_eval_combos()


def parse_combo(combo: str) -> tuple[str, str]:
    """Parse a combo string into (collection_name, strategy_spec).

    Args:
        combo: String in format "collection__strategy".

    Returns:
        Tuple of (collection_name, strategy_spec).

    Raises:
        ValueError: If the combo string is malformed.
    """
    parts = combo.split("__", maxsplit=1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Malformed eval combo: {combo!r}. Expected format: 'collection__strategy'"
        )
    return parts[0], parts[1]
