# Codebook — `contributors` and `publisher`

This document is handed to a labeller verbatim as their **only** instruction. It
contains no prior labels, no scores, and no record of how earlier rounds went —
any of that would anchor the next labeller and cost the blindness the gold
depends on.

You will be given one file per item. Each file contains a `[content_type: ...]`
tag and then the fetched text of one piece of content. **The text in that file
is the entire evidence base.** You are not given the URL, the page title, or any
stored metadata, and you must not go looking for them. Label only from the text.

---

## What you are producing, and why

The pipeline that consumes these labels builds **entity wiki pages** — one page
per named thing, accumulating claims across everything the owner reads, with
each claim attributed to who said it. Two fields feed that:

- **`contributors`** — the **people** who made this piece. These become person
  entities, and a claim in the piece gets attributed to them.
- **`publisher`** — the **organisation** that put it out. This becomes an
  organisation entity and is deliberately kept in a separate field.

The failure that matters most is putting an **organisation into the person
field**. A wiki that records a company as a human, and attributes opinions to
it as if it had spoken them, is wrong in a way nothing downstream can detect or
repair. When you are unsure whether a name is a person or an organisation,
that uncertainty is worth flagging (see *Flagging*, below) rather than guessing.

---

## Scope — follow the source, not the world

You are recording **what this text presents as its own authorship**, not what is
externally true.

- If the text credits someone who did not really write it, the label follows the
  text. A label is not wrong because reality disagrees with the byline.
- Do not use anything you happen to know about the publication, the channel, or
  the people involved. If the text does not support it, it is not a label.
- Do not infer a person from an organisation. A talk published by a company does
  not thereby have that company's founder as a contributor.

---

## `contributors`

### Definition

A contributor is **a person who helped make this specific piece**: the person
speaking in a talk, the host and the guest of an interview, the writer of an
article, the maintainer of a repository, the person who posted a post.

Three tests, all of which must pass:

1. **Is it a person?** A human being with a name — not a company, channel,
   publication, show, team, or product.
2. **Did they make *this* piece?** Not: are they discussed in it, quoted in it,
   criticised in it, or thanked in it.
3. **Does the text say so?** Either directly ("by X", "I'm X and I work at Y")
   or unmistakably (a talk whose speaker is named in its own title).

### Output shape, per contributor

| field | what to write |
|---|---|
| `name` | The person's name **as the text gives it**. Do not normalise, expand initials, correct spelling, or reorder to Western name order. |
| `role` | One of: `presenter`, `guest`, `host`, `author`, `maintainer`, `poster`. Use `null` when the text does not make the role clear — do not infer it from the content type. |
| `affiliation` | The organisation the text states they belong to. `null` when the text does not state one. Do not supply one you happen to know. |
| `evidence` | **A verbatim quote from the text** that shows this person made this piece. Copy it exactly, including any odd spacing. This is what makes the label checkable — a name without a supporting quote is not a label. |

### An empty list is frequently the correct answer

Many pieces name nobody who made them. Documentation, repository READMEs,
company blog posts and auto-transcribed audio very often carry no personal
attribution at all.

**Return an empty list in that case.** Do not reach for the nearest name to
avoid an empty answer. An empty list is a correct, expected, common label; a
guessed name is a defect that propagates into the wiki.

### Tie-breaks

These are the cases that actually recur in this corpus. Work through them in
order when a case is unclear.

**A name that is also an organisation.** Some organisations are named after a
person, and some channels use a personal-sounding name. The question is never
what the name *looks like* — it is whether the text shows a human doing the
making. If the text only ever uses the name as the thing that published or
hosts, it is a publisher, not a contributor.

**A speaker who only introduces themselves out loud.** In transcribed talks the
only attribution is often in the speech itself — a line of the form "I'm
so-and-so, I work at such-and-such". That is a contributor, and the affiliation
is stated. Take it.

