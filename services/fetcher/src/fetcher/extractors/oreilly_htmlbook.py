"""O'Reilly HTMLBook chapter page -> markdown. Deterministic, stdlib only.

The publisher's markup already names every structure it uses — `data-type`
labels sections, listings, callouts and footnotes; tables are real `<table>`
elements — so this reads those labels rather than inferring shape from layout.

The conversion moves markup, never words, and that is enforced:
`assert_word_sequence_preserved` compares the chapter's source text against the
markdown and refuses output that would drop or reorder a word.
"""

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


# Half the endpoint's cache key, so bump it for any change to what a caller
# receives — the markdown or any response field. A field added without a bump is
# served from warm caches in its old shape for the whole TTL, silently.
CONVERTER_VERSION = "2"


class ConversionRejected(Exception):
    """The converter produced output it will not vouch for, or met markup it
    cannot represent without silently losing structure. Raised rather than
    returned: a caller that receives markdown must be able to trust it."""


# Markup this converter adds, stripped before the guard compares words — so this
# list is part of the fidelity contract: anything emitted and missing here reads
# as a word the source never had. Order matters, because a heading inside a
# callout starts its line with `>` and its `#` will not match until that is gone.
_ADDED_MARKUP = (
    re.compile(r"^(?:\s*>\s?)+", re.M),  # callout blockquote prefix, any depth
    re.compile(r"^\s*#{1,6}\s+", re.M),  # heading markers
    re.compile(r"^\s*[-*]\s+", re.M),  # unordered list markers
    re.compile(r"^\s*\d+\.\s+(?=\S)", re.M),  # ordered list markers
    re.compile(r"!\[figure\]\([^)]*\)"),  # figure references
    re.compile(r"\*\*\[[A-Z]+\]\*\*"),  # callout kind labels
)
# Deliberately absent: a bare `*` or backtick. Both appear in the book's own
# prose — a glob pattern, an inline code span — and stripping every occurrence
# to hide the converter's markup deletes the author's too, which the guard then
# reports as missing words.

# A footnote marker's label is source text — a digit in the body, a letter under
# a table — so the syntax goes and the label stays. The converter's bold always
# wraps a whole block, so only a pair around an entire line is its own: stripping
# every `**` truncates an author's `***`.
_BOLD_BLOCK = re.compile(r"^(\s*)\*\*(.*?)\*\*\s*$")
_FOOTNOTE_DEFINITION = re.compile(r"^\s*\[\^(\w+)\]:\s", re.M)
_FOOTNOTE_REFERENCE = re.compile(r"\[\^(\w+)\]")


def _span_of(attrmap: dict[str, str | None], name: str) -> int:
    """A span attribute as a count, treating anything unparseable as 1."""
    try:
        return max(1, int(attrmap.get(name) or "1"))
    except ValueError:
        return 1


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
    kept, in_fence, fence_quote = [], False, ""
    for line in body.split("\n"):
        if line.strip().lstrip("> ").startswith("```"):
            # The opening fence carries exactly the quote prefix the converter
            # put on the listing's lines; reuse it so a `>>>` REPL prompt in the
            # author's code is not read as a deeper quote.
            if not in_fence:
                fence_quote = line[: len(line) - len(line.lstrip("> "))]
            in_fence = not in_fence
            continue
        if in_fence:
            kept.append(" " + (line[len(fence_quote) :] if line.startswith(fence_quote) else line))
            continue
        # A pipe is a cell wall only on a table row; the books' prose carries
        # literal pipes inside formulas.
        if line.lstrip("> ").startswith("|"):
            if re.fullmatch(r"\s*(?:>\s?)*\|[-| :]+\|\s*", line):
                continue
            line = line.replace("|", " ")
        for pattern in _ADDED_MARKUP:
            line = pattern.sub(" ", line)
        kept.append(_BOLD_BLOCK.sub(r"\1\2", line))
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


