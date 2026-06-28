# Queue Comments as Captured Extraction Artifact — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture a reader's Notion page comments on a queued item and surface them verbatim as a structured `reader_threads` field on the extractor's followups call — without biasing the canonical source-grounded extraction.

**Architecture:** Comments are read at the `triaged` asset via the Notion comments API, stored verbatim in a new `queue_items.user_comments_json` column (cohort-scoped, wiped+rewritten on re-triage), then read by the `extracted` asset and passed to the three-call extractor as a new `user_notes` param. Only the followups call consumes them — appending a labeled `[reader's notes]` block to its user message and a fold instruction to its system prompt, populating a new optional `Followups.reader_threads` field. Narrative and topic-card calls are untouched. The no-comment path is byte-identical to today.

**Tech Stack:** Python 3.13, `uv` workspaces, Dagster (orchestrators only), OpenAI SDK (`AsyncOpenAI`), Pydantic v2, SQLite, `notion-client`, pytest + `unittest.mock`.

## Global Constraints

- **Python 3.13** — do NOT add `from __future__ import annotations`.
- **Package manager is `uv`** — run everything via `uv run ...`; tests via `uv run poe test`, full gate via `uv run poe check`.
- **Mocks patch at the import location**, not the source location.
- **No TDD for LLM output quality** — the *mechanics* (param threading, sha computation, message construction, conditional schema) get TDD; the *quality* of `reader_threads` content is validated empirically on the eval cohort, never by asserting model output in a unit test.
- **Per-call `prompt_sha256` = hash of the ACTUAL system prompt that ran** (option B): base `.md` text for the no-comment path (unchanged); `base + fold instruction` for the comment path.
- **Cohort `extractor_sha256` stays base-only** — do NOT fold reader-threads into it (it's a per-item runtime condition, not a deploy property).
- **Do NOT bump any `*_DAG_VERSION`** — no-comment output is byte-identical, so existing materializations stay valid.
- **Dependency rule**: `domains` imports nothing internal; `workflows` may import `domains`; `orchestrators` may import anything. Nothing outside `orchestrators` imports `dagster`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `packages/orchestrators/src/orchestrators/defs/shared/queue_resources.py` | Notion I/O | add `get_page_comments` |
| `packages/domains/src/domains/queue_store/sources.py` | queue.db layer | add `user_comments_json` column + migration + `upsert_triaged` param |
| `packages/orchestrators/src/orchestrators/defs/triage_knowledge_queue/assets.py` | triage asset | read comments, persist via `upsert_triaged` |
| `packages/domains/src/domains/extraction/schemas.py` | extraction schemas | add `Followups.reader_threads` |
| `packages/workflows/src/workflows/extraction/protocol.py` | extractor contract | add `user_notes` param |
| `packages/workflows/src/workflows/extraction/three_call_openai.py` | three-call extractor | conditional followups notes-block + fold + option-B sha |
| `packages/orchestrators/src/orchestrators/defs/fetch_extract_queue/assets.py` | extract asset | read `user_comments_json`, pass `user_notes` |

**Prerequisite (manual, not code):** the Notion integration token must have the **"Read comments"** capability enabled, or `comments.list` returns empty. Flag this in the execution handoff.

---

### Task 1: Notion `get_page_comments`

**Files:**
- Modify: `packages/orchestrators/src/orchestrators/defs/shared/queue_resources.py` (add method after `get_page_body_markdown`, ~line 265)
- Test: `tests/shared/test_queue_resources.py` (create if absent)

**Interfaces:**
- Produces: `NotionQueueResource.get_page_comments(self, page_id: str) -> list[dict[str, str]]` — each dict has keys `author`, `text`, `created_at`; comments with empty text are dropped; empty page → `[]`.

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import MagicMock, patch

from orchestrators.defs.shared.queue_resources import NotionQueueResource


def _resource() -> NotionQueueResource:
    return NotionQueueResource(
        integration_token="t", queue_db_id="d", queue_data_source_id="s"
    )


def test_get_page_comments_extracts_text_author_time():
    res = _resource()
    client = MagicMock()
    client.comments.list.return_value = {
        "results": [
            {
                "rich_text": [{"plain_text": "focus on the "}, {"plain_text": "chunking"}],
                "created_by": {"id": "u1"},
                "created_time": "2026-06-28T10:00:00.000Z",
            },
            {"rich_text": [], "created_by": {"id": "u1"}, "created_time": "t2"},
        ],
        "has_more": False,
    }
    with patch.object(res, "_client", return_value=client):
        out = res.get_page_comments("p1")
    assert out == [
        {"author": "u1", "text": "focus on the chunking", "created_at": "2026-06-28T10:00:00.000Z"}
    ]
    client.comments.list.assert_called_once_with(block_id="p1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shared/test_queue_resources.py::test_get_page_comments_extracts_text_author_time -v`
Expected: FAIL — `AttributeError: ... has no attribute 'get_page_comments'`

- [ ] **Step 3: Write minimal implementation**

Add to `NotionQueueResource` (mirrors `get_page_body_markdown`'s pagination):

```python
def get_page_comments(self, page_id: str) -> list[dict[str, str]]:
    """Fetch all unresolved comments on a page, newest-first as Notion returns
    them. Each entry: {author (Notion user id), text (concatenated rich_text),
    created_at (ISO-8601)}. Comments whose text is empty after strip are
    dropped. Empty/commentless page → []. Requires the integration token to
    hold the 'Read comments' capability, else Notion returns no results."""
    client = self._client()
    out: list[dict[str, str]] = []
    cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {"block_id": page_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.comments.list(**kwargs)
        for c in resp.get("results") or []:
            text = "".join(rt.get("plain_text") or "" for rt in c.get("rich_text") or []).strip()
            if not text:
                continue
            out.append(
                {
                    "author": (c.get("created_by") or {}).get("id") or "",
                    "text": text,
                    "created_at": c.get("created_time") or "",
                }
            )
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
        if not cursor:
            break
    return out
```

(`Any` is already imported in this module — it's used by `get_page_body_markdown`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/shared/test_queue_resources.py::test_get_page_comments_extracts_text_author_time -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/orchestrators/src/orchestrators/defs/shared/queue_resources.py tests/shared/test_queue_resources.py
git commit -m "feat(queue): NotionQueueResource.get_page_comments"
```

---

### Task 2: `user_comments_json` column + `upsert_triaged` capture

**Files:**
- Modify: `packages/domains/src/domains/queue_store/sources.py` (`_SCHEMA` ~line 36-58; `create_schema` ALTER loop ~line 147-159; `upsert_triaged` ~line 192-259)
- Test: `tests/domains/queue_store/test_sources.py` (existing — reuse the `db_path` fixture at lines 83-87)

**Interfaces:**
- Produces: `upsert_triaged(..., user_comments_json: str | None = None)` — stores the value; on re-triage, the supplied value (including `None`) overwrites the prior one (cohort-scoped wipe-and-rewrite). `get_row(...)` returns the `user_comments_json` key.

- [ ] **Step 1: Write the failing tests**

```python
def test_upsert_triaged_stores_user_comments_json(db_path):
    upsert_triaged(
        db_path=db_path, notion_page_id="p1", url="u", canonical_url="c",
        content_type="Article", user_comments_json='[{"text": "focus X"}]',
    )
    row = get_row(db_path=db_path, notion_page_id="p1")
    assert row["user_comments_json"] == '[{"text": "focus X"}]'


def test_retriage_without_comments_wipes_user_comments_json(db_path):
    upsert_triaged(
        db_path=db_path, notion_page_id="p1", url="u", canonical_url="c",
        content_type="Article", user_comments_json='[{"text": "focus X"}]',
    )
    upsert_triaged(  # re-triage, no comments this pass
        db_path=db_path, notion_page_id="p1", url="u", canonical_url="c",
        content_type="Article", user_comments_json=None,
    )
    row = get_row(db_path=db_path, notion_page_id="p1")
    assert row["user_comments_json"] is None
```

(`upsert_triaged` and `get_row` are already imported at the top of this test file.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/domains/queue_store/test_sources.py -k user_comments -v`
Expected: FAIL — `TypeError: upsert_triaged() got an unexpected keyword argument 'user_comments_json'`

- [ ] **Step 3: Implement — schema, migration, upsert**

In `_SCHEMA`, add the column to the `queue_items` CREATE (after `raw_content_override`):

```python
    raw_content_override        TEXT NOT NULL DEFAULT '',  -- user-pasted body
    user_comments_json          TEXT,              -- verbatim Notion comments; cohort-scoped
```

In `create_schema`, add to the idempotent ADD loop (after the `enrichment_json` ADD):

```python
            "ALTER TABLE queue_items ADD COLUMN user_comments_json TEXT",
```

In `upsert_triaged`, add the parameter (after `raw_content_override`):

```python
    raw_content_override: str = "",
    user_comments_json: str | None = None,
```

Add `user_comments_json` to the INSERT column list and VALUES, and to the `DO UPDATE SET` block (alongside `raw_content_override = excluded.raw_content_override,`):

```python
            INSERT INTO queue_items (
                notion_page_id, url, canonical_url, content_type, content_shape,
                raw_content_override, user_comments_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(notion_page_id) DO UPDATE SET
                url = excluded.url,
                canonical_url = excluded.canonical_url,
                content_type = excluded.content_type,
                content_shape = excluded.content_shape,
                raw_content_override = excluded.raw_content_override,
                user_comments_json = excluded.user_comments_json,
                raw_content = NULL,
```

…and append `user_comments_json` to the params tuple (after `raw_content_override`):

```python
            (
                notion_page_id,
                url,
                canonical_url,
                content_type,
                content_shape,
                raw_content_override,
                user_comments_json,
            ),
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/domains/queue_store/test_sources.py -k user_comments -v`
Expected: PASS (both)

- [ ] **Step 5: Run the full queue_store suite (migration regression guard)**

Run: `uv run pytest tests/domains/queue_store/test_sources.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add packages/domains/src/domains/queue_store/sources.py tests/domains/queue_store/test_sources.py
git commit -m "feat(queue-store): user_comments_json column on queue_items, captured in upsert_triaged"
```

---

### Task 3: Triage reads + stores comments

**Files:**
- Modify: `packages/orchestrators/src/orchestrators/defs/triage_knowledge_queue/assets.py` (the `triaged` asset, around the `upsert_triaged` call at lines 283-290)
- Test: `tests/triage_knowledge_queue/test_assets.py` (existing — reuse `_resources` and `_materialize` helpers)

**Interfaces:**
- Consumes: `NotionQueueResource.get_page_comments` (Task 1), `upsert_triaged(..., user_comments_json=...)` (Task 2).
- Produces: after triage, the row's `user_comments_json` holds the JSON-serialized comments (or `None` when there are none).

- [ ] **Step 1: Write the failing test**

```python
import json

from domains.queue_store.sources import get_row


def test_triaged_persists_page_comments(tmp_path):
    resources, notion = _resources(tmp_path)
    notion.get_page_comments.return_value = [
        {"author": "u1", "text": "focus on chunking", "created_at": "t1"}
    ]
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://example.com/article",
    )
    assert result.success
    notion.get_page_comments.assert_called_once_with("p-1")
    row = get_row(db_path=tmp_path / "q.db", notion_page_id="p-1")
    assert json.loads(row["user_comments_json"]) == [
        {"author": "u1", "text": "focus on chunking", "created_at": "t1"}
    ]


def test_triaged_stores_null_when_no_comments(tmp_path):
    resources, notion = _resources(tmp_path)
    notion.get_page_comments.return_value = []
    result = _materialize(
        partition_key="p-2", resources=resources, url="https://example.com/x"
    )
    assert result.success
    row = get_row(db_path=tmp_path / "q.db", notion_page_id="p-2")
    assert row["user_comments_json"] is None
```

> Note: `_resources` returns a `MagicMock` Notion; `get_page_comments` is therefore auto-mocked. If the existing `_resources` helper sets specific return values, leave them; only `get_page_comments.return_value` is set per-test here.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/triage_knowledge_queue/test_assets.py -k page_comments -v`
Expected: FAIL — `user_comments_json` is `None` in the first test (asset doesn't read/store comments yet), or AssertionError on `get_page_comments.assert_called_once`.

- [ ] **Step 3: Implement — read comments, serialize, pass to upsert**

In the `triaged` asset, just before the `triage_store.upsert_triaged(...)` call (line ~283), add:

```python
    comments = triage_notion.get_page_comments(page_id)
    user_comments_json = json.dumps(comments) if comments else None
```

Then add the kwarg to the existing `upsert_triaged` call:

```python
    triage_store.upsert_triaged(
        notion_page_id=page_id,
        url=config.url,
        canonical_url=canonical,
        content_type=content_type,
        content_shape=content_shape,
        raw_content_override=config.raw_content_override,
        user_comments_json=user_comments_json,
    )
```

Ensure `import json` is present at the top of the module (add if missing).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/triage_knowledge_queue/test_assets.py -k page_comments -v`
Expected: PASS (both)

- [ ] **Step 5: Run the full triage suite (regression guard)**

Run: `uv run pytest tests/triage_knowledge_queue/test_assets.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add packages/orchestrators/src/orchestrators/defs/triage_knowledge_queue/assets.py tests/triage_knowledge_queue/test_assets.py
git commit -m "feat(triage): read Notion page comments and persist to queue_items"
```

---

### Task 4: `Followups.reader_threads` field

**Files:**
- Modify: `packages/domains/src/domains/extraction/schemas.py` (`Followups`, lines 67-83)
- Test: `tests/domains/extraction/test_schemas.py` (create if absent)

**Interfaces:**
- Produces: `Followups.reader_threads: list[str]` — defaults to `[]`; existing `Followups(questions=[...])` construction is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
from domains.extraction.schemas import Followups

_Q = ["a?", "b?", "c?", "d?"]


def test_followups_reader_threads_defaults_empty():
    assert Followups(questions=_Q).reader_threads == []


def test_followups_accepts_reader_threads():
    f = Followups(questions=_Q, reader_threads=["compare with dbt"])
    assert f.reader_threads == ["compare with dbt"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/domains/extraction/test_schemas.py -k reader_threads -v`
Expected: FAIL — `test_followups_accepts_reader_threads` raises `ValidationError` (unexpected field) under Pydantic, or the field is missing.

- [ ] **Step 3: Implement**

Add to `Followups` (after the `questions` field):

```python
    reader_threads: list[str] = Field(
        default_factory=list,
        description=(
            "The reader's own threads, extracted ONLY from the "
            "[reader's notes] block in the user message — a focus they "
            "asked for, an open-loop/action, or context they gave. Empty "
            "when no reader notes are present. Never source claims; never "
            "invented; never treat a note as a fact stated by the source."
        ),
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/domains/extraction/test_schemas.py -k reader_threads -v`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add packages/domains/src/domains/extraction/schemas.py tests/domains/extraction/test_schemas.py
git commit -m "feat(extraction): optional reader_threads field on Followups"
```

---

### Task 5: Extractor `user_notes` — followups notes-block + fold + option-B sha

**Files:**
- Modify: `packages/workflows/src/workflows/extraction/protocol.py` (lines 18-21)
- Modify: `packages/workflows/src/workflows/extraction/three_call_openai.py` (`extract`, `_extract_async`, `_structured_call`; add a module constant)
- Test: `tests/fetch_extract_queue/test_three_call_extractor.py` (existing — reuse `extractor` fixture, `_topic_card_obj`, `_followups_obj`, `_create_resp`, `_parse_resp`)

**Interfaces:**
- Consumes: `Followups.reader_threads` (Task 4).
- Produces: `extract(self, content, *, content_type, content_shape, user_notes: str | None = None)`. When `user_notes` is set: the followups call's system prompt gets the fold instruction appended, its user message gets a labeled `[reader's notes — NOT part of the source article]` block, and its recorded `prompt_sha256` is `_sha(base_text + fold_instruction)`. Narrative and topic-card calls are unaffected. When `user_notes` is None/empty: byte-identical to current behavior.

- [ ] **Step 1: Write the failing tests**

```python
def _wire_client_capturing(captured, create_text, topic_obj, followups_obj):
    """Like _wire_client but records the `messages` passed to each call,
    keyed by call kind, so tests can assert on the constructed prompts."""
    client = MagicMock()

    async def _create(*, model, max_tokens, messages):
        captured["narrative"] = messages
        return _create_resp(create_text)

    async def _parse(*, model, max_tokens, messages, response_format):
        if response_format is TopicCard:
            captured["topic_card"] = messages
            return _parse_resp(topic_obj)
        if response_format is Followups:
            captured["followups"] = messages
            return _parse_resp(followups_obj)
        raise AssertionError(f"unexpected response_format: {response_format}")

    client.chat.completions.create = AsyncMock(side_effect=_create)
    client.beta.chat.completions.parse = AsyncMock(side_effect=_parse)
    client.close = AsyncMock()
    return client


def _followups_sha(extractor, *, user_notes):
    captured = {}
    client = _wire_client_capturing(captured, "# n", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        _payload, calls = extractor.extract(
            content="raw", content_type="Article", content_shape="unknown",
            user_notes=user_notes,
        )
    by_kind = {c.call_kind: c for c in calls}
    return captured, by_kind


def test_no_user_notes_leaves_followups_unchanged(extractor):
    captured, _ = _followups_sha(extractor, user_notes=None)
    assert "reader's notes" not in captured["followups"][1]["content"]
    assert "reader_threads" not in captured["followups"][0]["content"]


def test_user_notes_injected_only_into_followups(extractor):
    captured, _ = _followups_sha(extractor, user_notes="- compare with dbt")
    # followups user message carries the labeled notes block verbatim
    fu_user = captured["followups"][1]["content"]
    assert "[reader's notes — NOT part of the source article]" in fu_user
    assert "compare with dbt" in fu_user
    # followups system prompt carries the fold instruction
    assert "reader_threads" in captured["followups"][0]["content"]
    # topic_card + narrative are untouched
    assert "reader's notes" not in captured["topic_card"][1]["content"]
    assert "reader's notes" not in captured["narrative"][1]["content"]


def test_followups_sha_reflects_notes_topic_card_does_not(extractor):
    _, base = _followups_sha(extractor, user_notes=None)
    _, noted = _followups_sha(extractor, user_notes="- compare with dbt")
    assert noted["followups"].prompt_sha256 != base["followups"].prompt_sha256
    assert noted["topic_card"].prompt_sha256 == base["topic_card"].prompt_sha256
```

(`TopicCard`, `Followups`, `AsyncMock`, `MagicMock`, `patch` are already imported in this test file per the existing `_wire_client`.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/fetch_extract_queue/test_three_call_extractor.py -k "user_notes or notes_injected or sha_reflects" -v`
Expected: FAIL — `extract()` rejects the `user_notes` kwarg (`TypeError`).

- [ ] **Step 3: Implement**

Add a module-level constant near the top of `three_call_openai.py` (after `_GENERIC_SHAPE`):

```python
_READER_THREADS_FOLD = (
    "\n\n---\n"
    "The user message may include a `[reader's notes — NOT part of the source "
    "article]` block: the reader's own annotations, NOT source content. Populate "
    "`reader_threads` with each note restated as the reader's own thread (a focus "
    "they asked for, an open-loop/action, or context they gave). Never answer reader "
    "notes from the source, never invent threads, and never treat a note as a fact "
    "stated by the source. Leave `reader_threads` empty if the block is absent."
)
```

Thread `user_notes` through `extract` and `_extract_async`:

```python
    def extract(
        self, content: str, *, content_type: str, content_shape: str,
        user_notes: str | None = None,
    ) -> tuple[ExtractionPayload, list[ExtractionCallRecord]]:
        """Sync wrapper. Dagster ops run in their own threads, so asyncio.run
        does not collide with the daemon's event loop."""
        return asyncio.run(
            self._extract_async(
                content=content,
                content_type=content_type,
                content_shape=content_shape,
                user_notes=user_notes,
            )
        )

    async def _extract_async(
        self, *, content: str, content_type: str, content_shape: str,
        user_notes: str | None = None,
    ) -> tuple[ExtractionPayload, list[ExtractionCallRecord]]:
```

In `_extract_async`, change only the followups gather arm to pass the notes + fold (topic-card arm unchanged):

```python
            topic_result, followups_result = await asyncio.gather(
                self._structured_call(
                    content, content_type, bundle["topic_card"],
                    TopicCard, "topic_card", resolved_shape,
                ),
                self._structured_call(
                    content, content_type, bundle["followups"],
                    Followups, "followups", resolved_shape,
                    user_notes=user_notes or None,
                ),
                return_exceptions=True,
            )
```

Extend `_structured_call` to honor `user_notes` (option-B sha + notes block):

```python
    async def _structured_call(
        self,
        content: str,
        content_type: str,
        prompt_triple: _RoleTriple,
        schema: type,
        call_kind: str,
        resolved_shape: str,
        *,
        user_notes: str | None = None,
    ) -> tuple[Any, ExtractionCallRecord]:
        prompt_text, prompt_label, prompt_sha = prompt_triple
        user_content = f"[content_type: {content_type}]\n\n{content}"
        if user_notes:
            # Option B: the recorded sha reflects the ACTUAL system prompt that
            # ran, so a future edit to the fold instruction flags comment-bearing
            # rows stale; the no-comment path keeps the base sha untouched.
            prompt_text = prompt_text + _READER_THREADS_FOLD
            prompt_sha = _sha(prompt_text)
            user_content += (
                "\n\n[reader's notes — NOT part of the source article]\n" + user_notes
            )
        t0 = time.monotonic()
        resp = await self._client.beta.chat.completions.parse(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": user_content},
            ],
            response_format=schema,
        )
        duration_ms = (time.monotonic() - t0) * 1000
        parsed = resp.choices[0].message.parsed
        record = ExtractionCallRecord(
            call_kind=call_kind,
            prompt_label=prompt_label,
            prompt_sha256=prompt_sha,
            schema_name=schema.__name__,
            output=parsed.model_dump_json(),
            tokens_in=resp.usage.prompt_tokens,
            tokens_out=resp.usage.completion_tokens,
            cached_tokens=_cached_tokens(resp.usage),
            duration_ms=duration_ms,
            extracted_at=_now_iso(),
            prompt_set_shape=resolved_shape,
        )
        return parsed, record
```

Update the protocol signature in `protocol.py`:

```python
class ExtractorProtocol(Protocol):
    def extract(
        self, content: str, *, content_type: str, content_shape: str,
        user_notes: str | None = None,
    ) -> tuple[dict[str, Any], ExtractionUsage]: ...
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/fetch_extract_queue/test_three_call_extractor.py -k "user_notes or notes_injected or sha_reflects" -v`
Expected: PASS (all three)

- [ ] **Step 5: Run the full extractor suite (regression — no-comment path unchanged)**

Run: `uv run pytest tests/fetch_extract_queue/test_three_call_extractor.py -v`
Expected: PASS (all) — existing tests prove the no-comment path is unaffected.

- [ ] **Step 6: Commit**

```bash
git add packages/workflows/src/workflows/extraction/three_call_openai.py packages/workflows/src/workflows/extraction/protocol.py tests/fetch_extract_queue/test_three_call_extractor.py
git commit -m "feat(extraction): user_notes -> reader_threads on followups call (option-B sha)"
```

---

### Task 6: Extract asset passes `user_notes`

**Files:**
- Modify: `packages/orchestrators/src/orchestrators/defs/fetch_extract_queue/assets.py` (`extracted` asset, the `ex.extract(...)` call at lines 237-241; add a helper)
- Test: `tests/fetch_extract_queue/test_assets.py` (create if absent — test the pure helper)

**Interfaces:**
- Consumes: `get_row(...)["user_comments_json"]` (Task 2), `extract(..., user_notes=...)` (Task 5).
- Produces: `comments_json_to_user_notes(raw: str | None) -> str | None` — parses stored comments JSON into a bullet-list string for the prompt, or `None` when there are no usable comments.

- [ ] **Step 1: Write the failing tests**

```python
from orchestrators.defs.fetch_extract_queue.assets import comments_json_to_user_notes


def test_comments_json_to_user_notes_formats_bullets():
    raw = '[{"text": "focus on chunking"}, {"text": "compare with dbt"}]'
    assert comments_json_to_user_notes(raw) == "- focus on chunking\n- compare with dbt"


def test_comments_json_to_user_notes_none_when_empty():
    assert comments_json_to_user_notes(None) is None
    assert comments_json_to_user_notes("[]") is None
    assert comments_json_to_user_notes('[{"text": "   "}]') is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/fetch_extract_queue/test_assets.py -k comments_json -v`
Expected: FAIL — `ImportError: cannot import name 'comments_json_to_user_notes'`

- [ ] **Step 3: Implement helper + wire into the asset**

Add the helper near the top of `fetch_extract_queue/assets.py` (after the imports / `_oneline`):

```python
def comments_json_to_user_notes(raw: str | None) -> str | None:
    """Turn the stored `user_comments_json` into the bullet-list string the
    extractor wraps in its `[reader's notes]` block. Returns None when there
    are no non-empty comments, so the extractor's no-comment path runs."""
    if not raw:
        return None
    texts = [
        (c.get("text") or "").strip()
        for c in json.loads(raw)
        if (c.get("text") or "").strip()
    ]
    return "\n".join(f"- {t}" for t in texts) if texts else None
```

Ensure `import json` is at the top of the module (add if missing).

In the `extracted` asset, build `user_notes` from the row and pass it:

```python
    user_notes = comments_json_to_user_notes(row.get("user_comments_json"))
    payload, calls = ex.extract(
        content=row["raw_content"],
        content_type=content_type,
        content_shape=content_shape,
        user_notes=user_notes,
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/fetch_extract_queue/test_assets.py -k comments_json -v`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add packages/orchestrators/src/orchestrators/defs/fetch_extract_queue/assets.py tests/fetch_extract_queue/test_assets.py
git commit -m "feat(extract): thread user_comments into extractor user_notes"
```

---

### Task 7: Full gate + manual validation

**Files:** none (verification + manual steps)

- [ ] **Step 1: Run the full check gate**

Run: `uv run poe check`
Expected: PASS — fmt, lint, and the entire test suite green.

- [ ] **Step 2: Empirical eval (LLM behaviour — not a unit test)**

Run the capture-mode eval on a small cohort (reuse the spike approach from the design phase, or the `eval-extraction` workbench): for ≥3 items with comments, confirm on **both** `gpt-4o-mini` and `gpt-4o` that (a) `topic_card`/followups-questions hold source-faithfulness vs the no-comment baseline, and (b) `reader_threads` reflects only the notes (faithful-to-notes rubric) and is `[]` when no comment. Record the numbers in the eval log; do NOT assert LLM output in unit tests.

- [ ] **Step 3: Manual prerequisites + UX contract**

- Confirm the Notion integration token has the **"Read comments"** capability (else `get_page_comments` returns `[]` silently).
- Update the **Knowledge OS — Queue** Notion DB description to document the contract: *"Add a comment, then flip Status → Queued to (re)process the item with your comment; this replaces the prior extraction."*

- [ ] **Step 4: Final commit (if any doc/config changed)**

```bash
git add -A
git commit -m "chore(queue-comments): validation pass + docs"
```

---

## Self-Review

**Spec coverage:**
- Read Notion comments → Task 1. ✓
- Store verbatim in `queue.db`, cohort-scoped wipe → Task 2. ✓
- Read at `triaged` asset → Task 3. ✓
- `reader_threads` field → Task 4. ✓
- Followups-only injection, narrative/topic-card untouched, conditional schema, option-B per-call sha, cohort sha base-only → Task 5. ✓
- Extract asset threads notes → Task 6. ✓
- No DAG bump (Global Constraints) ✓; re-queue contract (Task 7 Step 3) ✓; validation split TDD/eval (Tasks + Task 7 Step 2) ✓.
- Privacy (never write to Notion): no task writes extraction output to Notion; `get_page_comments` is read-only. ✓

**Placeholder scan:** none — every code/test step shows complete content.

**Type consistency:** `get_page_comments -> list[dict[str,str]]` (Task 1) → JSON-serialized in Task 3 → parsed by `comments_json_to_user_notes -> str | None` (Task 6) → `extract(..., user_notes: str | None)` (Task 5) → `_structured_call(..., user_notes=...)`. `Followups.reader_threads` (Task 4) consumed by the fold instruction (Task 5). `upsert_triaged(..., user_comments_json: str | None)` (Task 2) called with the JSON from Task 3. Consistent.
