"""Resolving an extraction prompt label to the text the model is shown.

Labels, never text. A caller may name a different version of a prompt — that is
how prompt A/B runs against the real service — but it may not supply prompt
bodies, which would turn the endpoint into an injection surface and leave no
version anyone could reproduce a result from.
"""

from pathlib import Path

from domains.extraction.prompts import strip_design_notes


class UnknownPromptVersion(Exception):
    """No file backs the requested label. Raised during pre-flight, so a bad
    label in one task costs nothing rather than failing after siblings have
    already been billed."""

    def __init__(self, label: str, directory: Path) -> None:
        super().__init__(f"no prompt file {label}.md under {directory}")
        self.label = label


def load_prompt(label: str, *, prompts_root: Path) -> str:
    """Read `<prompts_root>/extraction/<label>.md`, minus its design-notes
    header — the text above the first `---` records why the prompt changed and
    must never reach the model."""
    directory = Path(prompts_root) / "extraction"
    # Reject separators outright rather than resolving them: a label is a flat
    # name, and anything else is a caller reaching outside the prompt tree.
    if "/" in label or "\\" in label or label in {"", ".", ".."}:
        raise UnknownPromptVersion(label, directory)
    path = directory / f"{label}.md"
    try:
        return strip_design_notes(path.read_text())
    except OSError as exc:
        raise UnknownPromptVersion(label, directory) from exc
