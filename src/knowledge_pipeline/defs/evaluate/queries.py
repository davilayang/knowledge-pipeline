# Curated evaluation queries with ground-truth content IDs.
#
# Ground truth is at the content_id level (not chunk_id) so it survives
# re-chunking across strategies.
#
# Curated from baseline collection on 2026-04-05 against raw_store_2026-04-05.db.

from __future__ import annotations

from dataclasses import dataclass, field

QUERY_SET_VERSION = "v1"


@dataclass
class EvalQuery:
    query: str
    expected_content_ids: list[str] = field(default_factory=list)
    category: str = "general"  # factual, topical, broad, specific


EVAL_QUERIES: list[EvalQuery] = [
    EvalQuery(
        query="data engineering trends 2026",
        expected_content_ids=[
            "medium::https://medium.com/@khushbu.shah_661/9-data-engineering-trends-in-2026-that-will-redefine-the-modern-data-engineer-5a2c27271345",
            "medium::https://medium.com/@avourakis/the-ultimate-guide-to-future-proofing-your-data-science-career-2026-2027-5b968511e687",
        ],
        category="topical",
    ),
    EvalQuery(
        query="RAG retrieval augmented generation architecture",
        expected_content_ids=[
            "medium::https://medium.com/@robi.tomar72/9-rag-architectures-that-save-you-months-of-dev-work-0e219eb637cb",
            "medium::https://medium.com/@linz07m/adaptive-rag-0642973c7938",
            "medium::https://pub.towardsai.net/i-spent-3-months-building-ra-systems-before-learning-these-11-strategies-1a8f6b4278aa",
        ],
        category="topical",
    ),
    EvalQuery(
        query="AI coding assistant tools for data engineering",
        expected_content_ids=[
            "medium::https://medium.com/@reliabledataengineering/ai-code-assistants-for-data-engineering-i-tested-6-tools-for-sql-and-python-e20183f3154e",
            "medium::https://medium.com/@sandunlakshan213/10-modern-agentic-ai-tools-developers-should-explore-in-2026-27ce3b08e2e1",
        ],
        category="topical",
    ),
    EvalQuery(
        query="Python automation scripts",
        expected_content_ids=[
            "medium::https://medium.com/@abromohsin504/9-python-automations-that-quietly-make-money-while-you-sleep-2d1c7781bad1",
        ],
        category="specific",
    ),
    EvalQuery(
        query="scalable data pipeline architecture",
        expected_content_ids=[
            "medium::https://medium.com/@fareedkhandev/building-a-scalable-production-grade-agentic-rag-pipeline-1168dcd36260",
            "medium::https://medium.com/@Rohan_Dutt/10-future-trends-in-genai-for-data-engineering-pipelines-2dc1a6aba8c9",
        ],
        category="topical",
    ),
    EvalQuery(
        query="LLM fine tuning knowledge graphs",
        expected_content_ids=[
            "medium::https://medium.com/@shereshevsky/building-knowledge-graphs-with-local-llms-from-benchmarking-to-fine-tuning-ed572268aa3f",
        ],
        category="specific",
    ),
    EvalQuery(
        query="career roadmap for data engineers",
        expected_content_ids=[
            "medium::https://medium.com/@avourakis/the-ultimate-guide-to-future-proofing-your-data-science-career-2026-2027-5b968511e687",
            "medium::https://medium.com/@ThinkingInTech/the-ultimate-3-month-roadmap-to-senior-data-engineering-9f5894d09722",
        ],
        category="broad",
    ),
    EvalQuery(
        query="agentic AI tools for developers",
        expected_content_ids=[
            "medium::https://medium.com/@sandunlakshan213/10-modern-agentic-ai-tools-developers-should-explore-in-2026-27ce3b08e2e1",
        ],
        category="specific",
    ),
    EvalQuery(
        query="Claude code source code leaked",
        expected_content_ids=[
            "medium::https://medium.com/@anhaia.gabriel/claude-codes-entire-source-code-was-just-leaked-via-npm-source-maps-here-s-what-s-inside-eb9f6a1d5ccb",
        ],
        category="factual",
    ),
    EvalQuery(
        query="semantic caching for LLM applications",
        expected_content_ids=[
            "medium::https://medium.com/@svosh2/semantic-cache-how-to-speed-up-llm-and-rag-applications-79e74ce34d1d",
        ],
        category="specific",
    ),
    EvalQuery(
        query="how to build production RAG pipeline",
        expected_content_ids=[
            "medium::https://medium.com/@fareedkhandev/building-a-scalable-production-grade-agentic-rag-pipeline-1168dcd36260",
            "medium::https://medium.com/@robi.tomar72/9-rag-architectures-that-save-you-months-of-dev-work-0e219eb637cb",
        ],
        category="broad",
    ),
    EvalQuery(
        query="GenAI impact on data engineering pipelines",
        expected_content_ids=[
            "medium::https://medium.com/@Rohan_Dutt/10-future-trends-in-genai-for-data-engineering-pipelines-2dc1a6aba8c9",
        ],
        category="topical",
    ),
]
