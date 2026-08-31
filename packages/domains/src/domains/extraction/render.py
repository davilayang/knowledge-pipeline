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

    A list field renders one entry per line, a string as-is — split by type, not
    by field name, so the rule survives new sections.
    """
    sections = []
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        body = "\n".join(value) if isinstance(value, list) else str(value)
        sections.append(f"{field.title}:\n{body}")
    return "\n\n".join(sections)
