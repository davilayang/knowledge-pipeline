"""Rendered entity pages link to each other, so the wiki folder is a browsable
graph (Obsidian, OKF readers) rather than 1,233 unconnected files.

Two kinds of link: inside each claim bullet, the first mention of another entity
the claim is about (via claim_entities) and that has a page; and a `## Related`
section listing related entities that have pages. Every relative link must
resolve. Frontmatter stays plain text, because newsletter-assistant embeds
`summary:` into its vector store and reads `related:` as names.

Uses the `wiki_db_path` fixture (fresh SQLite wiki.db, schema applied).
"""

import re
import subprocess
import sys
from pathlib import Path

from domains.wiki.attributed import (
    ClaimRecord,
    SourceRecord,
    claim_text_hash,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    mint_source_id,
    upsert_source,
)
from domains.wiki.identity import EntityRecord, normalize_name, shortid, slugify
from domains.wiki.state import connection, insert_aliases, insert_entity
from workflows.wiki_synthesis.attributed_synthesis import render_entity_pages

NOW = "2026-10-08T00:00:00+00:00"
CHECKER = Path(__file__).resolve().parents[2] / "scripts" / "check_wiki_links.py"

CLAUDE_CODE = "e_cc00000000000001"
CLAUDE = "e_c100000000000002"
MCP = "e_3c00000000000003"
ANTHROPIC = "e_a700000000000004"  # one claim from one source: no page
GO = "e_6000000000000005"  # a two-letter name that is also an English word
KIRK = "e_4b00000000000006"  # dotless ı: str.lower/casefold and re.IGNORECASE disagree

_NAMES = {
    CLAUDE_CODE: "Claude Code",
    CLAUDE: "Claude",
    MCP: "MCP",
    ANTHROPIC: "Anthropic",
    GO: "Go",
    KIRK: "Kırklareli",
}

# (source url, stance, text, entities the claim is about)
_CLAIMS = [
    (
        "https://example.com/a",
        "reported",
        "Claude Code uses MCP to call tools.",
        {CLAUDE_CODE, MCP},
    ),
    (
        "https://example.com/a",
        "reported",
        "Claude is the model family behind Claude Code.",
        {CLAUDE, CLAUDE_CODE},
    ),
    (
        "https://example.com/b",
        "opinion",
        "MCP will become the default tool protocol for Claude.",
        {MCP, CLAUDE},
    ),
    (
        "https://example.com/b",
        "reported",
        "Claude Code connects to MCP servers, and MCP clients call them.",
        {CLAUDE_CODE, MCP},
    ),
    (
        "https://example.com/b",
        "reported",
        "Anthropic built Claude using the Model Context Protocol.",
        {ANTHROPIC, CLAUDE, MCP},
    ),
    # About both Claude and Claude Code, but only "Claude Code" appears, so a
    # shortest-first match would wrongly link "Claude" inside it.
    (
        "https://example.com/b",
        "reported",
        "MCP support shipped in Claude Code first.",
        {MCP, CLAUDE_CODE, CLAUDE},
    ),
    # Mentions inside a URL, inline code or an existing markdown link must stay
    # untouched; the first plain mention is the one that links.
    (
        "https://example.com/c",
        "reported",
        "See https://claude.ai/docs to learn how Claude handles MCP.",
        {MCP, CLAUDE},
    ),
    (
        "https://example.com/c",
        "reported",
        "`claude` is the CLI name; Claude itself speaks MCP.",
        {MCP, CLAUDE},
    ),
    (
        "https://example.com/c",
        "reported",
        "Read [the Claude guide](https://example.com/g) before using MCP with Claude.",
        {MCP, CLAUDE},
    ),
    ("https://example.com/c", "reported", "MCPs are spreading among Claude users.", {CLAUDE, MCP}),
    ("https://example.com/d", "reported", "Go compiles fast.", {GO}),
    ("https://example.com/d", "reported", "Teams go faster with Go and MCP.", {GO, MCP}),
    ("https://example.com/d", "reported", "Kırklareli is a city in Turkey.", {KIRK}),
    ("https://example.com/d", "reported", "KIRKLARELI hosts an MCP meetup.", {KIRK, MCP}),
]


