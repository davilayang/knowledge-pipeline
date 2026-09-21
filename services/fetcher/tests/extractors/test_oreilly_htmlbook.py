"""The O'Reilly HTMLBook converter: publisher markup -> markdown.

The converter is deterministic — it reads the publisher's own structural labels
rather than inferring shape from layout — so every test here asserts an exact
mapping, and the fidelity guard asserts that no word of the chapter was lost.
"""

import pytest

from fetcher.extractors.oreilly_htmlbook import (
    ConversionRejected,
    assert_word_sequence_preserved,
    convert_chapter,
    convert_chapter_with_metadata,
    convert_page,
)


def test_guard_passes_when_only_markup_changed():
    """Layout may change freely; the word sequence may not. Markup the converter
    is defined to add is stripped before comparison, so a heading marker, a
    callout label and a figure reference are all invisible to the guard."""
    source_text = "Keyboard shortcuts Provide hotkeys for submitting feedback."
    markdown = (
        "## Keyboard shortcuts\n\n" "**[TIP]**\n\n" "> Provide hotkeys for submitting feedback.\n"
    )
    assert_word_sequence_preserved(source_text, markdown)


def test_guard_rejects_a_dropped_word():
    source_text = "Provide hotkeys for submitting feedback."
    markdown = "Provide hotkeys for feedback.\n"
    with pytest.raises(ConversionRejected, match="word sequence"):
        assert_word_sequence_preserved(source_text, markdown)


CHAPTER = """
<section data-type="chapter" id="ch"><h1><span class="label">Chapter 3. </span>Error Analysis</h1>
<p>Opening paragraph that the publisher
wrapped at a clause boundary.</p>
<section data-type="sect1"><h1>Establishing Terminology</h1>
<p>Body of the first section.</p>
<section data-type="sect2"><h1>Open Coding</h1>
<p>Body of the nested section.</p>
</section></section></section>
"""


def test_section_depth_decides_heading_level():
    """The publisher nests `sect1`/`sect2` rather than relying on heading tags,
    so depth is what the level must come from."""
    md = convert_chapter(CHAPTER)
    assert "# Chapter 3. Error Analysis" in md
    assert "## Establishing Terminology" in md
    assert "### Open Coding" in md


def test_paragraph_clause_wrapping_is_collapsed():
    """Source XML wraps paragraphs mid-sentence; those newlines are layout, and
    a reader that keeps them sees a paragraph as many short lines."""
    md = convert_chapter(CHAPTER)
    assert "Opening paragraph that the publisher wrapped at a clause boundary." in md


LISTS = """
<section data-type="chapter"><h1>Ch</h1>
<dl><dt>Keyboard shortcuts</dt>
<dd><p>Provide hotkeys for submitting feedback.</p></dd>
<dt>Visual hierarchy</dt><dd><p>Emphasize the final output.</p></dd></dl>
<ul><li><p>Random sampling gives an unbiased estimate.</p></li>
<li><p>Uncertainty sampling surfaces disagreement.</p></li></ul>
<ol><li><p>First step.</p></li><li><p>Second step.</p></li></ol>
</section>
"""


def test_definition_terms_survive_as_bold_lead_ins():
    """A `<dl>` is how the book introduces named features. Dropping the `<dt>`
    loses the name and leaves its definition as orphaned prose."""
    md = convert_chapter(LISTS)
    assert "**Keyboard shortcuts**" in md
    assert "Provide hotkeys for submitting feedback." in md
    assert "**Visual hierarchy**" in md


def test_list_items_keep_their_markers_despite_nested_paragraphs():
    """List items wrap their text in `<p>`; handling the paragraph first flushes
    the text as a plain block and the item marker is lost."""
    md = convert_chapter(LISTS)
    assert "- Random sampling gives an unbiased estimate." in md
    assert "- Uncertainty sampling surfaces disagreement." in md
    assert "1. First step." in md


CALLOUTS = """
<section data-type="chapter"><h1>Ch</h1>
<div data-type="tip"><h6>Tip</h6><p>Keyboard shortcuts double throughput.</p></div>
<div data-type="warning"><h6>Warning</h6><p>Verbalized confidence is unreliable.</p></div>
<aside data-type="sidebar"><div class="sidebar"><h1>A Note for Readers</h1>
<p>This is an early release.</p></div></aside>
<p>Body resumes here.</p>
</section>
"""


def test_callouts_are_labelled_and_scoped_from_div_and_aside():
    """Warning, tip and note are `<div data-type=...>`; only sidebar is `<aside>`.
    Handling `<aside>` alone misses most of them, and an unscoped callout body
    merges into the surrounding prose with nothing marking where it ends."""
    md = convert_chapter(CALLOUTS)
    assert "**[TIP]**" in md and "**[WARNING]**" in md and "**[SIDEBAR]**" in md
    assert "> Keyboard shortcuts double throughput." in md
    assert "> This is an early release." in md
    # The callout ends: prose after it is not quoted.
    assert "\nBody resumes here." in md


TABLE = """
<section data-type="chapter"><h1>Ch</h1>
<table id="t"><caption><span class="label">Table 3-1. </span>Realistic open codes</caption>
<thead><tr><th>Trace situation</th><th>Open code</th></tr></thead>
<tbody><tr><td><p>Summary included signature</p></td><td><p>boilerplate leak</p></td></tr>
<tr><td><p>Wrong name extracted</p></td><td><p>name confusion</p></td></tr></tbody></table>
</section>
"""

MERGED_CELL = TABLE.replace("<th>Open code</th>", '<th colspan="2">Open code</th>')


