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
