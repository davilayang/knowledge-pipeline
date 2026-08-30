import hashlib
import json
import textwrap
import time
from datetime import date
from typing import Any
from urllib.parse import urlparse

import dagster as dg
from domains.types import IngestItem
from domains.wiki.claims import parse_claims_doc, render_claims
from workflows.extraction.metadata import MetadataPayload
from workflows.extraction.metadata import extract_metadata as run_extract_metadata
from workflows.extraction.shared_prefix import effective_prompt_sha
from workflows.wiki_synthesis.extract_claims import SPOKEN_CONTENT_TYPES
from workflows.wiki_synthesis.extract_claims import extract_claims as run_extract_claims
from workflows.wiki_synthesis.extract_entities import extract_entities as run_extract_entities
from workflows.wiki_synthesis.extract_entities import render_candidates
from workflows.wiki_synthesis.prompts import (
    EXTRACT_ARTICLE_ENVELOPE,
    EXTRACT_CLAIMS_TASK,
    EXTRACT_ENTITIES_TASK,
    EXTRACT_SHARED_SYSTEM,
)

from orchestrators.config import FETCH_EXTRACT_QUEUE_DAG_VERSION
from orchestrators.defs.shared.queue_resources import NotionQueueResource, QueueStoreResource

# enrichment_json is written by triage and read here for the youtube channel;
# EnrichmentSignals IS that serialisation contract, so it is imported rather than
# the JSON re-parsed by hand.
from orchestrators.defs.triage_knowledge_queue.enrich import EnrichmentSignals

from .def_config import (
    PIPELINE_TAG,
    PROMPT_LABEL_METADATA,
    queue_items_partition_def,
)
from .resources import ExtractorRegistry, FetcherResource, read_extraction_prompt

GROUP_NAME = "fetch_extract_queue"

# Per-prompt staleness handle for the extract-claims extraction_calls rows — hashes
# every static prompt part that shapes a claims call (shared system + article
# envelope template + claims task tail), so an edit to any of them bumps the
# recorded prompt_sha256.
_EXTRACT_CLAIMS_PROMPT_SHA = hashlib.sha256(
    (EXTRACT_SHARED_SYSTEM + EXTRACT_ARTICLE_ENVELOPE + EXTRACT_CLAIMS_TASK).encode()
).hexdigest()

# Same handle for the extract-entities rows — shared system + article envelope +
# the entities task tail (all static prompt parts that shape an entities call).
_EXTRACT_ENTITIES_PROMPT_SHA = hashlib.sha256(
    (EXTRACT_SHARED_SYSTEM + EXTRACT_ARTICLE_ENVELOPE + EXTRACT_ENTITIES_TASK).encode()
).hexdigest()

_PREVIEW_HEAD = 500
_PREVIEW_TAIL = 500


def _oneline(s: str) -> str:
    """Collapse a multi-line source string into a single-paragraph string.

    Lets us write Dagster `description=` blocks as readable multi-line source
    while the rendered string in the Dagster UI stays a single paragraph."""
    return " ".join(textwrap.dedent(s).split())


def _preview(content: str, *, head: int = _PREVIEW_HEAD, tail: int = _PREVIEW_TAIL) -> str:
    """Head + tail preview with an ellipsis marker for the middle.

    Returns the full content if shorter than head + tail. Otherwise returns
    a string of shape `{head}\n\n... [N chars omitted] ...\n\n{tail}` so the
    UI shows the start and end without flooding the page on multi-KB content."""
    if len(content) <= head + tail:
        return content
    omitted = len(content) - head - tail
    return f"{content[:head]}\n\n... [{omitted:,} chars omitted] ...\n\n{content[-tail:]}"


