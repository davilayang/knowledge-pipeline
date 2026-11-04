from evals.search.legacy_run import hits_from_legacy_run
from evals.search.score import Hit


def test_a_newsletter_assistant_run_maps_to_voice_and_mcp_views():
    row = {"content_id": "c1", "chunk": "x", "content_date": "2026-01-01", "session_id": None}
    run = {
        "1": {
            "lanes": {
                "articles": [row] * 7,
                "sessions": [{**row, "session_id": "s-1"}] * 4,
                "notes": [row] * 4,
                "briefs": [{**row, "source_ref": "local:b.md"}],
                "wiki": [],
            },
            "shipped": {
                "all": [
                    {"uri": "article://medium/c1", "kind": "article"},
                    {"uri": "session://transcript/s-1", "kind": "session"},
                    {"uri": "note://daily/2026-01-01", "kind": "note", "snippet": "## h"},
                    {"uri": "wiki://entity/e1", "kind": "wiki"},
                ]
            },
        }
    }
    views = hits_from_legacy_run(run)
    voice = views["voice"][1]
    assert len(voice) == 5 + 3 + 3 + 1
    assert Hit("corpus", "c1", "x", "2026-01-01") in voice
    assert Hit("session", "s-1", "x", "2026-01-01") in voice
    assert Hit("brief", "local:b.md", "x", "2026-01-01") in voice
    assert views["mcp"][1] == [
        Hit("corpus", "c1"),
        Hit("session", "s-1"),
        Hit("note", "", "## h", "2026-01-01"),
        Hit("wiki"),
    ]