# MathJax renders each formula twice in one `<mjx-container>`: an `<svg>` and an
# `<mjx-assistive-mml>` carrying the same symbols as text. Skipping the svg loses
# nothing and stops its children marking boundaries mid-sentence.
_SKIP_TAGS = {"script", "style", "svg"}
_HEADINGS = {f"h{n}" for n in range(1, 7)}
# Tags that do not separate words; everything else is block-level and implies a
# boundary. `<dt>Visual hierarchy</dt><dd>Emphasize` has no whitespace between
# them, while a footnote reference renders as `testing.1` and must not gain any.
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
    # `math` opens a formula; everything inside it is handled by the subtree
    # rule in `_is_inline`, not by name.
    "math",
    # MathJax's own wrappers, which sit inside a sentence: closing one must not
    # separate the formula from the full stop after it.
    "mjx-container",
    "mjx-assistive-mml",
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
        # Open callouts as [kind, unmatched nested div/aside]; bodies are quoted.
        # The counter is what tells a wrapper's close from the callout's own.
        self._callout: list[list] = []
        self._rows: list[list[str]] | None = None  # open table
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.figure_anchors: list[str] = []
        self.figure_captions: list[str] = []
        self._in_figure = 0
        self._noteref: list[str] | None = None
        self._pre: list[str] | None = None
        self._footnotes = False
        self._footnote_open = False
        self._cell_held: list[str] | None = None  # cell suspended by a footnote
        self._table_notes: list[str] = []  # definitions held until the grid closes
        self._equation = False
        self._math = 0
        self._span = (1, 1)
        self._held: dict[int, int] = {}  # column -> rows a span still covers
        # Gated on being inside the chapter: a saved page wraps it in site nav, a
        # sidebar and a cookie banner, none of which are the author's words.
        self._in_chapter = False
        self.found_chapter = False
        self._section_depth = 0
        self._ordered: list[bool] = []
        self._skip = 0

    def _is_inline(self, tag: str) -> bool:
        """True when `tag` must not mark a word boundary.

        Inside `<math>` the answer is always true, whatever the element is
        called: a formula is one run of characters, and MathML's element set is
        large enough that naming its members one at a time leaves the next
        unnamed one to split a word in half.
        """
        return tag in _INLINE_TAGS or self._math > 0

    def _advance_held(self) -> None:
        """Fill the cells that a span opened in an earlier row still covers.

        The spanning value is written once, where it starts; the rows beneath it
        get an empty cell. Repeating it would add words the chapter never had.
        """
        while len(self._row) in self._held:
            self._row.append("")

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
        if not self._is_inline(tag):
            self.text.append(" ")
        if tag == "math":
            self._math += 1
        attrmap = dict(attrs)
        if tag == "section":
            if (attrmap.get("data-type") or "") == "chapter":
                self._in_chapter = self.found_chapter = True
            if self._in_chapter:
                self._section_depth += 1
        if not self._in_chapter:
            return
        if not self._is_inline(tag):
            self._boundary()
        if tag == "pre":
            self._close()
            self._pre = []
            return
        if tag == "figure":
            self._close()
            self._in_figure += 1
        elif tag == "img" and self._in_figure:
            src = attrmap.get("src") or ""
            anchor = _figure_anchor(src)
            if not anchor:
                # An `<img>` carries no text, so an unnameable figure would pass
                # the word guard with `figure_anchors` empty, and the gate would
                # let a pictorial chapter through. Refuse while the save can be redone.
                raise ConversionRejected(
                    f"figure image source {src!r} is not a publisher asset URL, so no "
                    "stable anchor can name it — re-save the page from a reader that "
                    "keeps absolute asset URLs"
                )
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
            # Written where it starts, with the cells it covers left empty.
            # Repeating its value would add words the chapter never had, and
            # refusing over one table is worse than an imperfect grid.
            self._span = (_span_of(attrmap, "colspan"), _span_of(attrmap, "rowspan"))
            self._cell = []
        elif tag == "caption":
            self._open("**")
        kind = attrmap.get("data-type") or ""
        # A reference carries `data-type="noteref"`; the definition's back-link
        # carries nothing, so inside an open footnote the leading anchor is the
        # marker. Untagged, both render as a digit fused to the next sentence.
        if tag == "a" and kind == "indexterm":
            return
        if tag == "a" and (
            kind == "noteref" or (self._footnote_open and not "".join(self._buf).strip())
        ):
            self._noteref = []
            return
        if tag == "div" and kind == "equation":
            # A display formula has no paragraph of its own: without a block opened
            # for it, its symbols reach the source text and never the output.
            self._open("")
            self._equation = True
            return
        if tag == "div" and kind == "footnotes":
            self._close()
            self.blocks.append("**[FOOTNOTES]**")
            self._footnotes = True
            return
        if tag == "p" and kind == "footnote":
            # A table footnote's definition sits inside the grid, in a trailing row
            # whose one cell spans it — a paragraph, not tabular data. Left in the
            # cell, its body lands in the grid while its marker reaches a block.
            self._cell_held, self._cell = self._cell, None
            self._open("")
            self._footnote_open = True
            return
        if tag in ("aside", "div") and self._callout and kind not in _CALLOUT_KINDS:
            # A callout body routinely wraps an inner div. Counting it here is
            # what stops its close being read as the callout's own.
            self._callout[-1][1] += 1
        if tag in ("aside", "div", "section") and kind in _CALLOUT_KINDS:
            self._close()
            self.blocks.append("**[" + kind.upper() + "]**")
            self._callout.append([kind, 0])
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
        if tag == "math" and self._math:
            self._math -= 1
        if not self._is_inline(tag):
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
            if number:
                # In a cell the open buffer is the cell, not `_buf`. The label is
                # source text, so writing it to neither loses a word.
                marker = f"[^{number}]" + (": " if self._footnote_open else "")
                if self._cell is not None:
                    self._cell.append(marker)
                elif self._lead is not None:
                    self._buf.append(marker)
            return
        if tag == "div" and self._equation:
            self._equation = False
            self._close()
            return
        if tag == "p" and self._footnote_open:
            self._footnote_open = False
            self._close()
            self._cell, self._cell_held = self._cell_held, None
            if self._rows is not None and self.blocks:
                # The grid is only emitted when the table closes, so a
                # definition written inside it would otherwise be read before
                # the rows it annotates. Hold it until the table is done.
                self._table_notes.append(self.blocks.pop())
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
            self._advance_held()
            column = len(self._row)
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
            columns, rows_covered = self._span
            self._row += [""] * (columns - 1)
            if rows_covered > 1:
                for covered in range(column, column + columns):
                    self._held[covered] = rows_covered - 1
            self._span = (1, 1)
            return
        if tag == "tr" and self._row is not None:
            self._advance_held()
            self._rows.append(self._row)
            self._row = None
            self._held = {c: n - 1 for c, n in self._held.items() if n > 1}
            return
        if tag == "table" and self._rows is not None:
            rows, self._rows = self._rows, None
            if rows:
                width = max(len(r) for r in rows)
                padded = [r + [""] * (width - len(r)) for r in rows]
                self.blocks.append("| " + " | ".join(padded[0]) + " |")
                self.blocks.append("|" + "---|" * width)
                self.blocks += ["| " + " | ".join(r) + " |" for r in padded[1:]]
            self.blocks += self._table_notes
            self._table_notes = []
            return
        if tag == "caption":
            self._buf.append("**")
            self._close()
            return
        if tag in ("aside", "div") and self._callout and self._callout[-1][1]:
            self._callout[-1][1] -= 1
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
    authors: list[str] = field(default_factory=list)
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
_FIRST_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.M)


