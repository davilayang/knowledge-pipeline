"""O'Reilly HTMLBook chapter page -> markdown. Deterministic, stdlib only.

The publisher's markup already names every structure it uses — `data-type`
attributes label sections, code listings, callouts and footnotes, and tables are
real `<table>` elements — so this reads those labels instead of inferring shape
from layout. No model is involved and nothing is rewritten: the conversion moves
markup, never words.

That last property is enforced rather than assumed. `assert_word_sequence_preserved`
compares the chapter's source text against the produced markdown, and the
converter refuses to return output that would drop or reorder a word.
"""

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


CONVERTER_VERSION = "1"


class ConversionRejected(Exception):
    """The converter produced output it will not vouch for, or met markup it
    cannot represent without silently losing structure. Raised rather than
    returned: a caller that receives markdown must be able to trust it."""


# Markup this converter is defined to add. The guard strips these before
# comparing words, so the list is part of the fidelity contract — anything added
# to the output and missing here would be read as a word the source never had.
# Order matters: the blockquote prefix is stripped first, because a heading
# inside a callout starts the line with `>` and its `#` would not match yet.
_ADDED_MARKUP = (
    re.compile(r"^(?:\s*>\s?)+", re.M),  # callout blockquote prefix, any depth
    re.compile(r"^\s*#{1,6}\s+", re.M),  # heading markers
    re.compile(r"^\s*[-*]\s+", re.M),  # unordered list markers
    re.compile(r"^\s*\d+\.\s+(?=\S)", re.M),  # ordered list markers
    re.compile(r"!\[figure\]\([^)]*\)"),  # figure references
    re.compile(r"\*\*\[[A-Z]+\]\*\*"),  # callout kind labels
    re.compile(r"\*\*"),  # bold lead-ins, paired only
)
# Deliberately absent: a bare `*` or backtick. Both appear in the book's own
# prose — a glob pattern, an inline code span — and stripping every occurrence
# to hide the converter's markup deletes the author's too, which the guard then
# reports as missing words.

# A footnote marker carries a digit that IS source text (the publisher renders
# the number inside the note and its reference), so the marker syntax is removed
# while the number it names is kept.
_FOOTNOTE_DEFINITION = re.compile(r"^\s*\[\^(\d+)\]:\s", re.M)
_FOOTNOTE_REFERENCE = re.compile(r"\[\^(\d+)\]")


def _words(text: str) -> list[str]:
    return text.split()


def _markdown_words(markdown: str) -> list[str]:
    # No header stripping here: the guard runs before `convert_page` prepends
    # one, and a sentinel like `---` occurs inside the books' own YAML samples.
    body = _FOOTNOTE_DEFINITION.sub(r"\1 ", markdown)
    # A reference keeps whatever punctuation the source put after it.
    body = _FOOTNOTE_REFERENCE.sub(r"\1", body)
    # Line-start markers are only markup outside a listing. A code block can
    # legitimately open a line with `*` or `-`, and stripping it there deletes a
    # word the author wrote.
    kept, in_fence = [], False
    for line in body.split("\n"):
        if line.strip().lstrip("> ").startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            kept.append(re.sub(r"^\s*(?:>\s?)+", " ", line))
            continue
        # A pipe is a cell wall only on a table row; the books' prose carries
        # literal pipes inside formulas.
        if line.lstrip("> ").startswith("|"):
            if re.fullmatch(r"\s*(?:>\s?)*\|[-| :]+\|\s*", line):
                continue
            line = line.replace("|", " ")
        for pattern in _ADDED_MARKUP:
            line = pattern.sub(" ", line)
        kept.append(line)
    return _words("\n".join(kept))


def assert_word_sequence_preserved(source_text: str, markdown: str) -> None:
    """Raise unless `markdown` carries exactly the words of `source_text`, in order.

    Word equality is the right axis for this source: a copy of a book's text
    arrives with the author's exact words, so the recall floors used for fetched
    articles and transcripts — which tolerate losing half the wording — would
    certify nothing. It proves no prose was lost or reordered; it proves nothing
    about code-block whitespace or table cell structure, which are asserted
    separately.
    """
    expected, produced = _words(source_text), _markdown_words(markdown)
    if expected == produced:
        return
    for i, (want, got) in enumerate(zip(expected, produced)):
        if want != got:
            raise ConversionRejected(
                f"word sequence diverges at word {i}: source {want!r}, markdown {got!r}"
            )
    raise ConversionRejected(
        f"word sequence length differs: source has {len(expected)}, markdown has {len(produced)}"
    )


