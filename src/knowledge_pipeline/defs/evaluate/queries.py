# Curated evaluation queries with ground-truth content IDs.
#
# Ground truth is at the content_id level (not chunk_id) so it survives
# re-chunking across strategies.
#
# Curated from baseline collection on 2026-04-05 against raw_store_2026-04-05.db.
# Categories:
#   easy       — near-verbatim title match (sanity check, any model should ace)
#   paraphrase — different wording, no keyword overlap with titles
#   buried     — answer is deep in article body, not in title/heading
#   cross      — multiple articles partially answer the query
#   negative   — topic doesn't exist in corpus, should return low-confidence results

from __future__ import annotations

from dataclasses import dataclass, field

QUERY_SET_VERSION = "v2"


@dataclass
class EvalQuery:
    query: str
    expected_content_ids: list[str] = field(default_factory=list)
    category: str = "easy"


EVAL_QUERIES: list[EvalQuery] = [
    # -----------------------------------------------------------------------
    # Easy — sanity checks, near-verbatim title matches
    # -----------------------------------------------------------------------
    EvalQuery(
        query="data engineering trends 2026",
        expected_content_ids=[
            "medium::https://medium.com/@khushbu.shah_661/9-data-engineering-trends-in-2026-that-will-redefine-the-modern-data-engineer-5a2c27271345",
        ],
        category="easy",
    ),
    EvalQuery(
        query="RAG retrieval augmented generation architecture",
        expected_content_ids=[
            "medium::https://medium.com/@robi.tomar72/9-rag-architectures-that-save-you-months-of-dev-work-0e219eb637cb",
            "medium::https://medium.com/@linz07m/adaptive-rag-0642973c7938",
        ],
        category="easy",
    ),
    EvalQuery(
        query="semantic caching for LLM applications",
        expected_content_ids=[
            "medium::https://medium.com/@svosh2/semantic-cache-how-to-speed-up-llm-and-rag-applications-79e74ce34d1d",
        ],
        category="easy",
    ),
    EvalQuery(
        query="Claude code source code leaked",
        expected_content_ids=[
            "medium::https://medium.com/@anhaia.gabriel/claude-codes-entire-source-code-was-just-leaked-via-npm-source-maps-here-s-what-s-inside-eb9f6a1d5ccb",
        ],
        category="easy",
    ),
    # -----------------------------------------------------------------------
    # Paraphrase — describes the problem/concept, not the solution/title
    # -----------------------------------------------------------------------
    EvalQuery(
        query="approaches to reduce repeated LLM API calls for similar user questions",
        expected_content_ids=[
            "medium::https://medium.com/@svosh2/semantic-cache-how-to-speed-up-llm-and-rag-applications-79e74ce34d1d",
        ],
        category="paraphrase",
    ),
    EvalQuery(
        query="moving from nightly batch ETL to incremental data synchronization",
        expected_content_ids=[
            "tldr_data::https://podostack.com/p/change-data-capture-cdc-intro",
        ],
        category="paraphrase",
    ),
    EvalQuery(
        query="reducing hallucinations in AI answers by verifying claims against evidence",
        expected_content_ids=[
            "medium::https://medium.com/@robi.tomar72/9-rag-architectures-that-save-you-months-of-dev-work-0e219eb637cb",
        ],
        category="paraphrase",
    ),
    EvalQuery(
        query="generate hypothetical answers to improve retrieval recall",
        expected_content_ids=[
            "medium::https://medium.com/@robi.tomar72/9-rag-architectures-that-save-you-months-of-dev-work-0e219eb637cb",
        ],
        category="paraphrase",
    ),
    EvalQuery(
        query="why do data initiatives keep failing despite large team investments",
        expected_content_ids=[
            "medium::https://medium.com/@reliabledataengineering/building-a-data-team-from-0-to-10-what-wed-do-differently-2267292821e3",
            "medium::https://medium.com/@jens-linden/data-and-ai-roadmaps-failure-by-design-0e1e7903be36",
        ],
        category="paraphrase",
    ),
    # -----------------------------------------------------------------------
    # Buried detail — answer is deep in article body, not in title/heading
    # -----------------------------------------------------------------------
    EvalQuery(
        query="which local LLM achieves best schema conformance for triple extraction on small GPU",
        expected_content_ids=[
            "medium::https://medium.com/@shereshevsky/building-knowledge-graphs-with-local-llms-from-benchmarking-to-fine-tuning-ed572268aa3f",
        ],
        category="buried",
    ),
    EvalQuery(
        query="what percentage of AI data science job postings target entry-level candidates",
        expected_content_ids=[
            "medium::https://medium.com/@avourakis/the-ultimate-guide-to-future-proofing-your-data-science-career-2026-2027-5b968511e687",
        ],
        category="buried",
    ),
    EvalQuery(
        query="Using QLoRA with dual RTX 3090 GPUs for fine-tuning 8B parameter models",
        expected_content_ids=[
            "medium::https://medium.com/@shereshevsky/building-knowledge-graphs-with-local-llms-from-benchmarking-to-fine-tuning-ed572268aa3f",
        ],
        category="buried",
    ),
    EvalQuery(
        query=(
            "What is the LLM-Map operator for processing large datasets"
            " with recursive decomposition"
        ),
        expected_content_ids=[
            "medium::https://medium.com/@thegowtham/the-secret-architecture-that-makes-ai-remember-everything-and-it-just-beat-claude-code-ad8a95223445",
        ],
        category="buried",
    ),
    # -----------------------------------------------------------------------
    # Cross-article — answer spans multiple articles from different angles
    # -----------------------------------------------------------------------
    EvalQuery(
        query="how should the role of data scientists change now that LLMs handle routine analysis",
        expected_content_ids=[
            "medium::https://medium.com/@reliabledataengineering/we-hired-10-data-scientists-they-spent-3-years-cleaning-csvs-fcc58df236ea",
            "medium::https://medium.com/@avourakis/the-ultimate-guide-to-future-proofing-your-data-science-career-2026-2027-5b968511e687",
        ],
        category="cross",
    ),
    EvalQuery(
        query="what does it actually mean for data to be ready to feed into an AI system",
        expected_content_ids=[
            "medium::https://medium.com/@sauravsinghsisodiya/what-ai-ready-data-actually-means-and-why-most-teams-get-it-wrong-fd0fe7a0b649",
            "medium::https://medium.com/@khushbu.shah_661/9-data-engineering-trends-in-2026-that-will-redefine-the-modern-data-engineer-5a2c27271345",
        ],
        category="cross",
    ),
    EvalQuery(
        query="why organizations fail at becoming data-driven despite hiring analysts",
        expected_content_ids=[
            "medium::https://medium.com/@reliabledataengineering/we-hired-10-data-scientists-they-spent-3-years-cleaning-csvs-fcc58df236ea",
            "medium::https://medium.com/@reliabledataengineering/building-a-data-team-from-0-to-10-what-wed-do-differently-2267292821e3",
        ],
        category="cross",
    ),
    EvalQuery(
        query=(
            "When should I stop using simple vector search and add"
            " something smarter to my retrieval"
        ),
        expected_content_ids=[
            "medium::https://pub.towardsai.net/i-spent-3-months-building-ra-systems-before-learning-these-11-strategies-1a8f6b4278aa",
            "medium::https://medium.com/@robi.tomar72/9-rag-architectures-that-save-you-months-of-dev-work-0e219eb637cb",
            "medium::https://medium.com/@linz07m/adaptive-rag-0642973c7938",
        ],
        category="cross",
    ),
    # -----------------------------------------------------------------------
    # Negative — topic doesn't exist in corpus, expected_content_ids is empty.
    # A correct system returns nothing with high confidence.
    # -----------------------------------------------------------------------
    EvalQuery(
        query="Python libraries for building recommendation systems and collaborative filtering",
        expected_content_ids=[],
        category="negative",
    ),
    EvalQuery(
        query="Apache Kafka consumer group lag monitoring and alerting",
        expected_content_ids=[],
        category="negative",
    ),
    EvalQuery(
        query="how to fine-tune an LLM for better SQL generation",
        expected_content_ids=[],
        category="negative",
    ),
]
