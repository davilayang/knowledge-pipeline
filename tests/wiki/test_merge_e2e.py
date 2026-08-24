"""End-to-end dedup flow (#15): candidate generation → merge → verify, against
one real temp wiki.db + wiki dir. Ties together the three pieces the operator
runs (CLUSTER via wiki-dedup-candidates, JUDGE by picking a pair, MERGE via
wiki-merge) with a fake embedder standing in for OpenAI. No network, no LLM.
"""

from datetime import date

from domains.wiki.attributed import (
    ClaimRecord,
    SourceRecord,
    attributed_claims_for_entity,
    claim_text_hash,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    upsert_source,
)
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.io import write_page
from domains.wiki.merge_cli import run_merge
from domains.wiki.state import (
    connection,
    get_aliases_for_entity,
    get_entity,
    insert_entity,
    upsert_page,
)
from domains.wiki.types import WikiPage
from evals.wiki_dedup import run_candidates

NOW = "2026-07-04T00:00:00+00:00"


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Both "Max" entities embed to one axis, "Kubernetes" to the other."""
    return [[0.0, 1.0] if "Kubernetes" in t else [1.0, 0.0] for t in texts]


def _seed(conn, wiki_dir, entity_id, name, file_name, claim_text):
    insert_entity(
        conn,
        EntityRecord(
            entity_id=entity_id,
            canonical_name=name,
            normalized_name=normalize_name(name),
            slug=slugify(name),
            entity_type="concept",
            created_at=NOW,
        ),
    )
    upsert_page(conn, entity_id=entity_id, file_path=file_name, related_ids=[])
    write_page(
        wiki_dir / file_name,
        WikiPage(
            entity_id=entity_id,
            title=name,
            entity_type="concept",
            summary=f"{name} summary.",
            related=[],
            sources=["art1"],
            updated_at=date(2026, 7, 4),
            content=f"# {name}\n\nbody",
        ),
        aliases=[],
        num_sources=1,
        sources=["art1"],
        related=[],
    )
    src = upsert_source(
        conn,
        SourceRecord(
            source_id=f"src_{entity_id}",
            content_key=f"medium::https://example.com/{entity_id}",
            origin_type="queue",
            title="t",
            author="a",
            publication="p",
            url=f"https://example.com/{entity_id}",
            published_at="2026-03-01",
            content_hash="h",
            fetched_at=NOW,
            added_at=NOW,
        ),
    )
    th = claim_text_hash(claim_text)
    cid = insert_claim(
        conn,
        ClaimRecord(
            claim_id=mint_claim_id(src, th),
            source_id=src,
            text=claim_text,
            text_hash=th,
            provenance="source",
            stance="reported",
            created_at=NOW,
        ),
    )
    insert_claim_entity(conn, claim_id=cid, entity_id=entity_id)


def test_dedup_flow_candidates_then_merge(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed(conn, wiki_dir, "e_a", "Claude Max", "claude-max.md", "Max is the top tier.")
        _seed(conn, wiki_dir, "e_b", "Max plan", "max-plan.md", "Max is the paid plan.")
        _seed(conn, wiki_dir, "e_c", "Kubernetes", "kubernetes.md", "K8s orchestrates pods.")

    # CLUSTER: the generator surfaces exactly the Max/Max pair (Kubernetes is far).
    pairs = run_candidates(wiki_db_path, _fake_embed, threshold=0.8)
    assert len(pairs) == 1
    pair = pairs[0]
    assert {pair.a.entity_id, pair.b.entity_id} == {"e_a", "e_b"}

    # JUDGE: keep the fuller name; MERGE via the CLI (keep e_a, drop e_b).
    run_merge(wiki_db_path, wiki_dir, keep_id="e_a", drop_id="e_b")

    # verify: drop folded into keep, its page unlinked, its name now an alias.
    assert not (wiki_dir / "max-plan.md").exists()
    assert (wiki_dir / "claude-max.md").exists()
    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_b") is None
        assert len(attributed_claims_for_entity(conn, "e_a")) == 2  # both Max claims
        assert "Max plan" in get_aliases_for_entity(conn, "e_a")
        assert get_entity(conn, "e_c") is not None  # untouched