_SKIP_TAGS = {"script", "style"}
_HEADINGS = {f"h{n}" for n in range(1, 7)}
# Tags that do not separate words. Everything else is block-level and implies a
# boundary: `<dt>Visual hierarchy</dt><dd>Emphasize` carries no whitespace in the
# markup, and joining its text nodes raw would fuse two words into one. Inline
# tags must not add a boundary — a footnote reference renders as `testing.1`,
# and a `<span class="label">` sits mid-phrase.
_INLINE_TAGS = {
    "span",
    "a",
    "sup",
    "sub",
    "em",
    "strong",
    "i",
    "b",
    "code",
    "cite",
    "br",
    # MathML wraps every symbol of a formula in its own element. A formula is one
    # run of characters, so a boundary between `)` and the sentence's full stop
    # would split a word that the markup never separated.
    "math",
    "mi",
    "mo",
    "mn",
    "mrow",
    "msub",
    "msup",
    "mfrac",
    "msqrt",
    "mtext",
    "munderover",
    "munder",
    "mover",
    "mstyle",
    "semantics",
    "annotation",
}
# Callout kinds, and the two containers the publisher uses for them: `sidebar`
# is an `<aside>`, every other kind is a `<div>`. Matching only `<aside>` would
# miss note, warning and tip entirely.
_CALLOUT_KINDS = {"note", "warning", "tip", "caution", "important", "sidebar", "example"}


