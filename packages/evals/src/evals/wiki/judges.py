"""Wiki page-quality judges (eval Phase 1).

Reference-light, source-grounded judges over a synthesised wiki page. Each judge
is a frozen dataclass with an injected `chat_fn: Callable[[str], dict]` (matches
`evals.core.judges.LLMJudge`): the judge assembles the prompt, `chat_fn` runs the
judge LLM and returns parsed JSON. Tests pass a stub; production wires a thin
wrapper around `workflows.llm.generate_structured_with_usage`.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

# High-signal numeric specifics only — money, percentages, 4-digit years. Bare
# integers are deliberately excluded: codex flagged that an unguarded \d+ regex
# over-extracts noise (publication dates, IDs, benchmark names, team counts).
_NUMERIC_ANCHOR_RE = re.compile(
    r"\$\d[\d,]*(?:\.\d+)?[KMB]?"  # money: $5M, $1,000, $5
    r"|\d+(?:\.\d+)?%"  # percent: 30%, 30.5%
    r"|\b(?:19|20)\d{2}\b"  # year: 1900-2099
)


def extract_numeric_anchors(text: str) -> set[str]:
    """Deterministic high-signal numeric specifics from `text` (money/percent/year)."""
    return set(_NUMERIC_ANCHOR_RE.findall(text))


_MONTH = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
# Month-precision dates only: ISO (2010-03-15) or "Month YYYY". Bare years are
# already covered by extract_numeric_anchors; here we capture the finer specific.
_DATE_ANCHOR_RE = re.compile(rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b(?:{_MONTH})\s+\d{{4}}\b")


def extract_date_anchors(text: str) -> set[str]:
    """Deterministic month-precision date specifics from `text`."""
    return set(_DATE_ANCHOR_RE.findall(text))


def anchor_recall(anchors: set[str], page: str) -> float:
    """Fraction of source `anchors` that survive (appear verbatim) on the `page` —
    the specificity preservation metric."""
    if not anchors:
        return 1.0
    present = sum(1 for a in anchors if a in page)
    return present / len(anchors)


def numbers_dates_recall(sources: Sequence[str], page: str) -> float:
    """Deterministic recall of numeric + date specifics from `sources` onto `page`.
    (A date and the bare year inside it count as distinct anchors — a small
    double-count that mildly over-weights dropped dates; revisit after calibration.)"""
    anchors: set[str] = set()
    for source in sources:
        anchors |= extract_numeric_anchors(source)
        anchors |= extract_date_anchors(source)
    return anchor_recall(anchors, page)


FAITHFULNESS_PROMPT = """\
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
"""


def _grounding_block(sources: Sequence[str], prior_sources: Sequence[str]) -> str:
    """Assemble the grounding text. For UPDATE pages, prior sources are included
    (labelled) so claims carried over from earlier sources aren't falsely flagged
    unsupported when only the new source is shown."""
    new = "\n\n---\n\n".join(sources)
    if not prior_sources:
        return new
    prior = "\n\n---\n\n".join(prior_sources)
    return f"PRIOR SOURCES (already reflected in the page):\n{prior}" f"\n\nNEW SOURCES:\n{new}"


@dataclass(frozen=True)
class Claim:
    text: str
    supported: bool
    evidence: str | None = None


@dataclass(frozen=True)
class FaithfulnessScore:
    claims: list[Claim]
    unsupported_count: int
    grounded_fraction: float
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FaithfulnessJudge:
    chat_fn: Callable[[str], dict]
    prompt_template: str = FAITHFULNESS_PROMPT

    def score(
        self,
        *,
        page: str,
        sources: Sequence[str],
        prior_sources: Sequence[str] = (),
    ) -> FaithfulnessScore:
        prompt = self.prompt_template.format(
            page=page, sources=_grounding_block(sources, prior_sources)
        )
        raw = self.chat_fn(prompt)
        if not isinstance(raw.get("claims"), list):
            raise ValueError(
                f"faithfulness judge returned no 'claims' array (got keys: {sorted(raw)})"
            )
        claims = [
            Claim(
                text=c["text"],
                supported=bool(c["supported"]),
                evidence=c.get("evidence"),
            )
            for c in raw["claims"]
        ]
        unsupported = sum(1 for c in claims if not c.supported)
        grounded = (len(claims) - unsupported) / len(claims) if claims else 1.0
        return FaithfulnessScore(
            claims=claims,
            unsupported_count=unsupported,
            grounded_fraction=grounded,
            metadata={"raw": raw},
        )


SPECIFICITY_PROMPT = """\
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
"""


def _recall_from_flags(items: list[dict]) -> float:
    """Recall over LLM-flagged anchors: preserved / total (vacuous 1.0 if none)."""
    if not items:
        return 1.0
    return sum(1 for it in items if it.get("preserved")) / len(items)


@dataclass(frozen=True)
class SpecificityScore:
    numbers_dates_recall: float
    names_orgs_recall: float
    quote_recall: float
    abstraction_penalty: int  # count of source-specific → page-placeholder swaps
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SpecificityJudge:
    """Hybrid specificity judge: deterministic numeric/date recall + LLM-judged
    name/org/quote preservation and abstraction penalty. Sub-recalls are kept
    SEPARATE (not blended) so a single failure mode can't hide (codex)."""

    chat_fn: Callable[[str], dict]
    prompt_template: str = SPECIFICITY_PROMPT

    def score(
        self,
        *,
        entity: str,
        page: str,
        sources: Sequence[str],
        prior_sources: Sequence[str] = (),
    ) -> SpecificityScore:
        nd_recall = numbers_dates_recall([*sources, *prior_sources], page)
        prompt = self.prompt_template.format(
            entity=entity,
            page=page,
            sources=_grounding_block(sources, prior_sources),
        )
        raw = self.chat_fn(prompt)
        return SpecificityScore(
            numbers_dates_recall=nd_recall,
            names_orgs_recall=_recall_from_flags(raw.get("names_orgs", [])),
            quote_recall=_recall_from_flags(raw.get("quotes", [])),
            abstraction_penalty=len(raw.get("abstractions", [])),
            metadata={"raw": raw},
        )
