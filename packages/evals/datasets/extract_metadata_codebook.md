# Codebook — `contributors` and `publisher`

This document is handed to a labeller verbatim as their **only** instruction. It
contains no prior labels, no scores, and no record of how earlier rounds went —
any of that would anchor the next labeller and cost the blindness the gold
depends on.

You will be given one file per item. Each file contains a `[content_type: ...]`
tag and then the fetched text of one piece of content. **The text in that file
is the entire evidence base.** You are given no stored metadata alongside it and
must not go looking for any. Label only from the text.

The fetched text often contains URLs, link targets and image filenames. **You may
read them, but a name that appears only inside a URL or a filename is never
enough on its own.** It can corroborate a name the prose already gives; it cannot
supply one.

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
| `affiliation` | The organisation the text states they belong to — the institution or company, **not** the department, lab, or country. From "School of Computer Science, Example University, Germany" record `Example University`. Where a paper maps authors to affiliations by superscript number, resolve the number. `null` when the text states none. Do not supply one you happen to know. |
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
(`b_treadwell`, `pixelmonger`, a handle appearing inside a URL) are not personal names
and do not go in `contributors`, even when a real person is obviously behind
them. Record a person only when the text gives something that reads as a human
name. If a handle is the only attribution the text offers, `contributors` is
empty and you flag `uncertain_person_or_org`.

**Automated accounts are never contributors.** Bots, CI accounts and anything
marked as such (`github-actions[bot]`, `dependabot`) are excluded outright, even
where the text lists them alongside people.

**Furniture is defined by what it does, not by where it sits.** Fetched pages
carry a lot of the surrounding site along with the piece, and the test is which
way a block points:

- **Attribution** — a block that attributes *this* piece. A byline, a "Written
  by" card, an "About the author" box, a speaker credit. **These count**, wherever
  on the page they appear, including at the very bottom next to share buttons.
- **Furniture** — a block that points *away* from the piece: navigation, related
  articles, recommended reading, subscribe and cookie banners, sign-in prompts,
  legal boilerplate, "Related jobs". Names appearing **only** there are not
  contributors.

So a Medium footer reading "Written by <name>", sandwiched between Follow buttons
and a related-posts strip, **is a byline** and the person is a contributor. A name
that appears only in a "you might also like" strip is not.

If the piece's own text names nobody and every name on the page points away from
it, `contributors` is empty; flag `text_unusable` when furniture has evidently
replaced the content altogether.

**Except a personal site named after its owner.** The rule above has one
deliberate exception, because otherwise it swallows a common and clear case. A
personal blog is often titled with the owner's name, so that name appears *only*
in the site header — which is furniture — while the piece itself is written
throughout in the first person and names nobody. Here the header name **is** the
author: record it as a contributor with role `author`, and leave `publisher`
null, because a self-published personal site is not a separate publishing
organisation.

Three things must hold together: the site is named after a person, the piece is
written in the first person, and no other author is named. If the first-person
writing is absent — a link roll, an aggregator, a company site that happens to
carry a person's name — this exception does not apply and the name stays
furniture.

**Apply the handle test first.** If the site name reads as a handle or a bare
domain rather than a human name, this exception does not apply at all:
`contributors` is empty, the handle is the `publisher`, and you flag
`uncertain_person_or_org`. Only a name that reads as a person's reaches the
exception above.

**Write the name as the text gives it.** Do not shorten a full name to a
surname, expand initials, or reorder it. If the text says `Takeshi Yamamoto`,
the label is `Takeshi Yamamoto`, not `Yamamoto`.

**When the text renders a name two ways, take the piece's own rendering.** A
title and a spoken self-introduction often disagree on capitalisation or a
particle (`Van Der Berg` vs `van der Berg`), and an image filename or an
all-caps header is not a rendering at all. Prefer, in order: how the person
introduces themselves, how the body prose writes it, then the title. Note the
variant in `notes`. This matters because the label decides whether the wiki gets
one person page or two.

**A first name alone is still a name — record it, and never complete it.**
Interviews and podcasts often name their people only by first name: a host
addressed as "Dan", a guest introduced as "Matt, welcome to the show". Record
that person with the name the text gives and flag `unsure`.

**Under no circumstances supply a surname the text does not contain.** If you
find yourself confident that "Matt" is a particular well-known person, that
confidence came from outside the text and the codebook's scope rule forbids it.
A completed name is indistinguishable from a correct one downstream, and it is
the single hardest error for anything later in the pipeline to detect.

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
that **distributed** this piece — who put it out, not who the maker works for.
`null` when the text does not identify one.

