"""Extraction scorers — one per output the extractor produces.

- `TopicCardScorer` — per-field Topic Card scoring (exact / embedding / LLM
  judge per field; overall = unweighted mean).
- `NarrativeCoverageScorer` — per-gold-thread present/absent coverage over
  `narrative_md` (coverage@present).
- `NarrativeFidelityScorer` — three-metric fidelity floor over the narrative
  threads (omission / corruption / invention).

Callers inject `embed_fn` / `chat_fn` so every scorer carries no provider
dependency — tests pass stubs; runtime callers wire OpenAI clients.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from evals.core import FieldScore
from evals.core.judges import EmbeddingSimilarityJudge, ExactMatchJudge, LLMJudge
from evals.extraction.fidelity import (
    distortion_rate,
    fabrication_rate,
    faithful_recall,
    severe_omission_count,
)

if TYPE_CHECKING:
    from evals.core import FixtureRun
    from evals.extraction.types import ExtractionFixture

# extracted_title moved from exact-match to embedding similarity after the
# first real benchmark run scored every fixture's title at 0.0 — LLMs basically
# never reproduce a hand-written reference title verbatim, so exact match is
# pure noise. ExactMatchJudge is retained for future use (e.g. tag-like fields).
_EXACT_FIELDS: tuple[str, ...] = ()
_EMBED_FIELDS = ("extracted_title", "core_mechanism", "transferable_pattern")
_LLM_FIELDS = ("best_example", "main_tension", "candidate_tie_backs")

DEFAULT_LLM_PROMPT = """\
You are a strict judge comparing two extractions per field. For each field in
{fields}, score the actual output against the expected output on a 0.0-1.0
scale where 1.0 means equivalent meaning and 0.0 means unrelated.

Expected: {expected}
Actual:   {actual}

Return a JSON object mapping each field name to a float score.
"""


class TopicCardScorer:
    # Selection name read by run_benchmark (which is scorer-agnostic — it hands
    # the scorer the (fixture, run) pair and reads this name for the report).
    name = "TopicCardScorer"

    def __init__(
        self,
        *,
        embed_fn: Callable[[str], list[float]],
        chat_fn: Callable[[str], dict],
        llm_prompt_template: str = DEFAULT_LLM_PROMPT,
    ) -> None:
        self._exact = ExactMatchJudge(fields=_EXACT_FIELDS)
        self._embed = EmbeddingSimilarityJudge(fields=_EMBED_FIELDS, embed_fn=embed_fn)
        self._llm = LLMJudge(
            fields=_LLM_FIELDS, chat_fn=chat_fn, prompt_template=llm_prompt_template
        )

    def score_run(self, *, fixture: "ExtractionFixture", run: "FixtureRun") -> FieldScore:
        """Selection adapter — pull the Topic Card pair from the fixture/run and score."""
        actual = run.output.get("topic_card", {}) if run.output else {}
        return self.score(expected=fixture.expected_topic_card, actual=actual)

    def score(
        self,
        *,
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> FieldScore:
        exp = _flatten(expected)
        act = _flatten(actual)
        a = self._exact.score(expected=exp, actual=act)
        b = self._embed.score(expected=exp, actual=act)
        c = self._llm.score(expected=exp, actual=act)
        merged = {**a.value, **b.value, **c.value}
        overall = sum(merged.values()) / len(merged) if merged else 0.0
        return FieldScore(
            value={**merged, "__overall__": overall},
            metadata={
                "judge_per_field": {
                    **{f: "exact" for f in _EXACT_FIELDS},
                    **{f: "embedding" for f in _EMBED_FIELDS},
                    **{f: "llm" for f in _LLM_FIELDS},
                },
            },
        )


DEFAULT_COVERAGE_PROMPT = """\
You are a strict coverage judge. Below is a CANDIDATE narrative and a numbered
list of GOLD threads (each thread is a distinct point a listener could ask a
follow-up about). For each gold thread, decide whether the candidate narrative
covers that thread's specific content — its anchor (a name, number, mechanism,
or specific example). A thread counts as present (1) only if that specific
content appears; a vague, partial, or figure-dropping mention counts as absent
(0). Do not reward the candidate for topically-related prose that misses the
thread's anchor.

CANDIDATE NARRATIVE:
{narrative}

GOLD THREADS ({n}):
{threads}