**A name in the title.** Titles attach speaker names in many different shapes —
after a colon, after a dash, after a pipe, in brackets, after the word "with".
Read the name and ignore the punctuation. But be careful: a title can also
contain the name of a company or a person the piece is merely *about*
("How <Company> Is Reinventing X" names a company, not a speaker; "Why <Person>
Was Wrong About Y" names a subject, not an author). Ask which of the three tests
the name passes.

**More than one person, disagreeing.** A piece can carry a platform byline and a
different real author — for instance a post whose header says it is by one
person and whose opening line says it is a guest post by another. **Both are
contributors.** This is not a conflict for you to resolve by choosing one; record
both, with their roles if the text gives them. Multi-author papers likewise get
every named author, even when the text runs them together in one comma-separated
line.

**People who appear but did not make it.** Someone interviewed *about* the topic
by a third party, someone whose paper is being summarised, someone quoted from
elsewhere, someone thanked in the acknowledgements — none of these are
contributors to this piece.

**A bare handle is not a name.** Usernames, account handles and login slugs
(`kvlonge`, `Grandimam`, a handle appearing inside a URL) are not personal names
and do not go in `contributors`, even when a real person is obviously behind
them. Record a person only when the text gives something that reads as a human
name. If a handle is the only attribution the text offers, `contributors` is
empty and you flag `uncertain_person_or_org`.

**Automated accounts are never contributors.** Bots, CI accounts and anything
marked as such (`github-actions[bot]`, `dependabot`) are excluded outright, even
where the text lists them alongside people.

**Names in site furniture do not count.** Many fetched pages carry the site's
own navigation, sidebars, footers, "contributors" widgets, related-article
strips and cookie banners. Names appearing **only** in that furniture are not
contributors to the piece — the piece is the article, README or transcript
itself. If the piece's own text names nobody and the only names are in the
surrounding page chrome, `contributors` is empty; flag `text_unusable` when the
chrome has evidently replaced the content altogether.

**More than one affiliation for one person.** Where someone states several
organisations, record the one they give **for their role in this piece**. If the
text does not distinguish, take the first stated and say so in `notes`. Do not
concatenate them into one string.

**A trailing all-caps token in a byline.** A byline of the form
`By <Name> - <SOMETHING>` is ambiguous: the trailing part may be a publication
or a job title. Decide from the rest of the text — if that string appears
elsewhere as the thing that published the piece, it is the publisher; if it
reads as a description of the person, it is neither a publisher nor part of the
name. When it stays ambiguous, leave `publisher` null and say so in `notes`.

**Ordering.** List contributors in the order the text presents them. Where that
is genuinely ambiguous, put the person most central to making the piece first.

---

## `publisher`

### Definition

**One value.** The channel, publication, show, repository owner, or organisation
that put this piece out. `null` when the text does not make it clear.

- A bare web address is **not** a publisher. Do not convert a host name into a
  publisher; if the only evidence is a URL fragment, the answer is `null`.
- Where a piece appears under both a platform and a publication — a post on a
  blogging platform that belongs to a named publication — prefer **the named
  publication**, because that is the entity a wiki page would be about. The
  platform is infrastructure.
- A self-published piece by an individual has **`null`** as its publisher, not
  the person's name repeated. The person is already in `contributors`; copying
  them into `publisher` creates a false organisation entity.
- Record it **as the text gives it**, without expanding abbreviations or adding
  legal suffixes.

Give a verbatim `evidence` quote for the publisher too, on the same terms as for
contributors.

---

## Flagging

Three things get flagged rather than silently decided. Flagged items go to a
human; they are not failures.

- **`uncertain_person_or_org`** — you cannot tell from the text whether a name
  is a human or an organisation.
- **`text_unusable`** — the file's content is not the piece it claims to be:
  navigation boilerplate instead of an article, a body that stops mid-sentence
  before any attribution, two different pieces concatenated together, or an
  explicit marker that a span of the file was removed. Say which.
- **`unsure`** — anything else that stopped you giving a confident answer, with
  one line saying what.

---

## Output format

One JSON object per item, written to the path you are given. No prose around it.

```json
{
  "instance_id": "<the id in the filename>",
  "contributors": [
    {
      "name": "...",
      "role": "presenter | guest | host | author | maintainer | poster | null",
      "affiliation": "... or null",
      "evidence": "verbatim quote from the text"
    }
  ],
  "publisher": "... or null",
  "publisher_evidence": "verbatim quote from the text, or null",
  "flags": [],
  "notes": "one line, only if something needs saying"
}
```

`contributors` is `[]` when the text names nobody who made the piece. `flags` is
`[]` when nothing needed flagging.