`null` is a common and correct answer. Most personal blog posts, most posts on a
bare platform, and most transcripts with no channel line have no publisher.

### Work down this ladder and stop at the first rung that applies

The rungs are in order **because several of them can fire on the same document**,
and the field takes one value. Do not skip to the rung you find most interesting.

1. **A stated channel, show, publication or masthead.** A `**Channel:** …` line,
   a show name in the opening (*"welcome back to the <name> podcast"*), a named
   publication a post appears under. This outranks everything below it, including
   the maker's own employer.
2. **A named venue, for papers.** See the papers rung below.
3. **The organisation the piece speaks as** — see the next section.
4. **The repository owner or project name, for code**, when it appears in the
   repo heading or the prose. Not when it appears only inside a URL.
5. **Otherwise `null`.**

**A speaker's employer is an `affiliation`, not a publisher.** A conference talk
carried by a channel is published by the channel; the fact that the speaker says
*"what we do at <their employer>"* puts that employer in their `affiliation`
field and nowhere else. Rung 1 exists to settle exactly this case, which is the
single most common collision in this corpus.

**A venue is not a publisher.** Where a talk happened, which university ran the
course, which conference hosted the session — that is a location, not the entity
that put the recording out. With no channel line and no publishing voice, a
recorded lecture has `publisher: null` even when it plainly took place somewhere.

### Two things that are never the publisher

- **A bare platform.** Medium, Facebook, Substack, YouTube-the-website, a
  personal domain. These are infrastructure. Where a piece appears under **both**
  a platform and a named publication, take the publication; where the platform is
  the only candidate, the answer is `null`. (Papers are the one exception — see
  the papers rung.)
- **A person's own name, as a self-published byline.** The person is already in
  `contributors`; repeating them in `publisher` mints a false organisation entity.
  Note this does *not* apply when a **named channel or show** happens to carry a
  person's name — a channel is a distributor, so it goes in `publisher` under
  rung 1 even if it is named after its presenter.

### Writing *as* an organisation counts as naming it — rung 3

A piece written from inside a company often never says "published by X" — it
simply speaks as X: *"we shipped this to our own warehouse first"*, *"our
platform team owns the rollout"*. **Treat that as naming the publisher**, but only
after rung 1 has found nothing. First-person organisational voice identifies who
put the piece out as well as a masthead would, and requiring an explicit statement
would null out the publisher on most company engineering blogs.

Two exclusions, both of which produce wrong publishers otherwise:

- **The voice must be the maker's, not a mention.** A piece that discusses a
  company in the third person has not told you who published it.
- **Sponsor reads and advertising never establish the publisher.** *"Today's
  episode is brought to you by…"*, *"thanks to <company> for supporting the
  show"*, and the promotional passage that follows are about a third party who
  paid for the slot. In a bare transcript the sponsor is often the *only*
  organisation named — that is a trap, not evidence.

### Channels and shows keep their name even when it looks like a handle

The rule that a bare handle is not a *name* governs `contributors`, which holds
people. It does not govern `publisher`. A channel whose name is a lowercase
handle is still the channel that published the video, and a channel line in the
text states it outright. Record it under rung 1.

### When the byline names an organisation

A byline slot sometimes holds an organisation rather than a person — a company
account, a publication posting under its own name. **`contributors` is empty and
that organisation is the `publisher`.** Never carry a byline into `contributors`
without applying the person test to it; this is the single shape most likely to
produce the org-in-a-person-field error this whole codebook exists to prevent.

### Platform versus venue, for papers and preprints

A paper carries two candidates: the archive hosting the file, and the venue that
published or will publish it.

- A **stated venue wins**: *"Published as a conference paper at ICLR 2026"*, or a
  copyright line assigning publication rights to a named publisher.
- **Otherwise the archive is the publisher** — a bare `arXiv:` identifier with no
  venue named means the publisher is `arXiv`.
- *"Accepted to"* a venue is **not** *"published at"* it. A paper announcing
  future acceptance is still published by the archive today.

Record the publisher **as the text gives it**, without expanding abbreviations or
adding legal suffixes.

Give a verbatim `evidence` quote for the publisher too, on the same terms as for
contributors. It is required whenever `publisher` is non-null, and it is checked
against the body — publisher is the field with the weaker agreement between
labellers, so it is the one that most needs a citation.

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