class _ChapterParser(HTMLParser):
    """Walks a chapter's markup, emitting markdown blocks in document order.

    Heading level comes from `section data-type="sectN"` nesting depth, not from
    the heading tag: the publisher restarts at `<h1>` inside every section, so
    the tag alone says nothing about depth.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.text: list[str] = []  # every source text node, in order
        self._buf: list[str] = []
        self._lead: str | None = None  # markdown prefix for the open block
        self._depth = 0  # section nesting
        self._item = 0  # open <li>/<dd> — a nested <p> belongs to them
        self._callout: list[str] = []  # open callouts; their bodies are quoted
        self._rows: list[list[str]] | None = None  # open table
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.figure_anchors: list[str] = []
        self.figure_captions: list[str] = []
        self._in_figure = 0
        self._noteref: list[str] | None = None
        # Collection is gated on being inside the chapter: a saved page wraps it
        # in site nav, a sidebar and a cookie banner, none of which are the
        # author's words. Slicing the markup by text search instead would have to
        # find the matching close of a tag that nests.
        self._in_chapter = False
        self.found_chapter = False
        self._section_depth = 0
        self._pre: list[str] | None = None
        self._footnotes = False
        self._footnote_open = False
        # Collection is gated on being inside the chapter: a saved page wraps it
        # in site nav, a sidebar and a cookie banner, none of which are the
        # author's words. Slicing the markup by text search instead would have to
        # find the matching close of a tag that nests.
        self._in_chapter = False
        self.found_chapter = False
        self._section_depth = 0
        self._ordered: list[bool] = []
        self._skip = 0

    def _boundary(self) -> None:
        """Mark a word boundary on both sides at once.

        A block tag separates words even when the markup carries no whitespace.
        Both sides are written under one condition so they cannot disagree:
        recording a boundary against the source text that the buffer never sees
        desynchronises the sequences, and the guard then reports a divergence
        that is an artefact of its own bookkeeping rather than lost prose.
        """
        if self._cell is not None:
            self.text.append(" ")
            self._cell.append(" ")
        elif self._lead is not None:
            self.text.append(" ")
            self._buf.append(" ")

    # -- emitting -------------------------------------------------------
    def _open(self, lead: str) -> None:
        self._close()
        self._lead, self._buf = lead, []

    def _close(self) -> None:
        if self._lead is None:
            return
        body = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        lead, self._lead, self._buf = self._lead, None, []
        if body:
            quote = "> " * len(self._callout)
            self.blocks.append(quote + lead + body)

    # -- parsing --------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag not in _INLINE_TAGS:
            self.text.append(" ")
        attrmap = dict(attrs)
        if tag == "section":
            if (attrmap.get("data-type") or "") == "chapter":
                self._in_chapter = self.found_chapter = True
            if self._in_chapter:
                self._section_depth += 1
        if not self._in_chapter:
            return
        if tag not in _INLINE_TAGS:
            self._boundary()
        if tag == "pre":
            self._close()
            self._pre = []
            return
        if tag == "figure":
            self._close()
            self._in_figure += 1
        elif tag == "img" and self._in_figure:
            anchor = _figure_anchor(attrmap.get("src") or "")
            if anchor:
                self.figure_anchors.append(anchor)
                self.blocks.append(f"![figure]({anchor})")
        elif tag in _HEADINGS and self._in_figure:
            # A caption names a figure; it is not a section turn, and emitting it
            # as a heading makes the chapter look like it changes subject here.
            self._open("**")
            return
        if tag == "table":
            self._close()
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            for span in ("colspan", "rowspan"):
                if (attrmap.get(span) or "1") != "1":
                    raise ConversionRejected(
                        f"table carries a {span} cell, which has no faithful markdown grid"
                    )
            self._cell = []
        elif tag == "caption":
            self._open("**")
        kind = attrmap.get("data-type") or ""
        # A reference carries `data-type="noteref"`; the definition's back-link
        # carries nothing, so inside an open footnote the leading anchor is the
        # marker. Both render as a bare digit otherwise, which an extractor reads
        # as prose fused to the neighbouring sentence.
        if tag == "a" and kind == "indexterm":
            return
        if tag == "a" and (
            kind == "noteref" or (self._footnote_open and not "".join(self._buf).strip())
        ):
            self._noteref = []
            return
        if tag == "div" and kind == "footnotes":
            self._close()
            self.blocks.append("**[FOOTNOTES]**")
            self._footnotes = True
            return
        if tag == "p" and kind == "footnote":
            self._open("")
            self._footnote_open = True
            return
        if tag in ("aside", "div", "section") and kind in _CALLOUT_KINDS:
            self._close()
            self.blocks.append("**[" + kind.upper() + "]**")
            self._callout.append(kind)
        elif tag == "section" and kind.startswith("sect"):
            self._close()
            self._depth += 1
        elif tag in _HEADINGS:
            self._open("#" * min(6, self._depth + 1) + " ")
        elif tag in ("ul", "ol"):
            self._ordered.append(tag == "ol")
        elif tag == "dt":
            self._open("**")
        elif tag in ("li", "dd"):
            self._item += 1
            if tag == "li":
                self._open("1. " if (self._ordered or [False])[-1] else "- ")
        elif tag == "p":
            # A paragraph inside a list item or definition continues that item;
            # opening a block for it would strip the item's marker.
            if self._lead is None:
                self._open("")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "section" and self._in_chapter:
            self._section_depth -= 1
            if self._section_depth == 0:
                self._close()
                self._in_chapter = False
                return
        if not self._in_chapter:
            return
        if tag not in _INLINE_TAGS:
            self.text.append(" ")
        if tag == "pre" and self._pre is not None:
            # The one place newlines are content: a listing keeps them, and the
            # paragraph whitespace collapse must not reach inside.
            body = "".join(self._pre).strip("\n")
            self._pre = None
            quote = "> " * len(self._callout)
            self.blocks.append(
                quote
                + "```\n"
                + "\n".join(quote + line for line in body.split("\n"))
                + "\n"
                + quote
                + "```"
            )
            return
        if tag == "a" and self._noteref is not None:
            number = "".join(self._noteref).strip()
            self._noteref = None
            if number and self._lead is not None:
                self._buf.append(f"[^{number}]" + (": " if self._footnote_open else ""))
            return
        if tag == "p" and self._footnote_open:
            self._footnote_open = False
            self._close()
            return
        if tag == "div" and self._footnotes:
            self._footnotes = False
            self._close()
            return
        if tag == "figure" and self._in_figure:
            self._in_figure -= 1
            self._close()
            return
        if tag in _HEADINGS and self._in_figure:
            caption = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if caption:
                self.figure_captions.append(caption)
            self._buf.append("**")
            self._close()
            return
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
            return
        if tag == "tr" and self._row is not None:
            self._rows.append(self._row)
            self._row = None
            return
        if tag == "table" and self._rows is not None:
            rows, self._rows = self._rows, None
            if rows:
                width = max(len(r) for r in rows)
                padded = [r + [""] * (width - len(r)) for r in rows]
                self.blocks.append("| " + " | ".join(padded[0]) + " |")
                self.blocks.append("|" + "---|" * width)
                self.blocks += ["| " + " | ".join(r) + " |" for r in padded[1:]]
            return
        if tag == "caption":
            self._buf.append("**")
            self._close()
            return
        if tag in ("aside", "div", "section") and self._callout:
            self._close()
            self._callout.pop()
        elif tag == "section" and self._depth:
            self._close()
            self._depth -= 1
        elif tag in ("ul", "ol"):
            self._close()
            if self._ordered:
                self._ordered.pop()
        elif tag == "dt":
            self._buf.append("**")
            self._close()
        elif tag in ("li", "dd"):
            self._item = max(0, self._item - 1)
            self._close()
        elif tag in _HEADINGS:
            self._close()
        elif tag == "p":
            if not self._item:
                self._close()

    def handle_data(self, data: str) -> None:
        if self._skip or not self._in_chapter:
            return
        self.text.append(data)
        if self._pre is not None:
            self._pre.append(data)
            return
        if self._noteref is not None:
            self._noteref.append(data)
            return
        if self._cell is not None:
            self._cell.append(data)
        elif self._lead is not None:
            self._buf.append(data)


def convert_chapter(chapter_html: str) -> str:
    """Convert one chapter's markup to markdown, refusing output that lost a word."""
    return convert_chapter_with_metadata(chapter_html).markdown


