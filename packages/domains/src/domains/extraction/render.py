"""Render a structured extraction model back to the headed text a voice agent reads.

The narrative crosses to newsletter-assistant as one `extraction_calls.output`
value and is injected into the agent's context as prose — nothing parses it, and
the section headers are the only join. Storing it as json (so the narrative call
shares a prompt-cache partition with its two structured siblings) therefore needs
a rendering step to put the text back.

The renderer walks the model's fields **in declaration order** and takes each
header from the field's `title`. It holds no list of section names, so adding a
section is a model edit and nothing else — which matters because
newsletter-assistant mirrors this logic against the raw json, and a hard-coded
section list there would turn every future section change into a coordinated
cross-repo release.
"""

from pydantic import BaseModel


def render_narrative(model: BaseModel) -> str:
    """`Narrative` -> the headed text stored for, and spoken by, the voice agent.

    A list field renders one entry per line; a string field renders as-is. The
    split is by type rather than by field name, so the rule survives new
    sections. Sections are separated by a blank line.
    """
    sections = []
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        body = "\n".join(value) if isinstance(value, list) else str(value)
        sections.append(f"{field.title}:\n{body}")
    return "\n\n".join(sections)