def convert_page(page_html: str) -> ChapterConversion:
    """Convert a saved O'Reilly reader page: chrome stripped, identity prepended."""
    conversion = convert_chapter_with_metadata(page_html)
    authors = [html.unescape(a) for a in _META_AUTHOR.findall(page_html)]
    book = _BOOK_TITLE.search(page_html)
    chapter_meta = _META_TITLE.search(page_html)

    # The first heading, not the first line: a chapter may open with a figure, and
    # its reference would become the title written to Notion in preference to the
    # model's. Captions are emitted bold, so they cannot be mistaken for one here.
    heading = _FIRST_HEADING.search(conversion.markdown)
    title = heading.group(1).strip() if heading else ""
    if not title and chapter_meta:
        title = html.unescape(chapter_meta.group(1)).strip()
    header = [f"# {html.unescape(book.group(1))}" if book else "# Book"]
    if authors:
        header.append(f"By {', '.join(authors)}.")
    if chapter_meta:
        header.append(f"Chapter: {html.unescape(chapter_meta.group(1))}")
    markdown = "\n".join(header) + "\n\n---\n" + conversion.markdown

    return ChapterConversion(
        markdown=markdown,
        title=title,
        authors=authors,
        figure_anchors=conversion.figure_anchors,
        figure_captions=conversion.figure_captions,
    )
