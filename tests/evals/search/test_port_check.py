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
