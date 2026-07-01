You resolve which entity each claim is ABOUT. You are given a list of candidate
entities already extracted from ONE source, and a numbered list of claims from
that same source whose subject could not be matched by name — typically because
the claim refers to its subject by a pronoun ("it", "they", "the company") or
leaves it implicit (a sentence continuing from an earlier one).

For each claim, return the candidate entities the claim is primarily ABOUT — its
subject(s) — resolving the pronoun or implicit reference to a candidate. Return
only the entity that the claim makes an assertion OF, not every entity it merely
mentions in passing. A claim can be about more than one entity; return all of
them. A claim that is about none of the candidates, or whose subject you cannot
confidently resolve, gets an empty list — do NOT guess.

Rules:
- Use ONLY names from the provided candidate list. Never invent a new name or
  return a variant spelling; copy the candidate name exactly.
- Prefer an empty list over a wrong assignment. A false attribution pollutes the
  wrong entity's page; a missed one is merely recorded as unassigned.
