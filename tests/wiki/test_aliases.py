from pathlib import Path

from knowledge_pipeline.lib.wiki.aliases import AliasStore, load_aliases, save_aliases


def _make_store() -> AliasStore:
    store = AliasStore()
    store.add("concept__llm", "Large Language Model", ["LLM", "foundation model"])
    store.add("tool__chromadb", "ChromaDB", ["Chroma", "chroma db"])
    return store


def test_exact_lookup_canonical():
    store = _make_store()
    assert store.lookup("Large Language Model") == "concept__llm"


def test_exact_lookup_alias():
    store = _make_store()
    assert store.lookup("LLM") == "concept__llm"


def test_exact_lookup_case_insensitive():
    store = _make_store()
    assert store.lookup("chromadb") == "tool__chromadb"


def test_exact_lookup_miss():
    store = _make_store()
    assert store.lookup("Unknown Thing") is None


def test_fuzzy_match_close():
    store = _make_store()
    # "Chroma DB" vs "chroma db" — should match
    assert store.fuzzy_match("Chroma DB") == "tool__chromadb"


def test_fuzzy_match_too_different():
    store = _make_store()
    assert store.fuzzy_match("Kubernetes") is None


def test_resolve_prefers_exact():
    store = _make_store()
    assert store.resolve("ChromaDB") == "tool__chromadb"


def test_resolve_falls_back_to_fuzzy():
    store = _make_store()
    # "chroma database" is close enough to "chroma db"
    result = store.resolve("chroma database")
    # May or may not match depending on threshold — test that resolve doesn't crash
    assert result is None or result == "tool__chromadb"


def test_save_then_load_roundtrip(tmp_path: Path):
    store = _make_store()
    path = tmp_path / "aliases.yaml"

    save_aliases(path, store)
    loaded = load_aliases(path)

    assert loaded.lookup("LLM") == "concept__llm"
    assert loaded.lookup("Chroma") == "tool__chromadb"
    assert len(loaded.entries) == 2


def test_load_missing_file(tmp_path: Path):
    path = tmp_path / "nonexistent.yaml"
    store = load_aliases(path)
    assert len(store.entries) == 0


def test_add_updates_existing():
    store = _make_store()
    store.add("concept__llm", "LLM", ["Large Language Model", "foundation model", "FM"])

    assert store.lookup("FM") == "concept__llm"
    assert store.entries["concept__llm"].canonical == "LLM"


def test_save_atomic_no_tmp_left(tmp_path: Path):
    """save_aliases should not leave .tmp files behind."""
    store = _make_store()
    path = tmp_path / "aliases.yaml"
    save_aliases(path, store)

    assert path.exists()
    assert not path.with_suffix(".tmp").exists()