def _ingest_item_from_row(row: dict[str, Any]) -> IngestItem:
    """Build the IngestItem the claim extractor reads from a fetched queue row.

    `item_id` is the canonical URL (the content's stable identity), falling back
    to the captured URL; title/author/content_date come from the persisted
    fetcher metadata, and the body is `raw_content`. `content_type` is read
    separately by the asset (it is not an IngestItem field).

    Note the key choice: source summaries are keyed by `canonical_url`, not the
    raw_store `<source>::<url>` content_id used by the older wiki ingest path.
    The attributed-lane entity writer reads source summaries (this pipeline's
    output), so it aligns on `canonical_url` — the two paths are not reconciled
    by item_id."""
    content_date = row.get("content_date")
    return IngestItem(
        item_id=row.get("canonical_url") or row["url"],
        title=row.get("title") or "",
        date=date.fromisoformat(content_date) if content_date else None,
        text=row.get("raw_content") or "",
        source_type="queue",
        source_ref=row["notion_page_id"],
        author=row.get("author"),
    )


def _coerce_author(authors: Any) -> str | None:
    """Normalise the fetcher's `extras["authors"]` to a clean string or None for
    the queue `author` column. A list joins on ", "; anything falsy (None, empty
    list, empty string) becomes None so "no author" is consistently NULL, never
    an empty string or `"[]"`."""
    if isinstance(authors, list):
        return ", ".join(str(a) for a in authors) or None
    return str(authors) if authors else None


def comments_json_to_user_notes(raw: str | None) -> str | None:
    """Turn the stored `user_comments_json` into the bullet-list string the
    extractor wraps in its `[reader's notes]` block. Returns None when there
    are no non-empty comments, so the extractor's no-comment path runs."""
    if not raw:
        return None
    texts = [
        (c.get("text") or "").strip() for c in json.loads(raw) if (c.get("text") or "").strip()
    ]
    return "\n".join(f"- {t}" for t in texts) if texts else None


