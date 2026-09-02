"""Render a structured extraction model back to the headed text a voice agent reads.

The narrative reaches newsletter-assistant as one `extraction_calls.output`
value and is injected into the agent's context as prose, unparsed — the section
headers are the only join. Storing it as json, which is what lets the narrative
call share a prompt-cache partition with its structured siblings, therefore
needs a step to put the text back.

Headers come from each field's `title`, walked in declaration order. No list of
section names lives here, so adding a section is a model edit — and
newsletter-assistant mirrors that rule against the raw json, so a section change
ships from this repo alone.
"""

from pydantic import BaseModel


def render_narrative(model: BaseModel) -> str:
    """`Narrative` -> the headed text the voice agent speaks.

    A list field is numbered and its header carries the entry count; a string
    field renders as-is. The split is by type, not by field name, so the rule
    survives new sections and the consumer can mirror it from the raw json
    without knowing which sections exist.

    The count is what lets the agent say "that's five of the fifteen" instead of
    reading a subset as the whole. Deriving it here rather than asking the model
    for it means it cannot disagree with the list it counts, so there is no
    arithmetic to validate — but it says nothing about whether the list is worth
    counting. A padded inventory renders a truthful count of a bad set; that is
    the prompt's problem, not this function's.
    """
    sections = []
    for name, field in type(model).model_fields.items():
        # An untitled field would render `None:` while the consumer, which
        # derives its header from the json key, renders the real name. Only
        # `Narrative` titles every field, and only `Narrative` is pinned to the
        # derivation, so anything else is out of contract rather than nearly right.
        if field.title is None:
            raise ValueError(
                f"`{name}` has no `title`, so it would render a header the consumer "
                "cannot derive. render_narrative only serves models that title "
                "every field."
            )
        value = getattr(model, name)
        if isinstance(value, list):
            header = f"{field.title} ({len(value)})"
            body = "\n".join(f"{i}. {item}" for i, item in enumerate(value, 1))
        else:
            header = field.title
            body = str(value)
        sections.append(f"{header}:\n{body}")
    return "\n\n".join(sections)