Return a JSON object mapping each thread's number (as a string) to 1 or 0.
"""


class NarrativeCoverageScorer:
    """Per-gold-thread present/absent coverage over `narrative_md`.

    Coverage@present = (# gold threads the candidate covers) / (total gold
    threads). One batched LLM call per fixture judges every thread at once;
    `chat_fn` is injected so the scorer carries no provider dependency (tests
    pass a stub). The per-thread present/absent map is preserved in metadata so
    misses are inspectable in the workbench.
    """

    name = "NarrativeCoverageScorer"

    def __init__(
        self,
        *,
        chat_fn: Callable[[str], dict],
        prompt_template: str = DEFAULT_COVERAGE_PROMPT,
        max_retries: int = 2,
    ) -> None:
        self._chat = chat_fn
        self._tmpl = prompt_template
        # Batched LLM judges occasionally drop keys on a large thread list; retry
        # the call before giving up, so one transient flake doesn't abort a
        # multi-fixture / multi-run harness. A persistently incomplete map still
        # raises (fail loud, never mis-score).
        self._max_retries = max_retries

    def score_run(self, *, fixture: "ExtractionFixture", run: "FixtureRun") -> FieldScore:
        """Selection adapter — pull gold_threads + narrative_md from the fixture/run."""
        narrative = (run.output or {}).get("narrative_md", "") if run.output else ""
        return self.score(
            expected={"gold_threads": fixture.gold_threads or []},
            actual={"narrative_md": narrative},
        )

    def score(self, *, expected: dict[str, Any], actual: dict[str, Any]) -> FieldScore:
        threads: list[str] = list(expected.get("gold_threads") or [])
        narrative = actual.get("narrative_md", "") or ""
        if not threads:
            return FieldScore(value={"__overall__": 0.0}, metadata={"per_thread": {}, "raw": {}})
        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(threads))
        prompt = self._tmpl.format(narrative=narrative, threads=numbered, n=len(threads))
        # Validate the keyset before scoring: a broken/empty judge map or a
        # 1-based response must FAIL loudly, not masquerade as legitimate 0%
        # coverage (which would silently hide a real regression) or mis-align
        # verdicts onto the wrong threads. Retry a transient incomplete map first.
        expected_keys = {str(i) for i in range(len(threads))}
        raw: dict = {}
        for _attempt in range(self._max_retries + 1):
            raw = self._chat(prompt)
            if expected_keys.issubset({str(k) for k in raw}):
                break
        else:
            missing = sorted(expected_keys - {str(k) for k in raw}, key=int)
            preview = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
            raise ValueError(
                f"coverage judge returned an incomplete/misaligned verdict map after "
                f"{self._max_retries + 1} attempts: missing keys [{preview}] "
                f"(expected 0..{len(threads) - 1}). Broken judge output — not a real 0% coverage."
            )
        per_thread: dict[str, float] = {}
        for i, thread in enumerate(threads):
            present = 1.0 if _is_present(raw.get(str(i), raw.get(i, 0))) else 0.0
            per_thread[thread] = present
        coverage = sum(per_thread.values()) / len(threads)
        return FieldScore(
            value={"__overall__": coverage},
            metadata={
                "judge_name": "coverage_present_absent",
                "per_thread": per_thread,
                "raw": raw,
            },
        )


DEFAULT_FIDELITY_PROMPT = """\
You are a fidelity judge for a knowledge-extraction eval. Below is a CANDIDATE
summary and a numbered list of GOLD reference points from the same source. For
each gold point, decide how the candidate represents it. Use these definitions —
they are calibrated against human rulings, so apply them literally rather than
your own instinct for "strict" vs "lenient":

- "faithful": the point's claim AND its meaning-bearing specifics are present.
  STILL faithful when the candidate drops DERIVABLE scaffolding (a sub-value you
  could reconstruct from what's kept — e.g. "3 posts and 8 votes" behind the
  ratio 0.375), drops an ILLUSTRATIVE example while fully stating the claim it
  illustrated, or compresses wording while the essence survives.
- "distorted": what the candidate KEPT is corrupted — a wrong/flipped figure or
  attribution; a number stripped of the context that carries its meaning (e.g.
  "30/30 confirmed" without the denominator/precision that shows it's rare, not
  universal); a specific vaguened into an abstraction (e.g. "row-level security"
  → "security policies", or a "only one field required" point diluted into a
  multi-field structure); or false completeness (a closed count when the source
  listed more).
- "absent": the point is missing, too vague to count, OR reduced to a generic
  restatement that loses its distinctive specifics (e.g. a concrete step-by-step
  walkthrough compressed to "navigates the files"). Test: is what remains still
  THIS specific point, or has it collapsed into a restatement of a more general
  claim? Collapsed → absent.

If a gold point enumerates a LIST, its members ARE the information: a member the
candidate simply left out lowers fidelity toward "absent" (an omission), NOT
"distorted" — reserve "distorted" for a member that's vaguened, or a partial list
the candidate implies is complete.

CANDIDATE SUMMARY:
{narrative}

GOLD REFERENCE POINTS ({n}):
{threads}