@dg.asset(
    key=["fetch_extract_queue", "fetch_content"],
    group_name=GROUP_NAME,
    kinds={"http", "sqlite"},  # from http to sqlite
    code_version=FETCH_EXTRACT_QUEUE_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    retry_policy=dg.RetryPolicy(max_retries=1, delay=120),
    description=_oneline(
        """
        POSTs to the fetcher service's /v1/fetch and persists the returned
        markdown + tier_log to queue_items. Cache: skips when raw_content
        is already set.
        """
    ),
)
def fetch_content(
    context: dg.AssetExecutionContext,
    fetcher: FetcherResource,
    store: QueueStoreResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    notion_url = f"https://www.notion.so/{page_id.replace('-', '')}"
    store.ensure_schema()
    row = store.get_row(page_id)
    if not row:
        raise dg.Failure(
            description=(
                f"No local queue_items row for Notion page {notion_url}. "
                f"Triage did not seed this page — likely a manual Status flip, "
                f"a wrong NOTION_QUEUE_DB_ID, or a queue.db restored before this row."
            ),
            allow_retries=False,
            metadata={
                "notion_url": dg.MetadataValue.url(notion_url),
                "notion_page_id": dg.MetadataValue.text(page_id),
            },
        )
    content_type = row.get("content_type")
    if not content_type:
        raise dg.Failure(
            description=(
                f"queue_items row for {notion_url} has no Content Type. "
                f"Set one in Notion, or flip Status back to Queued so triage reclassifies."
            ),
            allow_retries=False,
            metadata={
                "notion_url": dg.MetadataValue.url(notion_url),
                "notion_page_id": dg.MetadataValue.text(page_id),
            },
        )

    if row.get("raw_content") and row.get("url"):
        url = row["url"]
        canonical = row.get("canonical_url") or ""
        return dg.MaterializeResult(
            metadata={
                "content_type": dg.MetadataValue.text(content_type),
                "url": dg.MetadataValue.url(url),
                "canonical_url": dg.MetadataValue.text(canonical),
                "fetch_skipped": dg.MetadataValue.bool(True),
                "existing_content_chars": dg.MetadataValue.int(
                    row.get("fetched_content_char_count") or 0
                ),
                "content_preview": dg.MetadataValue.md(f"```\n{_preview(row['raw_content'])}\n```"),
                "summary": dg.MetadataValue.md(
                    "Skipped — raw_content already cached for this URL."
                ),
            }
        )

    url = row.get("canonical_url") or ""
    if not url:
        raise dg.Failure(
            description=(
                f"queue_items row for {notion_url} has no canonical_url. "
                f"Triage did not resolve it — flip Status back to Queued to re-triage."
            ),
            allow_retries=False,
            metadata={
                "notion_url": dg.MetadataValue.url(notion_url),
                "notion_page_id": dg.MetadataValue.text(page_id),
                "raw_url": dg.MetadataValue.text(row.get("url") or ""),
            },
        )

    override = row.get("raw_content_override") or ""
    if override:
        result = fetcher.structure(override, source_url=url)
    else:
        result = fetcher.fetch_for_type(url, content_type=content_type)
    if result.error:
        raise dg.Failure(
            description=f"{content_type} fetch failed: {result.error}",
            allow_retries=result.transient,
            metadata={
                "content_type": dg.MetadataValue.text(content_type),
                "url": dg.MetadataValue.url(url),
                "fetch_tier": dg.MetadataValue.text(result.tier),
                "tier_log": dg.MetadataValue.json(result.tier_log),
            },
        )

    char_count = len(result.content)
    # The fetcher cascade falls back to `best_result` when no tier hits its
    # floor (services/fetcher/cascade.py), so a 200 can carry sub-floor
    # content. Guard the extractor against degenerate inputs before persist.
    if char_count < 500:
        raise dg.Failure(
            description=f"{content_type} fetch below extraction floor: {char_count} chars",
            allow_retries=False,
            metadata={
                "content_type": dg.MetadataValue.text(content_type),
                "url": dg.MetadataValue.url(url),
                "fetch_tier": dg.MetadataValue.text(result.tier),
                "content_chars": dg.MetadataValue.int(char_count),
                "tier_log": dg.MetadataValue.json(result.tier_log),
            },
        )
    content_hash = hashlib.sha256(result.content.encode()).hexdigest()
    extras = result.extras or {}
    author = _coerce_author(extras.get("authors"))
    published = extras.get("published")
    store.upsert_fetched(
        notion_page_id=page_id,
        url=url,
        raw_content=result.content,
        fetch_tier=result.tier,
        fetch_tier_log=result.tier_log,
        fetched_content_char_count=char_count,
        content_hash=content_hash,
        title=result.title or None,
        author=author,
        content_date=str(published) if published else None,
    )

    metadata: dict[str, dg.MetadataValue] = {
        "content_type": dg.MetadataValue.text(content_type),
        "url": dg.MetadataValue.url(url),
        "canonical_url": dg.MetadataValue.text(row.get("canonical_url") or ""),
        "fetch_tier": dg.MetadataValue.text(result.tier),
        "content_chars": dg.MetadataValue.int(char_count),
        "tier_log": dg.MetadataValue.json(result.tier_log),
        "content_hash_short": dg.MetadataValue.text(content_hash[:12]),
        "content_preview": dg.MetadataValue.md(f"```\n{_preview(result.content)}\n```"),
        "summary": dg.MetadataValue.md(f"**{content_type}** — {char_count:,} chars"),
    }
    if result.title:
        metadata["title"] = dg.MetadataValue.text(result.title)
    if extras.get("authors"):
        metadata["authors"] = dg.MetadataValue.text(str(extras["authors"]))
    if extras.get("published"):
        metadata["published"] = dg.MetadataValue.text(str(extras["published"]))
    if extras.get("arxiv_id"):
        metadata["arxiv_id"] = dg.MetadataValue.text(str(extras["arxiv_id"]))
    return dg.MaterializeResult(metadata=metadata)


def _github_owner(url: str | None) -> str | None:
    """Owner segment of a github URL — `langchain-ai` from
    `github.com/langchain-ai/langgraph`. None for a bare host or a reserved path."""
    if not url:
        return None
    parts = [seg for seg in urlparse(url).path.split("/") if seg]
    if not parts or parts[0] in {"orgs", "settings", "features", "about"}:
        return None
    return parts[0]


def _deterministic_publisher(row: dict[str, Any]) -> str | None:
    """The publisher a non-LLM source already knows, or None to let the model decide.

    Only two are unambiguous: oEmbed's `author_name` for youtube IS the channel,
    and a github repo's owner is a fixed URL segment. An HTML site name never
    qualifies — `article` is the fetcher's catch-all, so `og:site_name` reads
    Substack or Reddit as often as a real publication, and storing the platform
    buries the publication that ran the piece."""
    match (row.get("content_type") or "").lower():
        case "youtube":
            signals = EnrichmentSignals.from_json(row.get("enrichment_json"))
            return signals.youtube.channel if signals.youtube else None
        case "github":
            return _github_owner(row.get("canonical_url") or row.get("url"))
        case _:
            return None


def _metadata_inputs_sha(*, content_hash: str | None, model: str, prompt_sha: str) -> str:
    """One hash over everything that decides what this call returns.

    Populated columns are not enough to skip on: a re-fetch replaces the body, a
    prompt edit changes the question, a model swap changes the answerer — each
    leaving the columns in place. Anything left out lets the corpus hold two
    incomparable populations with nothing to tell them apart."""
    return hashlib.sha256("\n".join((content_hash or "", model, prompt_sha)).encode()).hexdigest()


def _metadata_is_fresh(last_call: dict[str, Any] | None, inputs_sha: str) -> bool:
    """True when the last recorded call read exactly these inputs. The hash rides
    on the call row because it is a fact about one call, and `queue_items` has no
    per-column extraction timestamp."""
    if not last_call:
        return False
    try:
        recorded = json.loads(last_call.get("node_metadata") or "{}")
    except ValueError:
        # Shared reserved slot: a future producer's row must not raise here, on a
        # path whose whole contract is to never fail.
        return False
    if not isinstance(recorded, dict):
        return False
    return recorded.get("inputs_sha") == inputs_sha


@dg.asset(
    key=["fetch_extract_queue", "extract_metadata"],
    group_name=GROUP_NAME,
    kinds={"openai", "sqlite"},
    code_version=FETCH_EXTRACT_QUEUE_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[fetch_content],
    check_specs=[
        dg.AssetCheckSpec(
            name="metadata_columns_populated",
            asset=dg.AssetKey(["fetch_extract_queue", "extract_metadata"]),
            blocking=False,
            description=(
                "A row with a fetched body carries contributors_json and publisher. "
                "Non-blocking, so an unwritten row is visible without gating the two "
                "extraction branches that depend on this asset."
            ),
        ),
    ],
    description=_oneline(
        """
        Reads the fetched body once and captures who made it and who
        published it. Sits upstream of both extraction branches, since a field
        the narrative call emitted would be invisible to the claims branch.
        Best-effort: any failure writes nothing and materialises anyway.
        Nothing reads these columns yet — they exist to be measured first.
        """
    ),
)
def extract_metadata(
    context: dg.AssetExecutionContext,
    extractor: ExtractorRegistry,
    store: QueueStoreResource,
):
    page_id = context.partition_key
    check_name = "metadata_columns_populated"
    # The WHOLE body is guarded, not just the model call: both extract branches
    # depend on this asset, so any exception here — missing prompt file, locked
    # queue.db, unreadable ledger row — stops the reading card and the claims lane
    # too. A missing metadata row costs nothing; a blocked extraction costs the
    # item. Every write precedes the first yield, so the failure path cannot emit
    # a second materialisation.
    try:
        # Migrates the schema it writes to: this asset can be materialised alone
        # (a backfill over stored bodies is exactly that), so it cannot rely on a
        # sibling having run first.
        store.ensure_schema()
        row = store.get_row(page_id)
        if not row or not row.get("raw_content"):
            yield dg.MaterializeResult(metadata={"metadata_skipped": dg.MetadataValue.bool(True)})
            # Nothing was expected of a body-less row, so this is not a finding.
            yield dg.AssetCheckResult(check_name=check_name, passed=True)
            return

        prompt = read_extraction_prompt(PROMPT_LABEL_METADATA)
        prompt_sha = effective_prompt_sha(prompt, MetadataPayload)
        inputs_sha = _metadata_inputs_sha(
            content_hash=row.get("content_hash"),
            model=extractor.model,
            prompt_sha=prompt_sha,
        )
        if _metadata_is_fresh(
            store.get_latest_extraction_calls(page_id).get("metadata"), inputs_sha
        ):
            yield dg.MaterializeResult(
                metadata={
                    "metadata_skipped": dg.MetadataValue.bool(True),
                    "summary": dg.MetadataValue.md("Skipped — same body, prompt and model."),
                }
            )
            yield dg.AssetCheckResult(check_name=check_name, passed=True)
            return

        started = time.monotonic()
        payload, call = run_extract_metadata(
            row["raw_content"],
            content_type=row.get("content_type") or "",
            prompt=prompt,
            model=extractor.model,
        )
        duration_ms = (time.monotonic() - started) * 1000

        known_publisher = _deterministic_publisher(row)
        contributors = [c.model_dump() for c in payload.contributors]

        store.record_metadata(
            notion_page_id=page_id,
            contributors_json=json.dumps(contributors),
            # On a disagreement the deterministic value wins and the model's
            # survives in the ledger row's `output`, which holds the whole reply.
            publisher=known_publisher or payload.publisher,
            prompt_label=PROMPT_LABEL_METADATA,
            prompt_sha256=prompt_sha,
            model=call.model,
            output=call.content,
            tokens_in=call.input_tokens,
            tokens_out=call.output_tokens,
            cached_tokens=call.cached_tokens,
            duration_ms=duration_ms,
            content_hash=row.get("content_hash"),
            inputs_sha=inputs_sha,
        )
        store.checkpoint_wal()
    except Exception as exc:
        context.log.warning("extract_metadata failed for %s: %r", page_id, exc)
        reason = dg.MetadataValue.text(f"{type(exc).__name__}: {exc}"[:1000])
        yield dg.MaterializeResult(
            metadata={
                "metadata_error": reason,
                "summary": dg.MetadataValue.md("No metadata written — see metadata_error."),
            }
        )
        yield dg.AssetCheckResult(
            check_name=check_name,
            passed=False,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"metadata_error": reason},
        )
        return

    yield dg.MaterializeResult(
        metadata={
            "content_type": dg.MetadataValue.text(row.get("content_type") or "(none)"),
            "contributors": dg.MetadataValue.int(len(contributors)),
            "publisher": dg.MetadataValue.text(known_publisher or payload.publisher or ""),
            "publisher_source": dg.MetadataValue.text(
                "deterministic" if known_publisher else "model"
            ),
            "model": dg.MetadataValue.text(call.model),
            "cached_tokens": dg.MetadataValue.int(call.cached_tokens),
            "duration_ms": dg.MetadataValue.int(int(duration_ms)),
            "payload": dg.MetadataValue.json({"contributors": contributors}),
            "summary": dg.MetadataValue.md(
                f"**{len(contributors)} contributors** — "
                f"publisher {known_publisher or payload.publisher or '(none)'}"
            ),
        }
    )
    yield dg.AssetCheckResult(check_name=check_name, passed=True)


