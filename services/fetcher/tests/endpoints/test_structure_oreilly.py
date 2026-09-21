"""POST /v1/structure-oreilly: a saved publisher page in, markdown out.

No model and no network are involved, so unlike its sibling routes there is no
cascade to exhaust and no key to be missing — the only failures are markup the
converter will not vouch for.
"""

from fastapi.testclient import TestClient

from fetcher.app import create_app


PAGE = """<html><head>
<meta property="og:title" content="03. Error Analysis">
<meta property="og:book:author" content="Shreya Shankar">
<meta property="og:book:author" content="Hamel Husain">
<script>var t = {"title":"Evals for AI Engineers"};</script>
</head><body><nav>Explore Skills</nav>
<section data-type="chapter"><h1><span class="label">Chapter 3. </span>Error Analysis</h1>
<figure><div class="figure">
<img src="/api/v2/epubs/urn:orm:book:9798341660717/files/assets/aiee_0301.png">
<h6><span class="label">Figure 3-1. </span>The cycle.</h6></div></figure>
<p>Body text.</p></section></body></html>"""


def _app(tmp_path, monkeypatch):
    """Settings are built in the app lifespan, so the client must be entered."""
    monkeypatch.setenv("FETCHER_DB_PATH", str(tmp_path / "fetches.db"))
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")
    return create_app()


def test_converts_a_saved_page_and_reports_title_and_anchors(tmp_path, monkeypatch):
    """The caller needs more than markdown: the title so a known one is not
    re-derived by a model, and the figure anchors so the row can be gated on
    figures that carry no description."""
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        resp = client.post("/v1/structure-oreilly", json={"page_html": PAGE})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "# Chapter 3. Error Analysis" in body["markdown"]
    assert body["title"] == "Chapter 3. Error Analysis"
    assert body["figure_anchors"] == ["oreilly:9798341660717/aiee_0301.png"]
    assert body["figure_captions"] == ["Figure 3-1. The cycle."]
    assert body["cache_hit"] is False


def test_second_request_is_served_from_cache(tmp_path, monkeypatch):
    """The key is the page content plus the converter version — not the prompt
    and chain shas the LLM routes use, which name nothing here."""
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        client.post("/v1/structure-oreilly", json={"page_html": PAGE})
        again = client.post("/v1/structure-oreilly", json={"page_html": PAGE})
    assert again.json()["cache_hit"] is True


def test_a_page_that_is_not_a_chapter_is_refused(tmp_path, monkeypatch):
    """A saved marketing page or a wrong tab reaches here as valid HTML; without
    a chapter section there is nothing to convert and the caller must be told."""
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        resp = client.post(
            "/v1/structure-oreilly", json={"page_html": "<html><body><p>Hi</p></body></html>"}
        )
    assert resp.status_code == 422
    assert resp.json()["code"] == "NOT_A_CHAPTER"
