You are grading whether a wiki page about "{entity}" preserves the concrete
specifics from its sources. Considering ONLY specifics relevant to {entity}:

- names_orgs: named people and organisations in the SOURCES; for each, is it
  preserved on the PAGE?
- quotes: direct quotes in the SOURCES; for each, is it preserved on the PAGE?
- abstractions: places where the PAGE replaced a source specific (a name, number,
  or quote) with a vague placeholder (e.g. "a researcher" for a named person).
  Omitting a low-value mention is NOT an abstraction.

Return JSON with "names_orgs" (items: anchor, preserved), "quotes" (items: quote,
preserved), and "abstractions" (items: source_specific, page_placeholder).

SOURCES:
{sources}

PAGE:
{page}