@dg.asset(
    key=["fetch_extract_queue", "extract_reading_card"],
    group_name=GROUP_NAME,
    kinds={"openai", "sqlite"},
    code_version=FETCH_EXTRACT_QUEUE_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["fetch_extract_queue", "extract_metadata"])],
    check_specs=[
        dg.AssetCheckSpec(
            name="topic_card_has_required_fields",
            asset=dg.AssetKey(["fetch_extract_queue", "extract_reading_card"]),
            blocking=True,
            description=(
                "Topic Card has extracted_title + core_mechanism, AND Followups "
                "has at least 4 questions."
            ),
        ),
    ],
    description=_oneline(
        """
        Runs the three-call extractor via ExtractorRegistry (v2:
        ThreeCallOpenAIExtractor — narrative, then topic_card, then
        followups, sequentially in one Dagster op so the structured pair
        shares the article's prompt cache). Persists one row per call into
        extraction_calls + updates queue_items cohort fields atomically.
        Future LangGraph swap is a one-class change inside the registry;
        no asset edits required.
        """
    ),
)
def extract_reading_card(
    context: dg.AssetExecutionContext,
    extractor: ExtractorRegistry,
    store: QueueStoreResource,
):
    page_id = context.partition_key
    row = store.get_row(page_id)
    if not row or not row.get("raw_content"):
        raise dg.Failure(
            description=f"No raw_content for page_id={page_id}; fetch_content must produce one.",
        )
    content_type = row["content_type"]

    # Build the extractor ONCE per materialization. It owns an AsyncOpenAI
    # client closed at the end of `.extract()`; calling `build()` again
    # would leak a fresh httpx pool. Reads of `bundle_label` /
    # `bundle_sha256` / `model` below are property accesses on this
    # instance — no extra client construction.
    ex = extractor.build()
    # Read the user-visible / classifier-emitted content_shape written by
    # the triaged asset. NULL (legacy rows, or rows the classifier returned
    # `unknown` for) falls back to the unknown bundle inside the extractor
    # — bundle selection is the extractor's concern.
    content_shape = row.get("content_shape") or "unknown"
    user_notes = comments_json_to_user_notes(row.get("user_comments_json"))
    payload, calls = ex.extract(
        content=row["raw_content"],
        content_type=content_type,
        content_shape=content_shape,
        user_notes=user_notes,
    )

    tokens_in_total = sum(c.tokens_in for c in calls)
    tokens_out_total = sum(c.tokens_out for c in calls)
    by_kind = {c.call_kind: c for c in calls}

    # One figure, not two: the three calls run one after another, so model time
    # and user-visible latency are the same number.
    total_model_time_ms = int(sum((v.duration_ms or 0) for v in by_kind.values()))

    store.record_extraction_calls(
        notion_page_id=page_id,
        extractor_label=ex.bundle_label,
        extractor_sha256=ex.bundle_sha256(content_shape),
        model=ex.model,
        calls=calls,
        tokens_in_total=tokens_in_total,
        tokens_out_total=tokens_out_total,
    )
    # Fold -wal sidecar into the main queue.db, allowing readers to read all rows
    store.checkpoint_wal()

    topic_card = payload.topic_card
    followups = payload.followups
    passed = (
        bool(topic_card.extracted_title)
        and bool(topic_card.core_mechanism)
        and len(followups.questions) >= 4
    )

    yield dg.MaterializeResult(
        metadata={
            "content_type": dg.MetadataValue.text(content_type),
            "content_shape": dg.MetadataValue.text(content_shape),
            "extractor_label": dg.MetadataValue.text(ex.bundle_label),
            "extractor_sha256_short": dg.MetadataValue.text(ex.bundle_sha256(content_shape)[:12]),
            "extraction_model": dg.MetadataValue.text(ex.model),
            "extracted_title": dg.MetadataValue.text(topic_card.extracted_title),
            "narrative_chars": dg.MetadataValue.int(len(payload.narrative_md)),
            "followups_count": dg.MetadataValue.int(len(followups.questions)),
            "tokens_in_total": dg.MetadataValue.int(tokens_in_total),
            "tokens_out_total": dg.MetadataValue.int(tokens_out_total),
            "total_model_time_ms": dg.MetadataValue.int(total_model_time_ms),
            "prompt_sha_short_narrative": dg.MetadataValue.text(
                by_kind["narrative"].prompt_sha256[:12]
            ),
            "prompt_sha_short_topic_card": dg.MetadataValue.text(
                by_kind["topic_card"].prompt_sha256[:12]
            ),
            "prompt_sha_short_followups": dg.MetadataValue.text(
                by_kind["followups"].prompt_sha256[:12]
            ),
            "narrative_preview": dg.MetadataValue.md(f"```\n{_preview(payload.narrative_md)}\n```"),
            "topic_card_preview": dg.MetadataValue.md(
                f"```json\n{_preview(topic_card.model_dump_json(indent=2))}\n```"
            ),
            "summary": dg.MetadataValue.md(
                f"**{topic_card.extracted_title}** — 3-call extraction, "
                f"{len(followups.questions)} chips, {total_model_time_ms}ms model"
            ),
        }
    )
    yield dg.AssetCheckResult(
        check_name="topic_card_has_required_fields",
        passed=passed,
        severity=dg.AssetCheckSeverity.ERROR,
        metadata={
            "extracted_title": dg.MetadataValue.text(topic_card.extracted_title),
            "followups_count": dg.MetadataValue.int(len(followups.questions)),
        },
    )


