"""Tests for the extraction lane's shared prompt-cache prefix + JSON-mode schema block."""

from domains.extraction.schemas import TopicCard
from workflows.extraction.shared_prefix import schema_block, structured_messages


def test_schema_block_names_every_field_of_the_model():
    block = schema_block(TopicCard)
    for field in TopicCard.model_fields:
        assert field in block


def test_schema_block_carries_the_literal_word_json():
    # JSON mode 400s unless "json" appears somewhere in the messages.
    assert "json" in schema_block(TopicCard)


def _messages(task: str) -> list[dict]:
    return structured_messages(content_type="Article", content="BODY", task=task)


def test_the_two_structured_calls_share_a_byte_identical_prefix():
    # Only the trailing task message may differ — anything earlier voids the cache.
    assert _messages("TOPIC CARD TASK")[:-1] == _messages("FOLLOWUPS TASK")[:-1]


def test_the_prompt_sha_moves_when_the_schema_moves():
    """The schema is generated into the prompt, so a field added to the pydantic
    model changes what the model is told. A sha over the markdown alone would
    call those rows fresh."""
    from domains.extraction.schemas import Followups
    from workflows.extraction.shared_prefix import effective_prompt_sha

    assert effective_prompt_sha("SAME ROLE PROMPT", TopicCard) != effective_prompt_sha(
        "SAME ROLE PROMPT", Followups
    )


def test_schema_block_states_the_exact_allowed_top_level_keys():
    """The generated JSON Schema has its own top-level `description`, `title` and
    `properties` keys, and gpt-5-mini copies `description` straight into its
    reply — every run, in a live 5-for-5 check. The block has to say plainly
    which keys the OUTPUT may carry, not just show the schema."""
    block = schema_block(TopicCard)
    assert "top-level keys" in block
    for field in TopicCard.model_fields:
        assert f"`{field}`" in block
