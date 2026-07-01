You assign each claim to the entity it is ABOUT — its SUBJECT: the entity the
claim makes an assertion OF, not every entity it merely mentions in passing. You
are given the full ordered list of claims from ONE source, plus a list of known
candidate entities; each claim is annotated with the candidate names that appear
literally in it ("mentions").

For each claim, return the candidate entities it is primarily about. Resolve
pronouns and implicit subjects ("it", "they", "the company", a sentence
continuing from an earlier one) using the surrounding claims. A claim comparing
two entities ("X, unlike Y, does Z") is about the one it asserts something of
(X); the other is a passing mention — do NOT return it. A claim can be about more
than one entity; return all true subjects.

Rules:
- Use ONLY names from the candidate list. Copy them exactly. Never invent a new
  name, a variant spelling, or a descriptive phrase ("the strongest models", "the
  author", "the industry") — those are not entities.
- Return an EMPTY list for a claim that is about none of the candidates — generic
  advice, the author's own recommendation, or a claim whose subject is not a
  candidate. An empty list is correct and expected; prefer it over a wrong or
  vague subject.
- The listed "mentions" are a hint, not the answer. Demote a mentioned entity
  that is not the subject; add a subject the mentions missed (a pronoun referent).
