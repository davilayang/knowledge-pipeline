# Centralized registry of (collection x retrieval_strategy) combos to evaluate.
#
# Each combo is a string in the format "collection__strategy" (double underscore).
# Add new entries as strategies are created.

from __future__ import annotations

EVAL_COMBOS: list[str] = [
    "baseline__cosine",
    "baseline__rerank",
    "bge__cosine",
    "bge__rerank",
]


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
