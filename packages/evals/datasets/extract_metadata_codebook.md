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

You are answering **two independent questions** about the same piece of text, and
they feed different consequences. Answer both for every item; nothing in one
depends on the other.

**Question 1 — who made this, and who put it out?** These labels build **entity
wiki pages**: one page per named thing, accumulating claims across everything the
owner reads, each attributed to who said it.

- **`contributors`** — the **people** who made this piece. These become person
  entities, and a claim in the piece gets attributed to them.
- **`publisher`** — the **organisation** that put it out. This becomes an
  organisation entity and is deliberately kept in a separate field.

The failure that matters most here is putting an **organisation into the person
field**. A wiki that records a company as a human, and attributes opinions to it
as if it had spoken them, is wrong in a way nothing downstream can detect or
repair. When you are unsure whether a name is a person or an organisation, flag
it (see *Flagging*) rather than guessing.

**Question 2 — did this text survive being fetched?**

- **`damaged`** — whether the body arrived broken, such that fetching it again
  would recover substance.

This one has a sharper consequence: a `damaged` item is **failed and sent back to
its owner** with "this needs refetching". Getting it wrong in one direction reads
navigation aloud as though it were an article; in the other it fails perfectly
good readings until the warning stops being read.

The two questions are answered from the same text but are otherwise unrelated. A
piece can be perfectly attributed and badly broken, or anonymous and intact.

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
| `role` | `presenter`, `guest`, `host`, `author`, `maintainer`, or `poster`. Take the role the text names ("I'm your host…"). Where it names none, read it off how the piece attributes them: a byline on written text is `author`, a post header on a social platform is `poster`, a named on-mic or on-camera speaker is `presenter`. Reserve `null` for a person whose relation to the piece the text genuinely leaves open — a second voice in a transcript who is never introduced. |
| `affiliation` | The **largest organisation the text itself names** in that credit — the institution or company, not the department, lab, or country. From "School of Computer Science, Example University, Germany" record `Example University`. Never substitute a parent organisation the text does not name: if the credit says only a named school or division, record that; if it says only "the CS department", record `null`. Where a paper maps authors to affiliations by superscript number, resolve the number. Where a piece speaks in first-person-plural as an organisation, its named authors take that organisation. `null` when the text states none, and never one you happen to know. |
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
  legal boilerplate, an open-roles strip. Names appearing **only** there are not
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
`contributors` is empty, `publisher` is `null`, and you flag
`uncertain_person_or_org`. Only a name that reads as a person's reaches the
exception above.

Note this differs from a **channel line**, which states a publisher outright even
when the channel's name is handle-shaped. A site simply being *titled* with a
handle states nothing; a line naming the channel that carried the piece does. The
flag routes the ambiguous case to a human, which is better than minting an
organisation page named after a username or a domain.

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
This ladder is the whole rule: nothing below it overrides a rung, and there is no
sixth candidate hiding in the prose.

0. **Two things are never the publisher, whatever else fires.** Check these
   first and, if either applies, skip that candidate and keep descending:
   a bare **platform** (Medium, Facebook, Substack, YouTube-the-website, a
   personal domain), and a **person's own name that you have already recorded as
   a contributor** — see "the same name twice" below.
1. **A stated channel, show, publication or masthead.** A `**Channel:** …` line,
   a show name in the opening (*"welcome back to the <name> podcast"*), a named
   publication a post appears under, or a site identity stated in its own
   furniture — a masthead, a logo's alt text, or a copyright line naming an
   organisation that recurs across the page. This outranks everything below it,
   including the maker's own employer.
2. **For a repository**, the owner — the account the repository belongs to.
   Take it from an `owner/repo` heading where one is shown, and otherwise from
   the `owner` segment of the repository URL itself, including where that is the
   only place it appears. A repository's URL path is structured data naming who
   holds the project, not prose to be interpreted, so the "no names from URLs"
   caution below does not reach it: the pipeline reads the same segment
   deterministically and prefers it to any answer given here. Use the project
   name only when no owner exists at all.
3. **For a paper**, a stated venue — *"Published as a conference paper at
   <VENUE>"*, or a copyright line assigning publication rights to a named
   publisher. *"Accepted to"* a venue is **not** *"published at"* it: acceptance
   announces a future event, so it does not name a publisher. Where no venue is
   stated, keep descending — **the archive is not the publisher.** A bare
   `arXiv:` identifier says where the file is hosted, which rung 0 excludes like
   any other platform, and a preprint with no venue reaches rung 5 and is
   `null`.
4. **The organisation the piece speaks as** — see the next section.
5. **Otherwise `null`.** This is a common and correct answer.

