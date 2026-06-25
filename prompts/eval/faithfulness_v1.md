You are grading a wiki page for faithfulness to its sources. Decompose the page
into atomic factual claims. For EACH claim decide whether it is directly
supported by the SOURCES below; quote the supporting span as evidence, or null
if unsupported.

Return JSON with a "claims" array; each item has "text" (the claim), "supported"
(boolean), and "evidence" (a source quote or null).

SOURCES:
{sources}

PAGE:
{page}
