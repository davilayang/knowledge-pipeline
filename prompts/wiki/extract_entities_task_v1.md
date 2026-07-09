TASK — extract the named entities the source document above is genuinely ABOUT,
on its own terms. The claims already extracted from it are given at the end as a
salience signal.

Extract an entity only if the source says something specific about it — a claim,
comparison, number, or judgment. Use the claims to judge salience, but also
extract entities the source discusses that the claims name only implicitly (the
main subject named in the title, a tool compared in passing).

DO extract:
- Named people, organisations, products, tools, technologies, datasets, methods,
  and named concepts/frameworks the source discusses.
- The source's main subject even if named only in the title.
- Minor / long-tail named entities the source genuinely discusses, even once —
  there is NO cap. Downstream salience decides which get a page; your job is
  recall of REAL entities.
- The author when the piece presents their own argument or analysis, and anyone
  to whom the source attributes a substantive view or work.

DO NOT extract (these are the failure modes):
- Publication / social / site chrome: the outlet the piece is PUBLISHED in — its
  masthead or byline publication / newsletter — is never an entity, even when
  named plainly with no URL (e.g. a Medium/Ghost/Substack publication name in the
  byline). This includes the case where the byline names the PUBLICATION ITSELF
  instead of a person (no individual human author): do not extract that outlet as
  an author, person, or organization — it is chrome, not the subject. Only extract
  a byline as a person when it is a real individual presenting their own argument.
  Also skip "follow me on X / Twitter / Instagram", subscribe/sponsor blurbs,
  related-post lists, comment threads. A name appearing ONLY in author-promo or a
  footer is not an entity.
- Example / placeholder / demo data invented to illustrate a point: companies,
  people, users, files, and datasets that exist ONLY inside the source's worked
  example, walkthrough, tutorial demo, or hypothetical scenario — e.g. a demo's
  "ACME Corp" and its "Jane Smith" user, a sample "refund_policy.pdf", and the
  domain terms present only because the demo happens to be about finance
  ("ebitda", "capital expenditure"). Drop them EVEN IF the example names them many
  times. Extract the technique the source teaches, not the props in its examples.
- Pure local code identifiers: variable, function, or parameter names from code
  blocks ("sql_query", "transformer_embed"). BUT a real product or library written
  in code / import form IS an entity — normalise it to its human name
  ("graphiti_core" → Graphiti, "pydantic_ai" → Pydantic AI).
- Generic common-knowledge terms ("API", "CLI", "database", "logging",
  "debugging", "production environment") unless the source makes a page-worthy
  claim about that thing specifically.

Use the natural human display name, not a code/import form. Prefer the SINGULAR
form for concepts, methods, and roles ("Code review" not "Code reviews",
"Analytics engineer" not "Analytics engineers") — keep the plural only when it is
the established proper name. Type each entity as exactly one of: concept, tool,
person, organization, method, dataset, trend, other. Prefer a specific type; use
"other" sparingly.

Output ONLY a flat list, one entity per line, in exactly this form:

Name — type

No commentary, no numbering, no blank lines. If the source is about no durable
entity (pure chrome or a bare link roundup), output the single line: NONE

CLAIMS ALREADY EXTRACTED:
{claims}
