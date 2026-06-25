"""FaithfulnessJudge — grounds a synthesised page's claims against its sources.

The judge LLM is injected as `chat_fn` (Callable[[str], dict]); tests pass a stub
returning a fixed claim analysis, so no real LLM runs.
"""

from evals.wiki.judges import FaithfulnessJudge


def _stub_three_claims(_prompt: str) -> dict:
    """Pretend the judge LLM decomposed the page into 3 claims, 1 unsupported."""
    return {
        "claims": [
            {
                "text": "Alice founded Acme in 2010.",
                "supported": True,
                "evidence": "Acme, founded 2010 by Alice",
            },
            {
                "text": "Acme is based in Berlin.",
                "supported": True,
                "evidence": "headquartered in Berlin",
            },
            {"text": "Acme has 5000 employees.", "supported": False, "evidence": None},
        ]
    }


def test_counts_unsupported_and_grounded_fraction():
    judge = FaithfulnessJudge(chat_fn=_stub_three_claims)

    score = judge.score(page="<page md>", sources=["<source article>"])

    assert score.unsupported_count == 1
    assert score.grounded_fraction == 2 / 3


def test_update_grounds_against_prior_sources_too():
    """An updated page carries claims from earlier sources; the judge must see
    the prior sources or it falsely flags those claims unsupported (codex BLOCKER 2)."""
    captured: dict[str, str] = {}

    def _capture(prompt: str) -> dict:
        captured["prompt"] = prompt
        return {"claims": []}

    judge = FaithfulnessJudge(chat_fn=_capture)
    judge.score(
        page="<page md>",
        sources=["NEW-SOURCE-TEXT"],
        prior_sources=["OLD-SOURCE-TEXT"],
    )

    assert "NEW-SOURCE-TEXT" in captured["prompt"]
    assert "OLD-SOURCE-TEXT" in captured["prompt"]