**A speaker's employer reaches `publisher` only through rung 4.** A conference
talk carried by a channel is published by the channel, and the speaker saying
*"what we do at <their employer>"* puts that employer in `affiliation`. But where
**no** rung above fires — a transcript with no channel line, a post on a
company's own site — the organisation the piece speaks as *is* the publisher, and
it may legitimately be both the publisher and the author's `affiliation`. The two
fields answer different questions and are allowed to hold the same organisation.

**A venue is not a publisher.** Where a talk happened, which university ran the
course, which conference hosted the session — that is a location, not the entity
that put the recording out, and it is not "speaking as" anything under rung 4.
With no channel line and no publishing voice, a recorded lecture has
`publisher: null` even when it plainly took place somewhere. (A paper's *venue*
under rung 3 is different: a journal or conference that publishes proceedings is
a publisher, a lecture theatre is not.)

**The same name twice.** When the channel, publication, or site name is identical
to a person you have already recorded in `contributors`, `publisher` is `null`.
A self-published piece has one human behind it, and recording that human as both
a person and an organisation mints two wiki entities for one entity — the exact
class confusion this codebook exists to prevent. This covers a personal blog
titled with its owner's name, a Substack under a person's name, and a video
channel named after its presenter, all of which are the same shape.

### On the platform exclusion at rung 0

A platform is infrastructure, not a publisher. Where a piece appears under
**both** a platform and a named publication, the publication is what rung 1
finds; where the platform is the only candidate, keep descending and the answer
is `null`. **A paper's archive is a platform like any other.** arXiv hosts a
preprint the way Medium hosts a post; neither selected it, and rung 3 looks for a
venue that did. A repository host is the same shape — the owner under rung 2 is
the account that published, not GitHub.

### Writing *as* an organisation counts as naming it — rung 4

A piece written from inside a company often never says "published by X" — it
simply speaks as X: *"we shipped this to our own warehouse first"*, *"our
platform team owns the rollout"*. **Treat that as naming the publisher**, but only
after rung 1 has found nothing. First-person organisational voice identifies who
put the piece out as well as a masthead would, and requiring an explicit statement
would null out the publisher on most company engineering blogs.

**The test is the evidence quote, and it has to carry both halves.** This rung
fires only when you can copy **one passage** that contains *both* the
organisation's name *and* the piece speaking in the first person about itself —
`we`, `our`, `us`. Both halves, one quote. *"…pushing **us** beyond **our**
traditional Homes focus"* alongside the name qualifies; *"<Company> is a proud Y
Combinator company"* does not, because nothing in it says the piece is theirs,
and *"At <Company>, we offer everything you or your team need"* does not, because
it is selling to the reader rather than speaking as the maker.

If you cannot produce that one passage, this rung has not fired. Descend to
rung 5 and answer `null`. Do not assemble the two halves from separate parts of
the document — a name in a footer plus a `we` in paragraph nine is not a piece
speaking as its publisher, and this rung was measured reproducing only half the
time while that was left to judgement.

Two exclusions, both of which produce wrong publishers otherwise:

- **The voice must be the maker's, not a mention.** A piece that discusses a
  company in the third person has not told you who published it. Third-person
  self-description — a boilerplate "about us" line written in the third
  person — is a mention, not a voice.
- **Sponsor reads and advertising never establish the publisher.** *"Today's
  episode is brought to you by…"*, *"thanks to <company> for supporting the
  show"*, and the promotional passage that follows are about a third party who
  paid for the slot. In a bare transcript the sponsor is often the *only*
  organisation named — that is a trap, not evidence. Promotional copy reads as
  organisational voice because it uses `we`: *"At <Company>, we offer everything
  you or your team need"* is an advertisement, and the giveaway is that it
  addresses the reader as a customer rather than describing work the piece
  reports on.

**A higher rung always wins, even when rung 4 would also fire.** A repository
README that speaks as its company still takes the owner from rung 2; a post under
a named publication still takes that publication from rung 1. This rung exists
for pieces where nothing above it fired, and the ladder is not a menu.

### Channels and shows keep their name even when it looks like a handle

The rule that a bare handle is not a *name* governs `contributors`, which holds
people. It does not govern `publisher`. A channel whose name is a lowercase
handle is still the channel that published the video, and a channel line in the
text states it outright. Record it under rung 1, and **do not flag it** — a
handle in a channel line is a publisher stated outright, not an ambiguous
attribution. Flag a handle only when it is the sole candidate for a *person*.

### When the byline names an organisation

A byline slot sometimes holds an organisation rather than a person — a company
account, a publication posting under its own name. **That organisation is the
`publisher`, and it does not go in `contributors`.** Never carry a byline into
`contributors` without applying the person test to it; this is the single shape
most likely to produce the org-in-a-person-field error this whole codebook exists
to prevent.

This does not empty `contributors` on its own. If the body also names a human who
wrote the piece, that person is still a contributor — an organisation in the
byline slot and a person named in the text are two facts, not a contradiction.

