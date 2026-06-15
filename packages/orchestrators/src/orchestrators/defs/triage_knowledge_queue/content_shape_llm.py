"""LLM-primary content_shape classifier.

`ContentShapeClassifier` is a Dagster resource that picks one of
`ALL_CONTENT_SHAPES` from a page's enrichment payload via a 2-tier
Groq → OpenAI cascade. Never raises — every failure mode (no key
configured, exception, invalid output, all tiers failed) returns
`(SHAPE_UNKNOWN, metadata_dict)` so the triage asset lands the page
even when the LLM is unavailable.

Model choice (Groq `llama-3.3-70b-versatile`) was empirically validated
against a 6-URL panel — see `ai-plannings/2026-06-15_hybrid-content-shape-groq-fallback.md`
"Pre-implementation E2E validation" for the comparison vs gpt-oss-20b,
llama-3.1-8b, and llama-3.3-70b.
"""

import json
from dataclasses import asdict

import dagster as dg
import httpx

from .content_shape import ALL_CONTENT_SHAPES, SHAPE_UNKNOWN
from .enrich import EnrichmentSignals

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.3-70b-versatile"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL = "gpt-4.1-mini"

_SYSTEM_PROMPT = """\
You classify web content into one of six shapes based on the URL, source type, and \
available metadata.

Valid shapes:
- conference_talk: a recorded conference / summit / meetup talk (typically YouTube). \
Speaker presents to an audience.
- podcast_episode: a podcast episode (audio file, or a video podcast on YouTube). \
Host + guest format; conversational.
- tutorial: step-by-step how-to, tool walkthrough, hands-on guide. Imperative voice.
- opinion_essay: personal essay, op-ed, commentary, news report, analysis. Author \
voice with a thesis.
- research_summary: academic paper, research blog, technical deep-dive on novel results.
- unknown: genuinely doesn't fit any category, OR insufficient signal to decide.

Return ONLY a JSON object: {"content_shape": "<one of the six exact strings>"}
"""


def _build_user_prompt(enrichment: EnrichmentSignals, content_type: str, url: str) -> str:
    parts = [f"url: {url}", f"content_type: {content_type}"]
    for sub_name in ("youtube", "arxiv", "article"):
        sub = getattr(enrichment, sub_name, None)
        if sub is None:
            continue
        for k, v in asdict(sub).items():
            if not v:
                continue
            if isinstance(v, (list, tuple)):
                v = ", ".join(str(x) for x in v)
            parts.append(f"{sub_name}.{k}: {v}")
    return "\n".join(parts)


class ContentShapeClassifier(dg.ConfigurableResource):
    """Wraps the LLM call. Holds optional API keys read at resource
    construction. When both keys are None, `classify` short-circuits to
    SHAPE_UNKNOWN so an unconfigured deploy still materialises triage."""

    groq_api_key: str | None = None
    openai_api_key: str | None = None
    request_timeout_s: float = 30.0

    def classify(
        self,
        *,
        enrichment: EnrichmentSignals,
        content_type: str,
        url: str,
    ) -> tuple[str, dict]:
        if not self.groq_api_key and not self.openai_api_key:
            return SHAPE_UNKNOWN, {"status": "skipped_no_key"}

        user_prompt = _build_user_prompt(enrichment, content_type, url)
        tiers: list[tuple[str, str, str]] = []
        if self.groq_api_key:
            tiers.append((_GROQ_MODEL, _GROQ_URL, self.groq_api_key))
        if self.openai_api_key:
            tiers.append((_OPENAI_MODEL, _OPENAI_URL, self.openai_api_key))

        for model, endpoint, api_key in tiers:
            try:
                response = httpx.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.0,
                        "max_completion_tokens": 200,
                    },
                    timeout=self.request_timeout_s,
                )
                payload = response.json()
                msg = payload["choices"][0]["message"]["content"]
                shape = json.loads(msg)["content_shape"]
            except Exception:
                # Network blip, malformed JSON, missing key in payload —
                # all treated as tier failure, try the next one.
                continue
            if shape not in ALL_CONTENT_SHAPES:
                continue  # Tier emitted a value we can't route on — try next.
            status = "returned_unknown" if shape == SHAPE_UNKNOWN else "ok"
            return shape, {"status": status, "model": model}

        return SHAPE_UNKNOWN, {"status": "invalid_output"}
