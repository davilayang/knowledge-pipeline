from pathlib import Path

from knowledge_pipeline.lib.wiki.state import WikiStateDB


def test_mark_and_get_processed_ids(tmp_path: Path):
    db = WikiStateDB(tmp_path / "state.db")
    db.mark_processed("item-1", "raw_store", "done", pages_touched=["concept__rag"])
    db.mark_processed("item-2", "raw_store", "failed", error="LLM timeout")

    done = db.get_processed_ids("done")
    assert done == {"item-1"}

    failed = db.get_processed_ids("failed")
    assert failed == {"item-2"}
    db.close()


def test_get_failed(tmp_path: Path):
    db = WikiStateDB(tmp_path / "state.db")
    db.mark_processed("item-1", "raw_store", "done")
    db.mark_processed("item-2", "raw_store", "failed", error="bad output")

    failed = db.get_failed()
    assert len(failed) == 1
    assert failed[0].item_id == "item-2"
    assert failed[0].error == "bad output"
    db.close()


def test_mark_processed_upserts(tmp_path: Path):
    db = WikiStateDB(tmp_path / "state.db")
    db.mark_processed("item-1", "raw_store", "failed", error="timeout")
    db.mark_processed("item-1", "raw_store", "done", pages_touched=["concept__rag"])

    done = db.get_processed_ids("done")
    assert "item-1" in done

    failed = db.get_failed()
    assert len(failed) == 0
    db.close()


def test_upsert_and_get_page(tmp_path: Path):
    db = WikiStateDB(tmp_path / "state.db")
    db.upsert_page(
        "concept__rag", "concept", "concept/rag.md", "2026-04-21",
        related=["concept__vector_db"],
    )

    page = db.get_page("concept__rag")
    assert page is not None
    assert page.page_type == "concept"
    assert page.file_path == "concept/rag.md"
    assert page.related == ["concept__vector_db"]
    db.close()


def test_get_page_missing(tmp_path: Path):
    db = WikiStateDB(tmp_path / "state.db")
    assert db.get_page("nonexistent") is None
    db.close()


def test_get_all_pages(tmp_path: Path):
    db = WikiStateDB(tmp_path / "state.db")
    db.upsert_page("concept__rag", "concept", "concept/rag.md", "2026-04-21")
    db.upsert_page("tool__chromadb", "tool", "tool/chromadb.md", "2026-04-21")

    pages = db.get_all_pages()
    assert len(pages) == 2
    assert pages[0].entity_id == "concept__rag"  # sorted
    assert pages[1].entity_id == "tool__chromadb"
    db.close()


def test_creates_parent_dirs(tmp_path: Path):
    db = WikiStateDB(tmp_path / "nested" / "deep" / "state.db")
    db.mark_processed("item-1", "raw_store", "done")
    assert (tmp_path / "nested" / "deep" / "state.db").exists()
    db.close()


def test_mark_processed_with_pages_atomic(tmp_path: Path):
    """mark_processed_with_pages writes both tables in one transaction."""
    db = WikiStateDB(tmp_path / "state.db")
    db.mark_processed_with_pages(
        item_id="item-1",
        source_type="raw_store",
        status="done",
        pages=[
            ("concept__rag", "concept", "concept/rag.md", "2026-04-27", ["tool__chromadb"]),
            ("tool__chromadb", "tool", "tool/chromadb.md", "2026-04-27", None),
        ],
    )

    done = db.get_processed_ids("done")
    assert "item-1" in done

    page1 = db.get_page("concept__rag")
    assert page1 is not None
    assert page1.related == ["tool__chromadb"]

    page2 = db.get_page("tool__chromadb")
    assert page2 is not None
    assert page2.page_type == "tool"
    db.close()


def test_mark_processed_with_pages_empty(tmp_path: Path):
    """mark_processed_with_pages works with no pages (e.g. failed with 0 pages)."""
    db = WikiStateDB(tmp_path / "state.db")
    db.mark_processed_with_pages(
        item_id="item-1",
        source_type="raw_store",
        status="failed",
        pages=[],
        error="LLM timeout",
    )

    failed = db.get_processed_ids("failed")
    assert "item-1" in failed
    db.close()