### Recording the publisher's name

Record it **as the text gives it**, without expanding abbreviations or adding
legal suffixes — but where the text renders it more than one way, prefer the
prose rendering over a URL slug or a heading slug, and **drop a year or edition
number from a venue name**. A venue is one durable entity; recording its year
mints a fresh organisation page annually.

Give a verbatim `evidence` quote for the publisher too, on the same terms as for
contributors. It is required whenever `publisher` is non-null, and it is checked
against the body — publisher is the field with the weaker agreement between
labellers, so it is the one that most needs a citation.

---

## `damaged` — did the body arrive broken?

Something is missing from almost every text, so "is anything missing?" is not the
question. The question is **actionable**: *if this were fetched again, or from a
better source, would more of the piece come back?*

Two things can be wrong with a fetched text and only one is worth failing:

- **The text arrived broken.** A refetch could fix it. → `damaged: true`.
- **The piece leans on things that were never text** — slides, charts, a screen
  recording, figures in a paper. No refetch produces them, because they were
  never words. → `damaged: false`, and see `references_unshown` below.

A talk that constantly points at slides is *normal*, not broken. A paper that
references Figure 4 is *normal*, not broken. Failing those would fail a large
share of everything the owner reads, and the warning would stop being read.

### True when the text is not the piece, or not all of it

- **The content was replaced.** The body is navigation, a menu, a cookie or
  consent wall, a sign-in prompt, or an error message where the article should
  be. The give-away is that you can read the whole file and never reach the thing
  it claims to be.
- **The text stops.** It ends mid-sentence, mid-word, or mid-section, with no
  ending. Also: an explicit marker that a span was removed.
- **A section is announced and then empty.** Headings with nothing under them.
- **Two different pieces are run together** with no boundary.
- **It is a stub.** A summary or opening fragment of something the text itself
  says exists in full elsewhere.

### False when the piece is there, even if

- it refers to slides, images, charts, figures, diagrams or a screen recording;
- a speaker says "as you can see here" about something you cannot see;
- it contains tables or code that would be awkward read aloud;
- it is wrapped in site furniture — navigation, footers, related links — **but
  the piece itself is also there**.

**That last one is the distinction most often got wrong**, and it is the same
furniture test the contributors section uses: chrome *around* a complete article
is not damage, chrome *instead of* the article is. Read far enough to find out
whether the content is present before deciding.

### `cause`, only when damaged

- **`chrome`** — the content was replaced by the site's own furniture: menus, an
  error page, a paywall or consent wall, a sign-in screen.
- **`truncation`** — the content is there but cut: stops mid-thought, an elided
  span, an announced section left empty, a stub of a longer piece.

Where both could apply, choose the one that describes **why the substance is
missing**. `null` when `damaged` is false.

### `references_unshown`

Independent of the other two, and **never a reason to call something damaged**.
True when the piece depends on material that was never text — slides pointed at,
figures referenced, a chart read from, on-screen steps, a diagram gestured at.
Recorded because it tells a reader the piece leans on visuals. Most conference
talks and most papers are `references_unshown: true`, `damaged: false`.

### Reading a long file for this

You cannot decide `damaged` from the opening. An article that starts with a menu
very often has its content further down, and a text that reads fine for 40,000
characters may still stop mid-sentence at the end. **Read the beginning and the
end of every file**, and enough of the middle to know whether the piece is there.

---

## Flagging

Two things get flagged rather than silently decided. Flagged items go to a human;
they are not failures.

- **`uncertain_person_or_org`** — you cannot tell from the text whether a name
  is a human or an organisation.
- **`unsure`** — anything else that stopped you giving a confident answer, with
  one line saying what.

There is no flag for a body that is not the piece it claims to be — that is what
`damaged` records, and it is a field rather than a flag because something acts
on it.

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
  "damaged": false,
  "cause": null,
  "references_unshown": true,
  "damaged_evidence": "verbatim quote from the text, or null",
  "flags": [],
  "notes": "one line, only if something needs saying"
}
```

`contributors` is `[]` when the text names nobody who made the piece, and `flags`
is `[]` when nothing needed flagging — both are common, correct answers.

`damaged_evidence` is the line showing the damage when `damaged` is true; the
line depending on unshown material when only `references_unshown` is true; `null`
when neither.

**Every quote in every `evidence` field must appear in the file character for
character.** They are checked programmatically against the source, and a quote
that is not a real substring invalidates its label — including when the verdict
itself is right. Copy, never retype from memory.

---

## If you were asked for only part of this

A labelling round may cover the attribution questions or the damage question
alone. If so, you were told which; fill only those fields and omit the rest.
Nothing in either answer depends on the other, so a partial round is not a
degraded one.
