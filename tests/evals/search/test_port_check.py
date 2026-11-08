import pytest
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
    passages = {"src::a1": ["alpha chunk text"], "src::b2": ["beta chunk text"]}
    items = [{**it, "passages": passages[it["uri"].rsplit("/", 1)[-1]]} for it in record["items"]]
    return {"status": "ok", "items": items}


def test_exact_match_passes():
    rec = _record()
    assert compare("corpus", rec, _response(rec)) == ([], [])


def test_reorder_of_untied_items_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"].reverse()
    failures, ties = compare("corpus", rec, resp)
    assert len(failures) == 1 and "order" in failures[0]
    assert ties == []


def test_swap_of_tied_items_passes_and_is_listed():
    rec = _record(a=0.41203, b=0.41207)
    resp = _response(rec)
    resp["items"].reverse()
    failures, ties = compare("corpus", rec, resp)
    assert failures == []
    assert len(ties) == 1 and URI_A in ties[0] and URI_B in ties[0]


def test_uri_encoding_difference_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"][0]["uri"] = "kos://piece/corpus/src%3A%3Aa1"
    failures, _ = compare("corpus", rec, resp)
    assert len(failures) == 1 and "uri encoding" in failures[0]


def test_field_difference_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"][1]["date"] = "2026-09-30"
    failures, _ = compare("corpus", rec, resp)
    assert len(failures) == 1 and "date" in failures[0] and URI_B in failures[0]


def test_non_verbatim_passage_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"][0]["passages"] = ["alpha chunk, reworded"]
    failures, _ = compare("corpus", rec, resp)
    assert len(failures) == 1 and "passage" in failures[0] and URI_A in failures[0]


def test_status_other_than_expected_fails():
    rec = _record()
    resp = _response(rec)
    resp["status"] = "partial"
    failures, _ = compare("corpus", rec, resp)
    assert failures == ["status: searcher 'partial', expected 'ok'"]


def test_empty_on_both_sides_expects_nothing_matched():
    rec = {"chunks": [], "items": []}
    assert compare("corpus", rec, {"status": "nothing_matched", "items": []}) == ([], [])


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
        "format_version": 2,
        "depths": list(depths),
        "probes": {
            str(i): {
                "ask": f"ask {i}",
                "depths": {str(d): {"lanes": {"corpus": rec}} for d in depths},
            }
            for i in (1, 2)
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

    def boom(url, json, timeout):
        calls.append((json["query"], json["k"], json["sources"]))
        if json["query"] == "ask 1":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, content=b"not json", request=httpx.Request("POST", url))

    monkeypatch.setattr(port_check.httpx, "post", boom)
    harvest, probes = _harvest_files(tmp_path)
    with pytest.raises(SystemExit) as exc:
        port_check.main(["--harvest", str(harvest), "--probes", str(probes), "--lanes", "corpus"])
    assert exc.value.code == 1
    assert len(calls) == 4
    out = capsys.readouterr().out
    assert out.count("fail: searcher request:") == 4


def test_empty_passages_and_empty_string_passage_fail():
    rec = _record()
    resp = _response(rec)
    resp["items"][0]["passages"] = []
    resp["items"][1]["passages"] = [""]
    failures, _ = compare("corpus", rec, resp)
    assert len(failures) == 2
    assert URI_A in failures[0] and "no passages" in failures[0]
    assert URI_B in failures[1] and "empty passage" in failures[1]


def test_swap_with_an_item_missing_from_chunks_fails_as_unknown_distance():
    rec = _record()
    rec["chunks"] = rec["chunks"][:1]
    resp = _response(rec)
    resp["items"].reverse()
    failures, ties = compare("corpus", rec, resp)
    assert ties == []
    assert any("order" in f and "distance unknown" in f for f in failures)


def test_harvest_with_other_depths_is_refused(tmp_path, monkeypatch):
    import pytest
    from evals.search import port_check

    monkeypatch.setattr(port_check.httpx, "post", lambda *a, **k: pytest.fail("searched"))
    harvest, probes = _harvest_files(tmp_path, depths=(5,))
    with pytest.raises(SystemExit) as exc:
        port_check.main(["--harvest", str(harvest), "--probes", str(probes)])
    assert "depths" in str(exc.value.code)


