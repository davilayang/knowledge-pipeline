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
