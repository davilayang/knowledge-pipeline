from evals.search.port_check import compare

URI_A = "kos://piece/corpus/src::a1"
URI_B = "kos://piece/corpus/src::b2"


def _record(*, a: float = 0.30, b: float = 0.50) -> dict:
    return {
        "chunks": [
            {"content_id": "src::a1", "distance": a, "chunk": "alpha chunk text"},
            {"content_id": "src::b2", "distance": b, "chunk": "beta chunk text"},
        ],
        "items": [
            {"uri": URI_A, "title": "Alpha", "author": None, "url": None, "date": "2026-10-01"},
            {
                "uri": URI_B,
                "title": "Beta",
                "author": "Bo",
                "url": "https://b",
                "date": "2026-10-02",
            },
        ],
    }


def _response(record: dict) -> dict:
    passages = {"src::a1": ["alpha chunk"], "src::b2": ["beta chunk"]}
    items = [{**it, "passages": passages[it["uri"].rsplit("/", 1)[-1]]} for it in record["items"]]
    return {"status": "ok", "items": items}


def test_exact_match_passes():
    rec = _record()
    assert compare(rec, _response(rec)) == ([], [])


def test_reorder_of_untied_items_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"].reverse()
    failures, ties = compare(rec, resp)
    assert len(failures) == 1 and "order" in failures[0]
    assert ties == []


def test_swap_of_tied_items_passes_and_is_listed():
    rec = _record(a=0.41203, b=0.41207)
    resp = _response(rec)
    resp["items"].reverse()
    failures, ties = compare(rec, resp)
    assert failures == []
    assert len(ties) == 1 and URI_A in ties[0] and URI_B in ties[0]


def test_uri_encoding_difference_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"][0]["uri"] = "kos://piece/corpus/src%3A%3Aa1"
    failures, _ = compare(rec, resp)
    assert len(failures) == 1 and "uri encoding" in failures[0]


def test_field_difference_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"][1]["date"] = "2026-09-30"
    failures, _ = compare(rec, resp)
    assert len(failures) == 1 and "date" in failures[0] and URI_B in failures[0]


def test_non_verbatim_passage_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"][0]["passages"] = ["alpha chunk, reworded"]
    failures, _ = compare(rec, resp)
    assert len(failures) == 1 and "passage" in failures[0] and URI_A in failures[0]


def test_status_other_than_expected_fails():
    rec = _record()
    resp = _response(rec)
    resp["status"] = "partial"
    failures, _ = compare(rec, resp)
    assert failures == ["status: searcher 'partial', expected 'ok'"]


def test_empty_on_both_sides_expects_nothing_matched():
    rec = {"chunks": [], "items": []}
    assert compare(rec, {"status": "nothing_matched", "items": []}) == ([], [])


def _harvest_files(tmp_path, depths=(5, 10)):
    import json

    head = {"schema_version": 1, "fixture_kind": "search_probes", "dataset_version": "v1"}
    rows = [
        {"probe_id": i, "text": "q", "caller": "both", "type": "sweep", "negative": None}
        for i in (1, 2)
    ]
    probes = tmp_path / "probes.jsonl"
    probes.write_text("\n".join(json.dumps(r) for r in [head, *rows]))
    rec = _record()
    harvest = {
        "depths": list(depths),
        "probes": {
            str(i): {"ask": f"ask {i}", "depths": {str(d): rec for d in depths}} for i in (1, 2)
        },
    }
    path = tmp_path / "harvest.json"
    path.write_text(json.dumps(harvest))
    return path, probes


def test_searcher_error_fails_that_probe_and_the_run_continues(tmp_path, monkeypatch, capsys):
    import httpx
    import pytest
    from evals.search import port_check

    calls = []

    def boom(searcher, query, k):
        calls.append((query, k))
        if query == "ask 1":
            raise httpx.ConnectError("refused")
        raise ValueError("not json")

    monkeypatch.setattr(port_check, "_search", boom)
    harvest, probes = _harvest_files(tmp_path)
    with pytest.raises(SystemExit) as exc:
        port_check.main(["--harvest", str(harvest), "--probes", str(probes)])
    assert exc.value.code == 1
    assert len(calls) == 4
    out = capsys.readouterr().out
    assert out.count("fail: searcher request:") == 4


def test_empty_passages_and_empty_string_passage_fail():
    rec = _record()
    resp = _response(rec)
    resp["items"][0]["passages"] = []
    resp["items"][1]["passages"] = [""]
    failures, _ = compare(rec, resp)
    assert len(failures) == 2
    assert URI_A in failures[0] and "no passages" in failures[0]
    assert URI_B in failures[1] and "empty passage" in failures[1]


def test_swap_with_an_item_missing_from_chunks_fails_as_unknown_distance():
    rec = _record()
    rec["chunks"] = rec["chunks"][:1]
    resp = _response(rec)
    resp["items"].reverse()
    failures, ties = compare(rec, resp)
    assert ties == []
    assert any("order" in f and "distance unknown" in f for f in failures)


def test_harvest_with_other_depths_is_refused(tmp_path, monkeypatch):
    import pytest
    from evals.search import port_check

    monkeypatch.setattr(port_check, "_search", lambda *a: pytest.fail("searched"))
    harvest, probes = _harvest_files(tmp_path, depths=(5,))
    with pytest.raises(SystemExit) as exc:
        port_check.main(["--harvest", str(harvest), "--probes", str(probes)])
    assert "depths" in str(exc.value.code)


def test_passage_from_the_tail_of_a_truncated_harvested_chunk_fails():
    rec = _record()
    rec["chunks"][0]["chunk"] = "alpha chu"
    resp = _response(rec)
    resp["items"][0]["passages"] = ["chunk text"]
    failures, _ = compare(rec, resp)
    assert len(failures) == 1 and "not verbatim" in failures[0]