def test_passage_from_the_tail_of_a_truncated_harvested_chunk_fails():
    rec = _record()
    rec["chunks"][0]["chunk"] = "alpha chu"
    resp = _response(rec)
    resp["items"][0]["passages"] = ["chunk text"]
    failures, _ = compare("corpus", rec, resp)
    assert len(failures) == 1 and "not a whole chunk" in failures[0]


def test_truncated_searcher_passage_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"][0]["passages"] = ["alpha chunk"]
    failures, _ = compare("corpus", rec, resp)
    assert len(failures) == 1 and "not a whole chunk" in failures[0]


def test_blank_na_title_matched_by_searcher_url_passes_and_is_listed():
    rec = _record()
    rec["items"][1]["title"] = ""
    resp = _response(rec)
    resp["items"][1]["title"] = "https://b"
    failures, listed = compare("corpus", rec, resp)
    assert failures == []
    assert listed == [f"title: {URI_B} blank in newsletter-assistant, searcher uses its url"]


def test_blank_na_title_against_another_searcher_title_fails():
    rec = _record()
    rec["items"][1]["title"] = ""
    resp = _response(rec)
    resp["items"][1]["title"] = "Beta"
    failures, _ = compare("corpus", rec, resp)
    assert len(failures) == 1 and "title" in failures[0]


def test_harvest_without_format_version_2_is_refused(tmp_path, monkeypatch):
    import json

    import pytest
    from evals.search import port_check

    monkeypatch.setattr(port_check.httpx, "post", lambda *a, **k: pytest.fail("searched"))
    harvest, probes = _harvest_files(tmp_path)
    data = json.loads(harvest.read_text())
    del data["format_version"]
    harvest.write_text(json.dumps(data))
    with pytest.raises(SystemExit) as exc:
        port_check.main(["--harvest", str(harvest), "--probes", str(probes)])
    assert "format_version" in str(exc.value.code)


S1 = "kos://piece/session/newsletter-s1"
S2 = "kos://piece/session/newsletter-s2"


def _sessions(*, a: float = 0.30, b: float = 0.50) -> dict:
    return {
        "chunks": [
            {"session_id": "newsletter-s1", "distance": a, "chunk": "first session window"},
            {"session_id": "newsletter-s2", "distance": b, "chunk": "second session window"},
        ],
        "items": [
            {
                "uri": S1,
                "date": "2026-10-01",
                "summary": "Talked about evals.",
                "passages": ["first session window"],
            },
            {
                "uri": S2,
                "date": "2026-10-02",
                "summary": None,
                "passages": ["second session window"],
            },
        ],
    }


def _lane_response(rec: dict) -> dict:
    return {"status": "ok", "items": [dict(it) for it in rec["items"]]}


def test_sessions_exact_match_passes():
    rec = _sessions()
    assert compare("sessions", rec, _lane_response(rec)) == ([], [])


N1 = "kos://piece/note/2026-10-01_evals-a1b2c3"
B1 = "kos://piece/brief/0192a6f0-1111-7000-8000-000000000001"
W1 = "kos://piece/wiki/ent_evals"


def _notes() -> dict:
    return {
        "chunks": [
            {
                "source_ref": "local:2026-10-01_evals-a1b2c3.md",
                "distance": 0.3,
                "chunk": "a note chunk",
            },
        ],
        "items": [
            {"uri": N1, "title": "Evals", "date": "2026-10-01", "passages": ["a note chunk"]}
        ],
    }


def _briefs() -> dict:
    return {
        "chunks": [
            {
                "source_ref": "local:2026-10-01_try-evals-0192a6f0.md",
                "brief_id": "0192a6f0-1111-7000-8000-000000000001",
                "distance": 0.3,
                "chunk": "a brief chunk",
            },
        ],
        "items": [
            {"uri": B1, "title": "Try evals", "date": "2026-10-01", "meta": {"status": "queued"}}
        ],
    }


def _wiki() -> dict:
    return {
        "chunks": [{"entity_id": "ent_evals", "distance": 0.3, "chunk": "Evals summary."}],
        "items": [
            {
                "uri": W1,
                "title": "Evals",
                "summary": "Evals summary.",
                "meta": {
                    "num_sources": 3,
                    "stale": False,
                    "related": ["Judges"],
                    "has_user_notes": False,
                },
            }
        ],
    }