def _file(entity_id: str) -> str:
    return f"{slugify(_NAMES[entity_id])}-{shortid(entity_id)}.md"


def _render(tmp_path, wiki_db_path) -> Path:
    with connection(wiki_db_path) as conn, conn:
        for eid, name in _NAMES.items():
            insert_entity(
                conn,
                EntityRecord(
                    entity_id=eid,
                    canonical_name=name,
                    normalized_name=normalize_name(name),
                    slug=slugify(name),
                    entity_type="concept",
                    created_at=NOW,
                ),
            )
        insert_aliases(conn, [("Model Context Protocol", MCP)])
        for url, stance, text, entity_ids in _CLAIMS:
            sid = mint_source_id(url)
            upsert_source(
                conn,
                SourceRecord(
                    source_id=sid,
                    content_key=url,
                    origin_type="queue",
                    title="T",
                    author="Jane Doe",
                    publication=None,
                    url=url,
                    published_at="2026-09-01",
                    content_hash=None,
                    fetched_at=None,
                    added_at=NOW,
                ),
            )
            th = claim_text_hash(text)
            cid = insert_claim(
                conn,
                ClaimRecord(
                    claim_id=mint_claim_id(sid, th),
                    source_id=sid,
                    text=text,
                    text_hash=th,
                    provenance="source",
                    stance=stance,
                    created_at=NOW,
                ),
            )
            for eid in entity_ids:
                insert_claim_entity(conn, claim_id=cid, entity_id=eid)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir, updated_at="2026-10-08")
    return wiki_dir


def _page(wiki_dir: Path, entity_id: str) -> str:
    return (wiki_dir / _file(entity_id)).read_text(encoding="utf-8")


def _bullet(page: str, starts: str) -> str:
    """The claim bullet whose visible text starts with `starts` (links unwrapped)."""
    for line in page.splitlines():
        if line.startswith("- ") and _unlink(line[2:]).startswith(starts):
            return line
    raise AssertionError(f"no bullet starting {starts!r} in page:\n{page}")


def _unlink(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)


def _section(page: str, heading: str) -> str:
    """The body of `## <heading>` up to the next `## ` heading, not the rest of the page."""
    match = re.search(rf"^## {heading}\n(.*?)(?=^## |\Z)", page, flags=re.M | re.S)
    assert match, f"no ## {heading} section in page:\n{page}"
    return match.group(1)


def _frontmatter(page: str) -> str:
    return page.split("---", 2)[1]


def test_claim_bullet_links_other_entity_and_not_itself(tmp_path, wiki_db_path):
    wiki_dir = _render(tmp_path, wiki_db_path)
    line = _bullet(_page(wiki_dir, MCP), "Claude Code uses MCP")
    assert f"[Claude Code]({_file(CLAUDE_CODE)})" in line
    assert "[MCP](" not in line


def test_longest_name_wins(tmp_path, wiki_db_path):
    # "Claude Code" must link as one entity, not as "Claude" followed by " Code",
    # even when the claim is about both.
    wiki_dir = _render(tmp_path, wiki_db_path)
    line = _bullet(_page(wiki_dir, MCP), "MCP support shipped in Claude Code")
    assert f"[Claude Code]({_file(CLAUDE_CODE)})" in line
    assert "[Claude](" not in line


def test_only_first_mention_per_claim_is_linked(tmp_path, wiki_db_path):
    wiki_dir = _render(tmp_path, wiki_db_path)
    line = _bullet(_page(wiki_dir, CLAUDE_CODE), "Claude Code connects to MCP servers")
    assert line.count(f"]({_file(MCP)})") == 1


