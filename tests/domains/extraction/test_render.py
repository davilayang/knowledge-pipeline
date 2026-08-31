"""Rendering a structured narrative back to the text the voice agent reads."""

from domains.extraction.render import render_narrative
from domains.extraction.schemas import Narrative


def test_render_narrative_puts_each_section_under_its_header():
    narrative = Narrative(
        salient_threads=[
            "Model routing - the piece argues a router beats one large model",
            "Latency budget - 400ms end to end, measured on an A100",
        ],
        core_idea="Routing to small specialists beats scaling one general model.",
        named_concepts_and_entities="OpenAI, vLLM, Ray Serve",
    )

    assert render_narrative(narrative) == (
        "Salient threads:\n"
        "Model routing - the piece argues a router beats one large model\n"
        "Latency budget - 400ms end to end, measured on an A100\n"
        "\n"
        "Core idea:\n"
        "Routing to small specialists beats scaling one general model.\n"
        "\n"
        "Named concepts and entities:\n"
        "OpenAI, vLLM, Ray Serve"
    )


def test_every_narrative_header_is_derivable_from_its_field_name():
    """The consumer derives the same headers without importing this model.

    newsletter-assistant renders the stored json back to text on its own side,
    and does it generically — header from key, `core_idea` -> `Core idea:` —
    rather than by mirroring `Narrative`, so that adding a section here needs no
    release there. That only holds while every `title` matches what the consumer
    would derive; a title the derivation cannot produce means the two sides emit
    different headers for the same section, and the headers are the whole
    interface the voice agent steers by.

    A section whose natural header is not derivable (a hyphen, say) fails here
    rather than drifting silently — at which point the choice is to rename the
    section or teach both sides the same explicit mapping.
    """
    for name, field in Narrative.model_fields.items():
        assert field.title == name.replace("_", " ").capitalize(), (
            f"`{name}` has title {field.title!r}, but the consumer derives "
            f"{name.replace('_', ' ').capitalize()!r} from the key alone."
        )
