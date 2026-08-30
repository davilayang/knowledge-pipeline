# Codebook — did this body arrive damaged?

This document is handed to a labeller verbatim as their **only** instruction. It
contains no prior labels, no scores, and no record of how earlier rounds went —
any of that would anchor the next labeller.

It is deliberately separate from the contributors-and-publisher codebook. That
one asks who made a piece; this one asks whether the piece survived being
fetched. Nothing you decide here depends on anything in that document.

You will be given one file per item: a `[content_type: ...]` tag and then the
fetched text of one piece of content. **That text is the entire evidence base.**
Judge only what is in front of you.

---

## The decision this feeds

A pipeline fetches things its owner wants to read, and a voice agent later reads
the fetched text aloud. When a fetch goes wrong, the agent reads the wreckage
aloud as though it were the article — confidently narrating a navigation menu.

The label you produce decides whether an item is **failed and sent back to its
owner with "this needs refetching"**. So the question is not "is anything
missing?" — something is missing from almost everything. The question is
**actionable**: *if this were fetched again, or from a better source, would more
of the piece come back?*

That framing is the whole job. Two things can be wrong with a fetched text and
only one of them is worth failing:

- **The text arrived broken.** A refetch could fix it. → this is `damaged`.
- **The piece leans on things that were never text.** Slides, charts, a screen
  recording, figures in a paper. No refetch produces them, because they were
  never words. → this is **not** damaged.

A talk that constantly points at slides is *normal*, not broken. A paper that
references Figure 4 is *normal*, not broken. Failing those would fail a large
share of everything the owner reads, and the warning would stop being read.

---

## Field 1 — `damaged` (true / false)

**True when the text you were given is not the piece, or not all of it, and
fetching again could plausibly get more.**

The shapes that qualify:

- **The content was replaced.** The body is navigation, a menu, a cookie or
  consent wall, a sign-in prompt, or an error message where the article should
  be. The give-away is that you can read the whole file and never reach the thing
  it claims to be.
- **The text stops.** It ends mid-sentence, mid-word, or mid-section, with no
  ending. Also: an explicit marker that a span was removed.
- **A section is announced and then empty.** Headings with nothing under them.
- **Two different pieces are run together** with no boundary, so part of what you
  are reading belongs to something else.
- **It is a stub.** The text is a summary or opening fragment of something the
  text itself says exists in full elsewhere.

**False** when the text is a complete piece, even if:

- it refers to slides, images, charts, figures, diagrams, or a screen recording;
- a speaker says "as you can see here" about something you cannot see;
- it contains tables or code that would be awkward read aloud;
- it is wrapped in site furniture — navigation, footers, related links — **but
  the piece itself is also there**. Chrome around a complete article is not
  damage. Chrome *instead of* the article is.

That last distinction is the one most likely to be got wrong. Read far enough to
find out whether the actual content is present somewhere in the file before
deciding.

## Field 2 — `cause`, only when `damaged` is true

One of exactly two values:

- **`chrome`** — the content was replaced by the site's own furniture: menus,
  an error page, a paywall or consent wall, a sign-in screen.
- **`truncation`** — the content is there but cut: stops mid-thought, an elided
  span, an announced section left empty, a stub of a longer piece.

When both could apply, choose the one that describes **why the substance is
missing**. `null` when `damaged` is false.

## Field 3 — `references_unshown` (true / false)

Independent of the other two. **True when the piece depends on material that was
never text** — slides pointed at, figures referenced, a chart read from, on-screen
steps in a recording, a diagram gestured at.

This is recorded because it tells a reader the piece leans on visuals, which is
useful. It is **never** a reason to call something damaged. Most conference talks
and most papers are `references_unshown: true` and `damaged: false`.

## Field 4 — `evidence`

**A verbatim quote from the text**, copied exactly.

- When `damaged` is true: the line that shows the damage — the error text, the
  last words before it stops, the empty heading.
- When `damaged` is false but `references_unshown` is true: the line that depends
  on the unshown material.
- When both are false: `null`.

A quote that is not in the file, character for character, invalidates the label.

## Field 5 — `notes`

One line, only where something genuinely needed saying. Say so plainly if you
were unsure and why.

---

## How to read a long file

Some items run to 60,000 characters. You cannot decide `damaged` from the opening
— an article that starts with a menu very often has its content further down, and
a text that reads fine for 40,000 characters may still stop mid-sentence at the
end. **Read the beginning and the end of every file**, and enough of the middle to
know whether the piece is actually there.

---

## Output format

One JSON object per item, written to the path you are given. No prose around it.

```json
{
  "instance_id": "<the id in the filename>",
  "damaged": false,
  "cause": null,
  "references_unshown": true,
  "evidence": "verbatim quote from the text, or null",
  "notes": ""
}
```
