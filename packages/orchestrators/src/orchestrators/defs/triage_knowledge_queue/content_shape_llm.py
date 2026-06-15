"""LLM-primary content_shape classifier.

`ContentShapeClassifier` is a Dagster resource that picks one of
`ALL_CONTENT_SHAPES` from a page's enrichment payload via a 2-tier
Groq → OpenAI cascade. Never raises — every failure mode (no key
configured, exception, invalid output, all tiers failed) returns
`(SHAPE_UNKNOWN, metadata_dict)` so the triage asset lands the page
even when the LLM is unavailable.
"""

import json
import os
from dataclasses import asdict
from pathlib import Path

import dagster as dg
import httpx

from .content_shape import ALL_CONTENT_SHAPES, SHAPE_UNKNOWN
from .def_config import CONTENT_SHAPE_CLASSIFIER_PROMPT
from .enrich import EnrichmentSignals

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.3-70b-versatile"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL = "gpt-4.1-mini"

# Resolve repo-root prompts/triage/ for the classifier system prompt.
# Anchor: parents[6] is the repo root from this file's location, matching
# the same trick `fetch_extract_queue.resources` uses for extraction prompts.
# Override with KP_PROMPTS_ROOT env var (evals + tests).
_DEFAULT_PROMPTS_ROOT = Path(__file__).resolve().parents[6] / "prompts"
_PROMPTS_ROOT = Path(os.environ.get("KP_PROMPTS_ROOT", _DEFAULT_PROMPTS_ROOT))
_SYSTEM_PROMPT = (_PROMPTS_ROOT / "triage" / f"{CONTENT_SHAPE_CLASSIFIER_PROMPT}.md").read_text()


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
    """When both keys are None, `classify` short-circuits to SHAPE_UNKNOWN
    so an unconfigured deploy still materialises triage."""

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