@pytest.mark.parametrize("lane", ["notes", "briefs", "wiki"])
def test_notes_and_briefs_and_wiki_exact_matches_pass(lane):
    rec = {"notes": _notes, "briefs": _briefs, "wiki": _wiki}[lane]()
    assert compare(lane, rec, _lane_response(rec)) == ([], [])


def test_note_passage_that_is_not_a_whole_chunk_of_that_note_fails():
    rec = _notes()
    resp = _lane_response(rec)
    resp["items"][0]["passages"] = ["a note chunk, reworded"]
    failures, _ = compare("notes", rec, resp)
    assert len(failures) == 1 and "not a whole chunk" in failures[0]


def test_brief_status_difference_fails():
    rec = _briefs()
    resp = _lane_response(rec)
    resp["items"][0]["meta"] = {"status": "done"}
    failures, _ = compare("briefs", rec, resp)
    assert failures == [f"meta.status of {B1}: searcher 'done', newsletter-assistant 'queued'"]


@pytest.mark.parametrize(
    "key, wrong",
    [("num_sources", 4), ("stale", True), ("related", []), ("has_user_notes", True)],
)
def test_each_wiki_meta_difference_fails(key, wrong):
    rec = _wiki()
    resp = _lane_response(rec)
    resp["items"][0]["meta"] = {**rec["items"][0]["meta"], key: wrong}
    failures, _ = compare("wiki", rec, resp)
    assert len(failures) == 1 and f"meta.{key}" in failures[0]


def test_wiki_summary_difference_fails():
    rec = _wiki()
    resp = _lane_response(rec)
    resp["items"][0]["summary"] = "Another summary."
    failures, _ = compare("wiki", rec, resp)
    assert len(failures) == 1 and failures[0].startswith(f"summary of {W1}")


def test_session_summary_difference_fails():
    rec = _sessions()
    resp = _lane_response(rec)
    resp["items"][1]["summary"] = "Invented."
    failures, _ = compare("sessions", rec, resp)
    assert len(failures) == 1 and "summary" in failures[0] and S2 in failures[0]


def test_session_reorder_fails_and_tied_swap_is_listed():
    rec = _sessions()
    resp = _lane_response(rec)
    resp["items"].reverse()
    failures, ties = compare("sessions", rec, resp)
    assert len(failures) == 1 and "order" in failures[0] and ties == []
    rec = _sessions(a=0.41203, b=0.41207)
    resp = _lane_response(rec)
    resp["items"].reverse()
    failures, ties = compare("sessions", rec, resp)
    assert failures == [] and len(ties) == 1


@pytest.mark.parametrize("lane", ["sessions", "notes", "briefs", "wiki"])
def test_non_ok_status_fails_in_every_lane(lane):
    rec = {"sessions": _sessions, "notes": _notes, "briefs": _briefs, "wiki": _wiki}[lane]()
    resp = _lane_response(rec)
    resp["status"] = "partial"
    failures, _ = compare(lane, rec, resp)
    assert failures == ["status: searcher 'partial', expected 'ok'"]


@pytest.mark.parametrize("lane", ["sessions", "notes"])
def test_duplicate_session_or_note_fails_as_a_duplicate(lane):
    rec = {"sessions": _sessions, "notes": _notes}[lane]()
    resp = _lane_response(rec)
    resp["items"].append(dict(resp["items"][0]))
    failures, _ = compare(lane, rec, resp)
    assert any(f.startswith("duplicate:") for f in failures), failures


def test_item_the_reference_dropped_but_the_searcher_returns_fails_by_name():
    rec = _sessions()
    resp = _lane_response(rec)
    extra = "kos://piece/session/newsletter-deleted"
    # newsletter-assistant read this chunk and dropped its piece; the searcher kept it.
    rec["chunks"].append(
        {"session_id": "newsletter-deleted", "distance": 0.6, "chunk": "gone session window"}
    )
    resp["items"].append({"uri": extra, "date": "2026-09-01", "summary": None, "passages": []})
    failures, _ = compare("sessions", rec, resp)
    assert failures == [f"pieces differ: searcher only {[extra]}, newsletter-assistant only []"]


