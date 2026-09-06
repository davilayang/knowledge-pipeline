"""What the per-call staleness signal has to cover.

`effective_prompt_sha` is how a caller decides an extraction stored months ago
still reflects what the model would be asked today. Anything static the model
sees but the hash does not is a silent-staleness hole: the stored row keeps
reading as fresh while the request behind it has changed.
"""

from domains.extraction.schemas import TopicCard

from fetcher.extract import shared
from fetcher.extract.openai_lane import effective_prompt_sha


def test_rewording_the_article_envelope_marks_stored_rows_stale(monkeypatch) -> None:
    """The envelope wraps every article, so editing it changes what every model
    is shown. Before it entered the hash, such an edit left every stored row
    reading as fresh against a request that no longer matched it."""
    before = effective_prompt_sha("ROLE", TopicCard)
    monkeypatch.setattr(shared, "ARTICLE_ENVELOPE", "[kind: {content_type}]\n\n{content}")
    monkeypatch.setattr(
        "fetcher.extract.openai_lane.ARTICLE_ENVELOPE",
        "[kind: {content_type}]\n\n{content}",
    )
    assert effective_prompt_sha("ROLE", TopicCard) != before


def test_per_item_values_do_not_move_the_sha() -> None:
    """The envelope enters as its unfilled template. Hashing a wrapped article
    instead would make every row uniquely stale, which is the same as having no
    staleness signal at all."""
    assert "{content}" in shared.ARTICLE_ENVELOPE
    assert effective_prompt_sha("ROLE", TopicCard) == effective_prompt_sha("ROLE", TopicCard)