def test_alias_mention_links_and_entity_without_page_does_not(tmp_path, wiki_db_path):
    wiki_dir = _render(tmp_path, wiki_db_path)
    assert not (wiki_dir / _file(ANTHROPIC)).exists()
    line = _bullet(_page(wiki_dir, CLAUDE), "Anthropic built Claude")
    assert f"[Model Context Protocol]({_file(MCP)})" in line
    assert "[Anthropic](" not in line


def test_related_section_lists_related_entities_with_pages(tmp_path, wiki_db_path):
    wiki_dir = _render(tmp_path, wiki_db_path)
    related = _section(_page(wiki_dir, MCP), "Related")
    assert f"[Claude Code]({_file(CLAUDE_CODE)})" in related
    assert f"[Claude]({_file(CLAUDE)})" in related
    assert "[Anthropic](" not in related


def test_frontmatter_stays_plain_text(tmp_path, wiki_db_path):
    wiki_dir = _render(tmp_path, wiki_db_path)
    for eid in (CLAUDE_CODE, CLAUDE, MCP):
        fm = _frontmatter(_page(wiki_dir, eid))
        assert "](" not in fm
    assert "Anthropic" in _frontmatter(_page(wiki_dir, CLAUDE)).split("related:", 1)[1]


def test_every_rendered_link_resolves(tmp_path, wiki_db_path):
    wiki_dir = _render(tmp_path, wiki_db_path)
    result = subprocess.run(
        [sys.executable, str(CHECKER), str(wiki_dir)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout


def test_mentions_inside_urls_code_and_links_are_left_alone(tmp_path, wiki_db_path):
    wiki_dir = _render(tmp_path, wiki_db_path)
    page = _page(wiki_dir, MCP)
    url = _bullet(page, "See https://claude.ai/docs")
    assert "https://claude.ai/docs" in url
    assert f"how [Claude]({_file(CLAUDE)}) handles" in url
    code = _bullet(page, "claude is the CLI name")
    assert "`claude`" in code
    assert f"[Claude]({_file(CLAUDE)}) itself" in code
    md = _bullet(page, "Read the Claude guide")
    assert "[the Claude guide](https://example.com/g)" in md
    assert f"with [Claude]({_file(CLAUDE)})." in md


def test_name_must_end_on_a_word_boundary(tmp_path, wiki_db_path):
    wiki_dir = _render(tmp_path, wiki_db_path)
    line = _bullet(_page(wiki_dir, CLAUDE), "MCPs are spreading")
    assert "[MCP" not in line


def test_short_names_match_exact_case(tmp_path, wiki_db_path):
    # "Go" (≤3 characters) must not link the verb "go".
    wiki_dir = _render(tmp_path, wiki_db_path)
    line = _bullet(_page(wiki_dir, MCP), "Teams go faster")
    assert f"Teams go faster with [Go]({_file(GO)})" in line


def test_case_folding_mismatch_does_not_break_the_render(tmp_path, wiki_db_path):
    # A name whose upper/lower case forms do not round-trip must never abort the
    # sweep: the page still renders, linked or not.
    wiki_dir = _render(tmp_path, wiki_db_path)
    assert "KIRKLARELI hosts an" in _unlink(_page(wiki_dir, MCP))


def test_checker_accepts_titled_and_same_page_section_links(tmp_path):
    (tmp_path / "a.md").write_text(
        '[b](b.md "Page B") [top](a.md#reported) [gone](missing.md "t")\n', encoding="utf-8"
    )
    (tmp_path / "b.md").write_text("# B\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CHECKER), str(tmp_path)], capture_output=True, text=True
    )
    assert result.stdout.strip() == "a.md: broken link missing.md"


def test_checker_flags_broken_and_self_links(tmp_path):
    # The verifier above must be able to fail: a missing target and a self-link
    # each exit 1.
    (tmp_path / "a.md").write_text("[gone](missing.md)\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("[me](b.md)\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CHECKER), str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "a.md: broken link missing.md" in result.stdout
    assert "b.md: links to itself" in result.stdout