def test_lane_filter_searches_only_those_lanes_one_lane_per_request(tmp_path, monkeypatch, capsys):
    import httpx
    import pytest
    from evals.search import port_check

    bodies = []

    def post(url, json, timeout):
        bodies.append(json)
        return httpx.Response(
            200, json={"status": "ok", "items": []}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(port_check.httpx, "post", post)
    harvest, probes = _harvest_files(tmp_path)
    with pytest.raises(SystemExit):
        port_check.main(
            ["--harvest", str(harvest), "--probes", str(probes), "--lanes", "sessions", "corpus"]
        )
    assert {tuple(b["sources"]) for b in bodies} == {("corpus",)}
    out = capsys.readouterr().out
    assert out.count("harvest has no sessions lane") == 4
    assert "0 of 8 passed" in out


def test_brief_chunks_are_matched_by_brief_id_not_filename_suffix():
    other = "kos://piece/brief/0192a6f0-2222-7000-8000-000000000002"
    rec = {
        "chunks": [
            {
                "source_ref": "local:2026-10-01_try-evals-0192a6f0.md",
                "brief_id": "0192a6f0-1111-7000-8000-000000000001",
                "distance": 0.3,
                "chunk": "first brief chunk",
            },
            {
                "source_ref": "local:2026-10-02_other-0192a6f0.md",
                "brief_id": "0192a6f0-2222-7000-8000-000000000002",
                "distance": 0.4,
                "chunk": "second brief chunk",
            },
        ],
        "items": [
            {"uri": B1, "title": "Try evals", "date": "2026-10-01", "meta": {"status": "queued"}},
            {"uri": other, "title": "Other", "date": "2026-10-02", "meta": {"status": "queued"}},
        ],
    }
    resp = _lane_response(rec)
    resp["items"][0]["passages"] = ["second brief chunk"]
    failures, _ = compare("briefs", rec, resp)
    assert len(failures) == 1 and "not a whole chunk" in failures[0] and B1 in failures[0]


def test_swap_within_query_embedding_jitter_passes_and_beyond_it_fails():
    rec = _sessions(a=0.4000, b=0.4015)
    resp = _lane_response(rec)
    resp["items"].reverse()
    failures, ties = compare("sessions", rec, resp)
    assert failures == [] and len(ties) == 1
    rec = _sessions(a=0.4000, b=0.4025)
    resp = _lane_response(rec)
    resp["items"].reverse()
    failures, ties = compare("sessions", rec, resp)
    assert len(failures) == 1 and "order" in failures[0] and ties == []


@pytest.mark.parametrize(
    "lane, field",
    [
        ("corpus", "author"),
        ("sessions", "summary"),
        ("notes", "date"),
        ("briefs", "meta.status"),
        ("wiki", "meta.has_user_notes"),
    ],
)
def test_harvest_item_missing_a_required_field_fails(lane, field):
    rec = {
        "corpus": _record,
        "sessions": _sessions,
        "notes": _notes,
        "briefs": _briefs,
        "wiki": _wiki,
    }[lane]()
    resp = _response(rec) if lane == "corpus" else _lane_response(rec)
    item = rec["items"][0]
    if field.startswith("meta."):
        del item["meta"][field.removeprefix("meta.")]
    else:
        del item[field]
    failures, _ = compare(lane, rec, resp)
    assert f"harvest item {item['uri']} lacks {field}" in failures


@pytest.mark.parametrize(
    "lane, filename",
    [("notes", "2026-10-01_evals-a1b2c3.md"), ("briefs", "2026-10-01_try-evals-0192a6f0.md")],
)
def test_blank_note_or_brief_title_replaced_by_its_filename_passes_and_is_listed(lane, filename):
    rec = {"notes": _notes, "briefs": _briefs}[lane]()
    rec["items"][0]["title"] = ""
    resp = _lane_response(rec)
    resp["items"][0]["title"] = filename
    uri = rec["items"][0]["uri"]
    assert compare(lane, rec, resp) == (
        [],
        [f"title: {uri} blank in newsletter-assistant, searcher uses its filename"],
    )


@pytest.mark.parametrize("lane", ["notes", "briefs"])
def test_blank_note_or_brief_title_replaced_by_something_else_fails(lane):
    rec = {"notes": _notes, "briefs": _briefs}[lane]()
    rec["items"][0]["title"] = ""
    resp = _lane_response(rec)
    resp["items"][0]["title"] = "Invented"
    failures, _ = compare(lane, rec, resp)
    assert len(failures) == 1 and failures[0].startswith("title of ")


def test_duplicate_reports_only_the_duplicate_line():
    rec = _sessions()
    resp = _lane_response(rec)
    resp["items"].append({**resp["items"][0], "summary": "A different copy."})
    failures, _ = compare("sessions", rec, resp)
    assert failures == [f"duplicate: searcher returned {[S1]} more than once"]


def test_field_failures_come_in_fields_order():
    rec = _record()
    resp = _response(rec)
    resp["items"][1].update(date="2026-09-30", title="Wrong", url="https://x")
    failures, _ = compare("corpus", rec, resp)
    assert [f.split(" of ")[0] for f in failures] == ["title", "url", "date"]


def test_empty_session_summary_against_null_fails():
    rec = _sessions()
    resp = _lane_response(rec)
    resp["items"][1]["summary"] = ""
    failures, _ = compare("sessions", rec, resp)
    assert failures == [f"summary of {S2}: searcher '', newsletter-assistant None"]


@pytest.mark.parametrize(
    "lane, stem",
    [("notes", "2026-10-01_evals-a1b2c3"), ("briefs", "2026-10-01_try-evals-0192a6f0")],
)
def test_blank_note_or_brief_title_replaced_by_its_filename_without_md_is_listed(lane, stem):
    rec = {"notes": _notes, "briefs": _briefs}[lane]()
    rec["items"][0]["title"] = ""
    resp = _lane_response(rec)
    resp["items"][0]["title"] = stem
    uri = rec["items"][0]["uri"]
    assert compare(lane, rec, resp) == (
        [],
        [f"title: {uri} blank in newsletter-assistant, searcher uses its filename"],
    )


def _wiki_tie() -> dict:
    """Four wiki pages at one distance at the cut-off; newsletter-assistant kept two."""
    ids = ["ent_a", "ent_b", "ent_c", "ent_d", "ent_e"]
    dists = [0.40, 0.56464, 0.56464, 0.56464, 0.56464]
    return {
        "chunks": [
            {"entity_id": i, "distance": d, "chunk": f"{i} summary."}
            for i, d in zip(ids[:3], dists[:3], strict=True)
        ],
        "items": [
            {
                "uri": f"kos://piece/wiki/{i}",
                "title": i,
                "summary": f"{i} summary.",
                "meta": {"num_sources": 1, "stale": False, "related": [], "has_user_notes": False},
            }
            for i in ids[:3]
        ],
    }


def _scored(rec: dict, swap_in: str, distance: float) -> dict:
    """The searcher's answer: the reference's items with the last one swapped for
    `swap_in` at `distance`."""
    items = [dict(it) for it in rec["items"]]
    last = dict(items[-1])
    last["uri"] = f"kos://piece/wiki/{swap_in}"
    last["title"] = swap_in
    last["summary"] = f"{swap_in} summary."
    items[-1] = last
    for it, d in zip(items, [0.40, 0.56464, distance], strict=True):
        it["matched_by"] = [{"score": 1 - d}]
    return {"status": "ok", "items": items}


def test_a_different_piece_tied_at_the_cut_off_is_listed():
    failures, listed = compare("wiki", _wiki_tie(), _scored(_wiki_tie(), "ent_d", 0.56464))
    assert failures == []
    assert len(listed) == 1 and "cut-off" in listed[0]


def test_a_different_piece_not_tied_at_the_cut_off_fails():
    failures, listed = compare("wiki", _wiki_tie(), _scored(_wiki_tie(), "ent_d", 0.60))
    assert len(failures) == 1 and "pieces differ" in failures[0]
    assert listed == []


@pytest.mark.parametrize("shown", ["Evals", "ent_evals"])
def test_blank_wiki_title_replaced_by_the_searcher_is_listed(shown):
    rec = _wiki()
    rec["items"][0]["title"] = ""
    resp = _lane_response(_wiki())
    resp["items"][0]["title"] = shown
    failures, listed = compare("wiki", rec, resp)
    assert failures == []
    assert len(listed) == 1 and "blank" in listed[0]


def test_a_reference_piece_at_the_cut_off_dropped_by_the_searcher_fails():
    rec = _wiki_tie()
    resp = _scored(rec, "ent_d", 0.56464)
    resp["items"] = resp["items"][:2]  # the searcher keeps a and b, and nothing for c
    failures, listed = compare("wiki", rec, resp)
    assert len(failures) == 1 and "pieces differ" in failures[0]
    assert listed == []


def test_a_piece_after_the_reference_cut_off_passes():
    """The searcher fills a lane to `k` pieces; newsletter-assistant reads `k` chunks,
    so a piece after the reference's last one is the searcher filling, not a fault."""
    rec = _wiki_tie()
    resp = _scored(rec, "ent_c", 0.56464)  # the reference's own three
    extra = dict(resp["items"][-1], uri="kos://piece/wiki/ent_d", title="ent_d")
    extra["summary"] = "ent_d summary."
    resp["items"].append(extra)
    assert compare("wiki", rec, resp) == ([], [])


def test_a_reference_item_repeated_and_merged_by_the_searcher_is_listed():
    rec = _wiki()
    rec["items"].append(dict(rec["items"][0]))
    failures, listed = compare("wiki", rec, _lane_response(_wiki()))
    assert failures == []
    assert listed == [f"newsletter-assistant repeats {W1}; searcher merged it"]


def test_searcher_pieces_after_the_reference_pass_when_the_reference_never_saw_them():
    rec = _record()
    resp = _response(rec)
    resp["items"].append(
        {
            "uri": "kos://piece/corpus/src::c3",
            "title": "Gamma",
            "author": None,
            "url": None,
            "date": "2026-10-03",
            "passages": ["gamma chunk text"],
        }
    )
    assert compare("corpus", rec, resp) == ([], [])


def test_a_searcher_piece_before_the_end_of_the_reference_still_fails_as_order():
    rec = _record()
    resp = _response(rec)
    resp["items"].insert(
        0,
        {
            "uri": "kos://piece/corpus/src::c3",
            "title": "Gamma",
            "author": None,
            "url": None,
            "date": "2026-10-03",
            "passages": ["gamma chunk text"],
        },
    )
    failures, _ = compare("corpus", rec, resp)
    assert failures


def test_an_empty_reference_passes_a_searcher_that_filled_past_it():
    rec = {"chunks": [], "items": []}
    resp = {
        "status": "ok",
        "items": [
            {
                "uri": "kos://piece/corpus/src::c3",
                "title": "Gamma",
                "author": None,
                "url": None,
                "date": "2026-10-03",
                "passages": ["gamma chunk text"],
            }
        ],
    }
    assert compare("corpus", rec, resp) == ([], [])


def test_passages_from_chunks_after_the_reference_window_pass():
    """The searcher fetches past newsletter-assistant's `k` chunks, so an item can carry
    passages the harvest never saw; they must come after every harvested chunk."""
    rec = _record()
    resp = _response(rec)
    resp["items"][0]["passages"] = ["alpha chunk text", "alpha chunk from further down"]
    assert compare("corpus", rec, resp) == ([], [])


def test_a_harvested_chunk_missing_from_the_passages_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"][0]["passages"] = ["alpha chunk from further down"]
    failures, _ = compare("corpus", rec, resp)
    assert len(failures) == 1 and "passage" in failures[0] and URI_A in failures[0]


def test_an_unharvested_passage_before_a_harvested_one_fails():
    rec = _record()
    resp = _response(rec)
    resp["items"][0]["passages"] = ["alpha chunk from further down", "alpha chunk text"]
    failures, _ = compare("corpus", rec, resp)
    assert len(failures) == 1 and "passage" in failures[0] and URI_A in failures[0]


def _record_with_alpha_chunks(n: int) -> dict:
    rec = _record()
    rec["chunks"][1:1] = [
        {"content_id": "src::a1", "distance": 0.30 + i / 1000, "chunk": f"alpha chunk {i}"}
        for i in range(1, n)
    ]
    return rec


def test_passages_cut_to_three_of_more_harvested_chunks_pass():
    """The searcher keeps an item's 3 best passages, so of a piece's harvested chunks
    only the first 3 must be there."""
    rec = _record_with_alpha_chunks(5)
    resp = _response(rec)
    resp["items"][0]["passages"] = ["alpha chunk text", "alpha chunk 1", "alpha chunk 2"]
    assert compare("corpus", rec, resp) == ([], [])


def test_one_of_the_first_three_harvested_chunks_missing_fails():
    rec = _record_with_alpha_chunks(5)
    resp = _response(rec)
    resp["items"][0]["passages"] = ["alpha chunk text", "alpha chunk 1", "alpha chunk 3"]
    failures, _ = compare("corpus", rec, resp)
    assert len(failures) == 1 and "alpha chunk 2" in failures[0]
