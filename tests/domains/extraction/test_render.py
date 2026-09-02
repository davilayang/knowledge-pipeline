"""Rendering a structured narrative back to the text the voice agent reads."""

from typing import get_origin

from domains.extraction.render import render_narrative
from domains.extraction.schemas import Narrative


def _narrative(**overrides) -> Narrative:
    fields = {
        "speakers_and_author": "Priya Raghunathan (Latchkey)",
        "structure": "one throughline - argues routing beats scale",
        "core_idea": "Measure the traffic before choosing the model.",
        "load_bearing_claims": ["Router beats scale - 61% spend cut"],
        "delivery_beats": ["Beat one\nAnchor: 61% spend cut"],
        "named_concepts_and_entities": "Priya Raghunathan, Latchkey, vLLM",
    }
    return Narrative(**{**fields, **overrides})


def test_render_narrative_puts_each_section_under_its_header():
    rendered = render_narrative(
        _narrative(
            load_bearing_claims=[
                "Model routing - a router beats one large model",
                "Latency budget - 400ms end to end, measured on an A100",
            ]
        )
    )

    assert rendered == (
        "Speakers and author:\n"
        "Priya Raghunathan (Latchkey)\n"
        "\n"
        "Structure:\n"
        "one throughline - argues routing beats scale\n"
        "\n"
        "Core idea:\n"
        "Measure the traffic before choosing the model.\n"
        "\n"
        "Load bearing claims (2):\n"
        "1. Model routing - a router beats one large model\n"
        "2. Latency budget - 400ms end to end, measured on an A100\n"
        "\n"
        "Delivery beats (1):\n"
        "1. Beat one\n"
        "Anchor: 61% spend cut\n"
        "\n"
        "Named concepts and entities:\n"
        "Priya Raghunathan, Latchkey, vLLM"
    )


def test_a_list_header_states_how_many_entries_follow_it():
    """The count is what stops a subset being delivered as the whole.

    The voice agent walks 4-6 beats selected from 9-28 claims, so after a
    complete walkthrough there is always undelivered material. Nothing in the
    prose marks that unless each section says how many entries it holds, and an
    agent that cannot see the remainder reports the beats as the piece.

    Derived from the list rather than reported by the model, so the number
    cannot disagree with what it counts.
    """
    rendered = render_narrative(
        _narrative(
            load_bearing_claims=[f"Claim {i} - anchored on figure {i}" for i in range(1, 16)]
        )
    )

    assert "Load bearing claims (15):" in rendered
    assert "\n15. Claim 15 - anchored on figure 15" in rendered


def test_a_scalar_section_is_neither_counted_nor_numbered():
    """`named_concepts_and_entities` is a list of names carried as one string.

    Counting it would report `(1)` over four names — a false count stated in the
    artefact's own convention, in the one place the agent is told to trust
    counts. The type is the model asserting "this is one thing", and the
    renderer honours it rather than overriding it.
    """
    rendered = render_narrative(
        _narrative(named_concepts_and_entities="Priya Raghunathan, Sam Okonjo, Latchkey, vLLM")
    )

    assert "Named concepts and entities:\nPriya Raghunathan, Sam Okonjo, Latchkey, vLLM" in rendered
    assert "Named concepts and entities (" not in rendered


def test_every_field_is_one_of_the_two_types_the_wire_rule_knows():
    """The consumer branches on the parsed json, this side on a pydantic value.

    Both apply the same rule — array is counted and numbered, string is not —
    but they inspect different objects, so the rule only holds while every field
    is a plain string or a plain list. A `tuple[NarrativeProse, ...]` field would
    arrive as a json array on the consumer's side and get counted, while pydantic
    hands this side a tuple, which is not a `list` and would render bare. Same
    narrative, two different texts, and nothing else would catch it.
    """
    # Checked by container rather than by exact annotation: pydantic strips the
    # `NarrativeProse` alias on a scalar field but keeps it inside a list, so the
    # two spellings differ. The container is what the rendering rule branches on.
    for name, field in Narrative.model_fields.items():
        container = get_origin(field.annotation)
        assert container in (None, list), (
            f"`{name}` is {field.annotation!r}. The cross-repo rendering rule only "
            f"covers a plain string or a plain list; anything else renders "
            f"differently in newsletter-assistant, which sees raw json."
        )
        if container is None:
            assert field.annotation is str, f"`{name}` is a bare {field.annotation!r}, not a str."


def test_every_narrative_header_is_derivable_from_its_field_name():
    """The consumer derives the same headers without importing this model.

    newsletter-assistant renders the stored json back to text on its own side,
    and does it generically — header from key, `core_idea` -> `Core idea:` —
    rather than by mirroring `Narrative`, so that adding a section here needs no
    release there. That only holds while every `title` matches what the consumer
    would derive; a title the derivation cannot produce means the two sides emit
    different headers for the same section, and the headers are the whole
    interface the voice agent steers by.

    This is why the claims section is spelled `Load bearing claims` and not
    `Load-bearing claims`: the derivation cannot produce a hyphen, and two
    spellings of one header in production is the failure this test exists to
    stop.
    """
    for name, field in Narrative.model_fields.items():
        assert field.title == name.replace("_", " ").capitalize(), (
            f"`{name}` has title {field.title!r}, but the consumer derives "
            f"{name.replace('_', ' ').capitalize()!r} from the key alone."
        )
