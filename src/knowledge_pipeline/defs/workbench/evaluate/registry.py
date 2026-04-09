# Centralized registry of (collection x retrieval_strategy) combos to evaluate.
# Combos are defined in strategies.yaml and loaded via config.get_eval_combos().

from __future__ import annotations

from knowledge_pipeline.lib.utils import get_eval_combos

EVAL_COMBOS: list[str] = get_eval_combos()


def get_combos_by_collection() -> dict[str, list[tuple[str, str]]]:
    """Group eval combos by collection name.

    Returns:
        {"baseline": [("baseline", "cosine"), ("baseline", "rerank"), ...], ...}
    """
    grouped: dict[str, list[tuple[str, str]]] = {}
    for combo in EVAL_COMBOS:
        coll, strat = parse_combo(combo)
        grouped.setdefault(coll, []).append((coll, strat))
    return grouped


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