Return a JSON object mapping each point's number (as a string) to exactly one of
"faithful", "distorted", or "absent".
"""


DEFAULT_FABRICATION_PROMPT = """\
You are a strict fabrication judge. Below is the SOURCE text and a numbered list
of PRODUCED threads that an extractor claims are in it. For each produced thread,
decide whether its specific content — a claim, figure, entity, or causal link —
is actually supported by the SOURCE. Flag `true` only when the thread asserts
something the source does not contain (an invention). Mild bridging that the
source plainly implies is `false` (not fabricated).

SOURCE:
{source}

PRODUCED THREADS ({n}):
{threads}

Return a JSON object mapping each thread's number (as a string) to a boolean:
`true` if fabricated (not in the source), `false` if supported.
"""


class NarrativeFidelityScorer:
    """Three-metric fidelity floor over `narrative_v2`'s threads.

    Reads `gold_threads` + `critical_threads` and scores omission
    (`faithful_recall`), corruption (`distortion_rate`), and invention
    (`fabrication_rate`) via two injected judges — a fidelity judge (gold thread
    → faithful/distorted/absent against the candidate) and a fabrication judge
    (produced thread → invented-bool against the source). Both `chat_fn`s are
    injected so the scorer carries no provider dependency (tests pass stubs).
    """

    name = "NarrativeFidelityScorer"

    def __init__(
        self,
        *,
        fidelity_chat_fn: Callable[[str], dict],
        fabrication_chat_fn: Callable[[str], dict],
        fidelity_prompt_template: str = DEFAULT_FIDELITY_PROMPT,
        fabrication_prompt_template: str = DEFAULT_FABRICATION_PROMPT,
    ) -> None:
        self._fidelity_chat = fidelity_chat_fn
        self._fabrication_chat = fabrication_chat_fn
        self._fidelity_tmpl = fidelity_prompt_template
        self._fabrication_tmpl = fabrication_prompt_template

    def score(self, *, expected: dict[str, Any], actual: dict[str, Any]) -> FieldScore:
        threads: list[str] = list(expected.get("gold_threads") or [])
        narrative = actual.get("narrative_md", "") or ""
        if not threads:
            return FieldScore(value={"faithful_recall": 0.0}, metadata={"per_thread": {}})
        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(threads))
        prompt = self._fidelity_tmpl.format(narrative=narrative, threads=numbered, n=len(threads))
        raw = self._fidelity_chat(prompt)
        verdicts = [_coerce_fidelity(raw.get(str(i), raw.get(i))) for i in range(len(threads))]
        critical = list(expected.get("critical_threads") or [])
        produced: list[str] = list(actual.get("threads") or [])
        invented = self._judge_fabrication(
            source=expected.get("source", "") or "", produced=produced
        )
        return FieldScore(
            value={
                "faithful_recall": faithful_recall(verdicts),
                "distortion_rate": distortion_rate(verdicts),
                "fabrication_rate": fabrication_rate(invented),
                "severe_omissions": float(severe_omission_count(verdicts, critical)),
            },
            metadata={
                "per_thread": {threads[i]: verdicts[i] for i in range(len(threads))},
                "raw": raw,
                "invented": {produced[i]: invented[i] for i in range(len(produced))},
            },
        )

    def _judge_fabrication(self, *, source: str, produced: list[str]) -> list[bool]:
        """Flag each produced thread as invented (True) if unsupported by source."""
        if not produced:
            return []
        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(produced))
        prompt = self._fabrication_tmpl.format(source=source, threads=numbered, n=len(produced))
        raw = self._fabrication_chat(prompt)
        return [_coerce_invented(raw.get(str(i), raw.get(i))) for i in range(len(produced))]


def _coerce_fidelity(v: Any) -> str:
    """Normalise a judge's per-thread verdict to faithful/distorted/absent.

    Unknown or missing verdicts default to `absent` — a false-pass-averse floor
    never credits fidelity the judge didn't clearly assert.
    """
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("faithful", "distorted", "absent"):
            return s
    return "absent"


def _coerce_invented(v: Any) -> bool:
    """Coerce a fabrication judge's per-thread verdict to an invented bool.

    Unknown/missing defaults to `False` (not fabricated) — fabrication is judged
    against the source, and absent evidence of invention isn't invention.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v >= 0.5
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "fabricated", "invented"}
    return False


def _is_present(v: Any) -> bool:
    """Coerce a judge's per-thread verdict to a present/absent bool. Partial = absent."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v >= 0.5
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "present"}
    return False


def _flatten(card: dict[str, Any]) -> dict[str, str]:
    """Coerce list-valued fields to strings; preserve order via newline join."""
    out: dict[str, str] = {}
    for k, v in card.items():
        if isinstance(v, list):
            out[k] = "\n".join(str(x) for x in v)
        elif v is None:
            out[k] = ""
        else:
            out[k] = str(v)
    return out