_ASSET_SRC = re.compile(r"urn:orm:book:(\d+)/files/assets/([^/?\"]+)")


def _figure_anchor(src: str) -> str:
    """`oreilly:<ISBN>/<asset>` from an image source, or empty for site chrome.

    Both halves come from the publisher's own URL, so the anchor is re-derivable
    on every conversion and unique across books — which is what lets a figure
    description be matched back to the figure it describes.
    """
    match = _ASSET_SRC.search(src)
    return f"oreilly:{match.group(1)}/{match.group(2)}" if match else ""


@dataclass(frozen=True)
class ChapterConversion:
    """A converted chapter, with what a caller needs to route and gate it."""

    markdown: str
    title: str = ""
    figure_anchors: list[str] = field(default_factory=list)
    figure_captions: list[str] = field(default_factory=list)


def convert_chapter_with_metadata(chapter_html: str) -> ChapterConversion:
    parser = _ChapterParser()
    parser.feed(chapter_html)
    parser._close()
    if not parser.found_chapter:
        raise ConversionRejected(
            "no <section data-type='chapter'> in the page — not an O'Reilly reader chapter"
        )
    markdown = "\n\n".join(parser.blocks).strip() + "\n"
    assert_word_sequence_preserved("".join(parser.text), markdown)
    return ChapterConversion(
        markdown=markdown,
        figure_anchors=parser.figure_anchors,
        figure_captions=parser.figure_captions,
    )


_META_AUTHOR = re.compile(r'<meta[^>]+og:book:author[^>]+content="([^"]*)"', re.I)
_META_TITLE = re.compile(r'<meta[^>]+og:title[^>]+content="([^"]*)"', re.I)
_BOOK_TITLE = re.compile(r'"title"\s*:\s*"([^"]{3,120})"')


def convert_page(page_html: str) -> ChapterConversion:
    """Convert a saved O'Reilly reader page: chrome stripped, identity prepended."""
    conversion = convert_chapter_with_metadata(page_html)
    authors = [html.unescape(a) for a in _META_AUTHOR.findall(page_html)]
    book = _BOOK_TITLE.search(page_html)
    chapter_meta = _META_TITLE.search(page_html)

    title = conversion.markdown.split("\n", 1)[0].lstrip("# ").strip()
    header = [f"# {html.unescape(book.group(1))}" if book else "# Book"]
    if authors:
        header.append(f"By {', '.join(authors)}.")
    if chapter_meta:
        header.append(f"Chapter: {html.unescape(chapter_meta.group(1))}")
    markdown = "\n".join(header) + "\n\n---\n" + conversion.markdown

    return ChapterConversion(
        markdown=markdown,
        title=title,
        figure_anchors=conversion.figure_anchors,
        figure_captions=conversion.figure_captions,
    )