def test_table_reconstructs_as_a_markdown_grid():
    """A pasted table arrives as tab-separated lines with its row structure
    destroyed; the publisher's `<table>` still has it, so pairing survives."""
    md = convert_chapter(TABLE)
    assert "| Trace situation | Open code |" in md
    assert "| Summary included signature | boilerplate leak |" in md
    assert "**Table 3-1. Realistic open codes**" in md


def test_a_merged_cell_is_rejected_rather_than_flattened():
    """A spanning cell has no faithful markdown grid. Flattening it silently
    would misalign every column after it, so the converter refuses."""
    with pytest.raises(ConversionRejected, match="colspan|rowspan"):
        convert_chapter(MERGED_CELL)


FIGURE_AND_NOTES = """
<section data-type="chapter"><h1>Ch</h1>
<p>The cycle is shown below.<sup><a data-type="noteref" id="m1" href="#id1">1</a></sup></p>
<figure><div id="fig-a" class="figure">
<img alt="alt" src="/api/v2/epubs/urn:orm:book:9798341660717/files/assets/aiee_0301.png">
<h6><span class="label">Figure 3-1. </span>The iterative process.</h6></div></figure>
<div data-type="footnotes"><p data-type="footnote" id="id1">
<sup><a href="#m1">1</a></sup> See the vendor documentation.</p></div>
</section>
"""


def test_figure_becomes_a_stable_reference_and_a_bold_caption():
    """The reference is minted from the ISBN and the publisher's asset name, both
    in the `<img src>`, so it survives re-conversion and is unique corpus-wide —
    it is the key the figure-text channel matches on. The caption is a bold line,
    not a heading: a caption is not a section turn."""
    md = convert_chapter(FIGURE_AND_NOTES)
    assert "![figure](oreilly:9798341660717/aiee_0301.png)" in md
    assert "**Figure 3-1. The iterative process.**" in md
    assert "# Figure 3-1" not in md


def test_footnote_reference_and_definition_are_linked():
    """A bare digit fused to the end of a sentence reads as prose to an
    extractor; markdown footnote syntax keeps reference and definition paired."""
    md = convert_chapter(FIGURE_AND_NOTES)
    assert "The cycle is shown below.[^1]" in md
    assert "**[FOOTNOTES]**" in md
    assert "[^1]: See the vendor documentation." in md


def test_figure_anchors_are_reported():
    """The gate counts anchors from the body, so the converter must enumerate
    them rather than leaving a caller to re-parse the markdown."""
    result = convert_chapter_with_metadata(FIGURE_AND_NOTES)
    assert result.figure_anchors == ["oreilly:9798341660717/aiee_0301.png"]
    assert result.figure_captions == ["Figure 3-1. The iterative process."]


PAGE = """<html><head>
<meta property="og:title" content="03. Error Analysis">
<meta property="og:book:author" content="Shreya Shankar">
<meta property="og:book:author" content="Hamel Husain">
<script>var t = {"title":"Evals for AI Engineers","other":1};</script>
</head><body><nav>Explore Skills</nav>
<section data-type="chapter"><h1><span class="label">Chapter 3. </span>Error Analysis</h1>
<p>Body text.</p></section>
<footer>Cookie preferences</footer></body></html>"""


def test_identity_header_carries_both_authors_and_the_title():
    """Neither the Notion Name nor a stored author reaches the extractor — only
    the body does. A chapter that does not name its book or authors is
    attributed to nobody, and the page's own metadata already has all three."""
    result = convert_page(PAGE)
    assert "Evals for AI Engineers" in result.markdown
    assert "Shreya Shankar" in result.markdown and "Hamel Husain" in result.markdown
    assert result.title == "Chapter 3. Error Analysis"


def test_page_chrome_is_excluded_from_the_chapter():
    """A saved page carries the site's nav and footer around the chapter. They
    are not the author's words and must not become claims."""
    result = convert_page(PAGE)
    assert "Explore Skills" not in result.markdown
    assert "Cookie preferences" not in result.markdown
    assert "Body text." in result.markdown


CODE_AND_LITERALS = """
<section data-type="chapter"><h1>Ch</h1>
<p>Use the `chat` endpoint and a * wildcard.</p>
<pre data-type="programlisting">response = client.create(
    model="gpt-4o",
)</pre>
<section data-type="sect1"><h1>A</h1><section data-type="sect2"><h1>B</h1>
<section data-type="sect3"><h1>C</h1><section data-type="sect4"><h1>D</h1>
<p>Deep body.</p></section></section></section></section>
</section>
"""


def test_code_block_is_fenced_and_keeps_its_line_breaks():
    """A listing is the one place newlines are content rather than layout."""
    md = convert_chapter(CODE_AND_LITERALS)
    assert '```\nresponse = client.create(\n    model="gpt-4o",\n)\n```' in md


def test_literal_backtick_and_asterisk_in_prose_survive():
    """Stripping every backtick and asterisk to hide the converter's own markup
    also deletes the author's, and the guard then reads real words as missing."""
    md = convert_chapter(CODE_AND_LITERALS)
    assert "Use the `chat` endpoint and a * wildcard." in md


def test_heading_level_follows_section_depth_beyond_two_levels():
    """A second O'Reilly title nests to `sect4`. Depth is the only thing that
    tracks it — the publisher restarts heading tags inside every section."""
    md = convert_chapter(CODE_AND_LITERALS)
    assert "## A" in md and "### B" in md and "#### C" in md and "##### D" in md
