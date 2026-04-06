# Curated evaluation queries with ground-truth content IDs.
#
# Ground truth is at the content_id level (not chunk_id) so it survives
# re-chunking across strategies.
#
# Curated from baseline collection on 2026-04-05 against raw_store_2026-04-05.db.
#
# Categories (v3):
#   easy               — near-verbatim title match (sanity check)
#   paraphrase         — different wording, some keyword overlap with titles
#   buried             — answer is deep in article body, not in title/heading
#   cross              — multiple articles partially answer the query
#   negative           — topic doesn't exist in corpus, should return nothing
#   lexical_gap        — casual/symptom language, zero vocabulary overlap with solutions
#   scattered_evidence — answer requires 4+ articles, one article can dominate slots
#   conversational     — natural user question with implicit need, no technical terms
#   exact_term         — rare technical terms/model names that embed poorly
#   dense_haystack     — specific fact diluted inside topically similar chunk
#   negation           — explicit negative constraint that bi-encoders can't encode

from __future__ import annotations

from dataclasses import dataclass, field

QUERY_SET_VERSION = "v3"


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
    # ===================================================================
    # NEW v3 CATEGORIES — designed to differentiate advanced strategies
    # ===================================================================
    # -----------------------------------------------------------------------
    # Lexical gap — casual/symptom language with zero vocabulary overlap
    # Target strategies: HyDE, Multi-query/Fusion, Step-back prompting
    # -----------------------------------------------------------------------
    EvalQuery(
        query="my AI keeps making stuff up and I don't know how to stop it",
        expected_content_ids=[
            # RAG architectures article covers hallucination mitigation, CRAG, grounding
            "medium::https://medium.com/@robi.tomar72/9-rag-architectures-that-save-you-months-of-dev-work-0e219eb637cb",
            # 11 strategies article covers self-RAG with verification
            "medium::https://medium.com/@gaurav21s/i-spent-3-months-building-ra-systems-before-learning-these-11-strategies-1a8f6b4278aa",
        ],
        category="lexical_gap",
    ),
    EvalQuery(
        query=(
            "how do I make my search results actually understand"
            " what users mean, not just match words"
        ),
        expected_content_ids=[
            # Advanced RAG techniques covers semantic retrieval
            "medium::https://medium.com/@yusufsevinir/11-elevating-your-ai-with-advanced-rag-techniques-a-comprehensive-guide-2eae9289defc",
            # 11 strategies covers hybrid search, reranking
            "medium::https://medium.com/@gaurav21s/i-spent-3-months-building-ra-systems-before-learning-these-11-strategies-1a8f6b4278aa",
        ],
        category="lexical_gap",
    ),
    EvalQuery(
        query=(
            "the longer I talk to the AI the worse it gets at remembering"
            " what we discussed earlier"
        ),
        expected_content_ids=[
            # Secret architecture article — context rot, memory management
            "medium::https://medium.com/@thegowtham/the-secret-architecture-that-makes-ai-remember-everything-and-it-just-beat-claude-code-ad8a95223445",
        ],
        category="lexical_gap",
    ),
    EvalQuery(
        query="our dashboards are pretty but nobody actually uses them to make decisions",
        expected_content_ids=[
            # Data warehouses + GenAI — passive dashboards to interactive platforms
            "medium::https://medium.com/@vinayakgole/i-spent-20-years-building-data-warehouses-heres-why-genai-just-changed-our-playbook-01fc14431881",
        ],
        category="lexical_gap",
    ),
    # -----------------------------------------------------------------------
    # Scattered evidence — answer requires 4+ articles from different angles
    # Target strategies: Query decomposition, MMR/diversity, Deduplication
    # -----------------------------------------------------------------------
    EvalQuery(
        query="comprehensive overview of all the ways to improve a RAG pipeline end-to-end",
        expected_content_ids=[
            # 11 strategies
            "medium::https://medium.com/@gaurav21s/i-spent-3-months-building-ra-systems-before-learning-these-11-strategies-1a8f6b4278aa",
            # 9 RAG architectures
            "medium::https://medium.com/@robi.tomar72/9-rag-architectures-that-save-you-months-of-dev-work-0e219eb637cb",
            # Advanced RAG techniques
            "medium::https://medium.com/@yusufsevinir/11-elevating-your-ai-with-advanced-rag-techniques-a-comprehensive-guide-2eae9289defc",
            # Chunking strategies
            "medium::https://medium.com/@visrow/rag-2-0-advanced-chunking-strategies-with-examples-d87d03adf6d1",
            # Pipeline vs Agentic vs KG RAG
            "medium::https://medium.com/@Micheal-Lanham/pipeline-rag-vs-agentic-rag-vs-knowledge-graph-rag-what-actually-works-and-when-47a26649a457",
        ],
        category="scattered_evidence",
    ),
    EvalQuery(
        query="how is AI changing the career path for data professionals in 2026",
        expected_content_ids=[
            # Upskilling as senior AI engineer
            "medium::https://medium.com/@arunnthevapalan/how-im-upskilling-as-a-senior-ai-engineer-in-2026-32eac7fcbf0c",
            # AI won't replace data scientists, it will replace tool-users
            "medium::https://medium.com/@datalev/ai-wont-replace-data-scientists-it-will-replace-tool-users-f8947f71e99b",
            # AI won't take your data engineering job
            "medium::https://medium.com/@reliabledataengineering/ai-wont-take-your-data-engineering-job-it-ll-make-you-unemployable-if-you-don-t-adapt-7863a16e6529",
            # Future-proofing data science career
            "medium::https://medium.com/@avourakis/the-ultimate-guide-to-future-proofing-your-data-science-career-2026-2027-5b968511e687",
        ],
        category="scattered_evidence",
    ),
    EvalQuery(
        query="what are all the different types of AI agents and how to build them",
        expected_content_ids=[
            # Production-grade AI agent guide
            "medium::https://medium.com/@sifatmusfique/building-a-production-grade-ai-agent-from-scratch-in-2026-a-principles-first-guide-5b21754dc201",
            # Agentic RAG using LangGraph
            "medium::https://medium.com/@alphaiterations/build-agentic-rag-using-langgraph-b568aa26d710",
            # Google AI agent trends
            "web::https://huryn.medium.com/google-just-revealed-5-ai-agent-trends-that-will-change-how-you-work-in-2026-22f6434f3450",
            # Anthropic agentic coding trends
            "medium::https://medium.com/@joe.njenga/this-newly-released-anthropic-agentic-coding-trends-report-is-a-must-read-0701af881148",
        ],
        category="scattered_evidence",
    ),
    # -----------------------------------------------------------------------
    # Conversational — natural user question, implicit need, no tech terms
    # Target strategies: Multi-query/Fusion, HyDE, Adaptive RAG
    # -----------------------------------------------------------------------
    EvalQuery(
        query="we built a chatbot but the answers are mediocre, where should we look first",
        expected_content_ids=[
            # 11 strategies — covers common RAG failure modes and fixes
            "medium::https://medium.com/@gaurav21s/i-spent-3-months-building-ra-systems-before-learning-these-11-strategies-1a8f6b4278aa",
            # Chunking strategies — often the root cause
            "medium::https://medium.com/@visrow/rag-2-0-advanced-chunking-strategies-with-examples-d87d03adf6d1",
        ],
        category="conversational",
    ),
    EvalQuery(
        query=(
            "I feel like I'm falling behind, what should a mid-career"
            " data person learn right now"
        ),
        expected_content_ids=[
            # Upskilling as senior AI engineer
            "medium::https://medium.com/@arunnthevapalan/how-im-upskilling-as-a-senior-ai-engineer-in-2026-32eac7fcbf0c",
            # Roadmap to senior data engineering
            "medium::https://medium.com/@ThinkingInTech/the-ultimate-3-month-roadmap-to-senior-data-engineering-9f5894d09722",
        ],
        category="conversational",
    ),
    EvalQuery(
        query=(
            "my boss wants us to use AI but just to cut costs,"
            " is that the right way to think about it"
        ),
        expected_content_ids=[
            # Andrew Ng — AI strategy beyond saving money
            "medium::https://medium.com/@intellizab/andrew-ngs-brutal-reality-check-if-your-ai-strategy-is-just-saving-money-you-re-already-dead-c5fed75d139e",
        ],
        category="conversational",
    ),
    # -----------------------------------------------------------------------
    # Exact term — rare technical terms that embed poorly in MiniLM
    # Target strategies: Hybrid BM25+vector, Cross-encoder reranking
    # -----------------------------------------------------------------------
    EvalQuery(
        query="MoE-Mamba architecture replacing transformer attention mechanism",
        expected_content_ids=[
            # Transformer architecture is being replaced
            "medium::https://medium.com/@adiinsightsinnovations/the-transformer-architecture-is-being-replaced-what-47-000-hours-of-training-data-revealed-e483c5ad7c6c",
        ],
        category="exact_term",
    ),
    EvalQuery(
        query="Liquid LFM 2.5 1.2B parameter model benchmarks",
        expected_content_ids=[
            # 1.2B model that beats giants
            "medium::https://medium.com/@mohamed-abdelmenem/you-dont-need-gpt-5-for-agents-the-1-2b-model-that-beats-giants-9ac9c3a2b626",
        ],
        category="exact_term",
    ),
    EvalQuery(
        query="voyage-context-3 contextualized chunk embedding model by MongoDB",
        expected_content_ids=[
            # Embedding model cuts vector DB costs
            "medium::https://medium.com/@avi_chawla/this-new-embedding-model-cuts-vector-db-costs-by-200x-bf6dc9ba7d56",
        ],
        category="exact_term",
    ),
    EvalQuery(
        query="PageIndex tree-search retrieval FinanceBench 98.7% accuracy",
        expected_content_ids=[
            # Vector RAG is Dead
            "medium::https://medium.com/@faisalhaque226/vector-rag-is-dead-pageindex-just-proved-it-470ea6ac446a",
        ],
        category="exact_term",
    ),
    # -----------------------------------------------------------------------
    # Dense haystack — specific fact buried in a topically matching 800t chunk
    # Target strategies: Proposition indexing, Parent-child chunking
    # -----------------------------------------------------------------------
    EvalQuery(
        query="what accuracy did naive RAG achieve before applying advanced strategies",
        expected_content_ids=[
            # 11 strategies — "My accuracy was around 60%"
            "medium::https://medium.com/@gaurav21s/i-spent-3-months-building-ra-systems-before-learning-these-11-strategies-1a8f6b4278aa",
        ],
        category="dense_haystack",
    ),
    EvalQuery(
        query="how many tokens per second does the Liquid LFM 2.5 model process",
        expected_content_ids=[
            # 1.2B model — "359 tokens/sec"
            "medium::https://medium.com/@mohamed-abdelmenem/you-dont-need-gpt-5-for-agents-the-1-2b-model-that-beats-giants-9ac9c3a2b626",
        ],
        category="dense_haystack",
    ),
    EvalQuery(
        query=(
            "what was the speed comparison between MoE-Mamba and GPT-4"
            " for 2 million token context"
        ),
        expected_content_ids=[
            # Transformer replaced — "47x faster while using 1/16th the compute"
            "medium::https://medium.com/@adiinsightsinnovations/the-transformer-architecture-is-being-replaced-what-47-000-hours-of-training-data-revealed-e483c5ad7c6c",
        ],
        category="dense_haystack",
    ),
    # -----------------------------------------------------------------------
    # Negation — explicit negative constraint bi-encoders can't encode
    # Target strategies: Hybrid BM25, Cross-encoder reranking
    # -----------------------------------------------------------------------
    EvalQuery(
        query="RAG techniques that improve retrieval quality WITHOUT any additional LLM calls",
        expected_content_ids=[
            # Chunking strategies — index-time improvement, no LLM needed
            "medium::https://medium.com/@visrow/rag-2-0-advanced-chunking-strategies-with-examples-d87d03adf6d1",
            # Embedding model cuts costs — better embeddings, no LLM
            "medium::https://medium.com/@avi_chawla/this-new-embedding-model-cuts-vector-db-costs-by-200x-bf6dc9ba7d56",
        ],
        category="negation",
    ),
    EvalQuery(
        query="AI career advice for data engineers that is NOT about becoming a data scientist",
        expected_content_ids=[
            # AI won't take your DE job
            "medium::https://medium.com/@reliabledataengineering/ai-wont-take-your-data-engineering-job-it-ll-make-you-unemployable-if-you-don-t-adapt-7863a16e6529",
            # Roadmap to senior data engineering
            "medium::https://medium.com/@ThinkingInTech/the-ultimate-3-month-roadmap-to-senior-data-engineering-9f5894d09722",
        ],
        category="negation",
    ),
    EvalQuery(
        query=(
            "approaches to document retrieval that do NOT use vector embeddings"
            " or similarity search"
        ),
        expected_content_ids=[
            # Vector RAG is Dead — PageIndex uses tree-search, no vectors
            "medium::https://medium.com/@faisalhaque226/vector-rag-is-dead-pageindex-just-proved-it-470ea6ac446a",
            # GraphRAG without graph DB — SQL ontologies
            "medium::https://medium.com/@tzvi-w/graphrag-without-a-graph-database-why-sql-ontologies-may-be-the-better-foundation-52e9b786f336",
        ],
        category="negation",
    ),
]