@dg.asset(
    key=["fetch_extract_queue", "publish_item"],
    group_name=GROUP_NAME,
    kinds={"notion", "sqlite"},
    code_version=FETCH_EXTRACT_QUEUE_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["fetch_extract_queue", "extract_reading_card"])],
    description=_oneline(
        """
        Flips Notion Status=Ready and overwrites Name with the extracted
        title + Description with the extracted core_mechanism — both
        materially sharper than the trafilatura HTML meta triage seeded.
        Isolated from extraction so a Notion API hiccup can be retried
        without re-spending an OpenAI extraction. Verifies
        queue_items.extracted_at is set as a guard against bad ordering.
        Ready means the reading card is ready — it does not mean every
        branch finished: this asset depends only on extract_reading_card,
        so claims and entities may still be running or may later fail.
        """
    ),
)
def publish_item(
    context: dg.AssetExecutionContext,
    notion: NotionQueueResource,
    store: QueueStoreResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    row = store.get_row(page_id)
    if not row or not row.get("extracted_at"):
        raise dg.Failure(
            description=(
                f"No extraction completed for page_id={page_id} — extract_reading_card "
                "must run first."
            ),
            metadata={
                "content_type": dg.MetadataValue.text((row or {}).get("content_type") or "(none)"),
            },
        )
    topic_card = store.get_latest_topic_card(page_id)
    core_mechanism = topic_card.core_mechanism if topic_card else None
    extracted_title = topic_card.extracted_title if topic_card else None
    notion.update_status(
        page_id,
        "Ready",
        description=core_mechanism,
        name=extracted_title,
        # Surface the content-published date in Notion — the user's own date if
        # they set one (idempotent), else the fetcher-discovered date filling a
        # previously-blank field. NULL only if neither source produced a date.
        published_date=row.get("content_date"),
    )
    return dg.MaterializeResult(
        metadata={
            "notion_page_id": dg.MetadataValue.text(page_id),
            "content_type": dg.MetadataValue.text(row.get("content_type") or "(none)"),
            "extracted_at": dg.MetadataValue.text(row.get("extracted_at") or ""),
            "extracted_title": dg.MetadataValue.text(extracted_title or ""),
            "core_mechanism": dg.MetadataValue.text(core_mechanism or ""),
            "summary": dg.MetadataValue.md("Notion → Ready"),
        }
    )


@dg.asset(
    key=["fetch_extract_queue", "extract_claims"],
    group_name=GROUP_NAME,
    kinds={"sqlite"},
    code_version=FETCH_EXTRACT_QUEUE_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[extract_metadata],
    description=_oneline(
        """
        Extracts claims from the fetched body into per-source [reported]/[opinion] claims
        (primed for transcripts by content_type) and records it as a extract_claims extraction_calls
        row — the attributed-lane wiki substrate. Skips when no body is fetched.
        """
    ),
)
def extract_claims(
    context: dg.AssetExecutionContext,
    store: QueueStoreResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    row = store.get_row(page_id)
    if not row or not row.get("raw_content"):
        return dg.MaterializeResult(metadata={"summary_skipped": dg.MetadataValue.bool(True)})

    item = _ingest_item_from_row(row)
    content_type = (row.get("content_type") or "").lower()
    summary, call = run_extract_claims(item, content_type=content_type)
    store.record_claims(
        notion_page_id=page_id,
        output=render_claims(summary),
        prompt_label="extract_claims_v2",
        prompt_sha256=_EXTRACT_CLAIMS_PROMPT_SHA,
        model=call.model,
        tokens_in=call.input_tokens,
        tokens_out=call.output_tokens,
    )
    opinion = sum(c.speculative for c in summary.claims)
    primed = content_type in SPOKEN_CONTENT_TYPES
    return dg.MaterializeResult(
        metadata={
            "item_id": dg.MetadataValue.text(item.item_id),
            "content_type": dg.MetadataValue.text(content_type or "(none)"),
            "spoken_prime": dg.MetadataValue.bool(primed),
            "claims": dg.MetadataValue.int(len(summary.claims)),
            "opinion": dg.MetadataValue.int(opinion),
            "summary": dg.MetadataValue.md(
                f"**{len(summary.claims)} claims** ({opinion} opinion) — "
                f"{content_type or 'unknown'}{', spoken prime' if primed else ''}"
            ),
        }
    )


@dg.asset(
    key=["fetch_extract_queue", "extract_entities"],
    group_name=GROUP_NAME,
    kinds={"openai", "sqlite"},
    code_version=FETCH_EXTRACT_QUEUE_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[extract_claims],
    description=_oneline(
        """
        Extracts article-grounded entity candidates from the fetched body + its
        extracted claims (shared prompt-cache prefix with extract_claims, so the
        article can be served from cache — best-effort), and records them as an
        extract_entities extraction_calls row — the attributed-lane candidate set
        assign_summary resolves against the live wiki. Skips when no body or no
        recorded claims row.
        """
    ),
)
def extract_entities(
    context: dg.AssetExecutionContext,
    store: QueueStoreResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    row = store.get_row(page_id)
    if not row or not row.get("raw_content"):
        return dg.MaterializeResult(metadata={"entities_skipped": dg.MetadataValue.bool(True)})
    # Claims are this asset's article-companion input — extract_claims (a dep)
    # records them and primes the shared article cache. We skip only when NO claims
    # row exists (extract_claims hasn't run for this page); a recorded-but-empty
    # claims doc still runs — the article is the primary input, claims are only a
    # salience signal, so `(no claims extracted)` is a valid, article-grounded pass.
    claims_doc = store.get_claims(page_id)
    if not claims_doc:
        return dg.MaterializeResult(metadata={"entities_skipped": dg.MetadataValue.bool(True)})

    item = _ingest_item_from_row(row)
    candidates, call = run_extract_entities(item, parse_claims_doc(claims_doc))
    store.record_candidates(
        notion_page_id=page_id,
        output=render_candidates(candidates),
        prompt_label="extract_entities_v2",
        prompt_sha256=_EXTRACT_ENTITIES_PROMPT_SHA,
        model=call.model,
        tokens_in=call.input_tokens,
        tokens_out=call.output_tokens,
        cached_tokens=call.cached_tokens,
    )
    by_type: dict[str, int] = {}
    for c in candidates:
        by_type[c.entity_type] = by_type.get(c.entity_type, 0) + 1
    return dg.MaterializeResult(
        metadata={
            "item_id": dg.MetadataValue.text(item.item_id),
            "candidates": dg.MetadataValue.int(len(candidates)),
            "cached_tokens": dg.MetadataValue.int(call.cached_tokens),
            "input_tokens": dg.MetadataValue.int(call.input_tokens),
            "by_type": dg.MetadataValue.json(by_type),
            "summary": dg.MetadataValue.md(
                f"**{len(candidates)} candidates** — "
                f"article cache {call.cached_tokens}/{call.input_tokens} tokens"
            ),
        }
    )


# The partitioned per-source assets feed the per-row fetch_extract_queue_job. The
# wiki-write lane (persist + render) moved to the synthesize_wiki DAG — the store
# seam: this pipeline writes queue.db + Notion only; wiki.db writes live there.
all_assets = [
    fetch_content,
    extract_metadata,
    extract_reading_card,
    publish_item,
    extract_claims,
    extract_entities,
]
