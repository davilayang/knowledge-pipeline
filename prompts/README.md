# `prompts/` — Versioned prompt assets

Markdown prompt bodies consumed by the workflow layer. Treated as **content artefacts**, not code — versioned in git, reviewable in PRs, decoupled from package boundaries.

## Layout

````
prompts/
  extraction/     # consumed by workflows.extraction (ThreeCallOpenAIExtractor)
  # future: wiki_synthesis/, knowledge_graph/, etc.
````

## Resolution

Consumers resolve the prompts root via the `KP_PROMPTS_ROOT` env var, defaulting to the repo-root `prompts/` directory. The orchestrator's `ExtractorRegistry` adapter (`packages/orchestrators/.../extract_complex_contents/resources.py`) defines:

```python
_DEFAULT_PROMPTS_ROOT = Path(__file__).resolve().parents[6] / "prompts"
_PROMPTS_ROOT = Path(os.environ.get("KP_PROMPTS_ROOT", _DEFAULT_PROMPTS_ROOT))
_PROMPTS_DIR = _PROMPTS_ROOT / "extraction"
```

`parents[6]` anchors at the repo root from `resources.py`'s location; `KP_PROMPTS_ROOT` is used by evals + tests to point at alternate trees, not by deployments.

## Version-naming convention

`v<N>_<content_type>_<source-tag>_<YYYY_MM_DD>.md`

- `v<N>` — major version of the prompt schema
- `<content_type>` — `youtube`, `arxiv`, `article`, etc.
- `<source-tag>` — short tag identifying the working copy / experiment (e.g., `kp_copy`)
- `<YYYY_MM_DD>` — landing date

Example: `v5_youtube_kp_copy_2026_06_01.md`

Sub-prompt files used by multi-call extractors (e.g., `narrative_v1.md`, `topic_card_v1.md`, `followups_v1.md`) use a simpler `<role>_v<N>.md` form.

## Future direction

Alignment with `newsletter-assistant`'s `packages/core/src/core/prompts/` pattern (loader + assembly framework) is a deferred follow-up. For now: flat per-domain subdirs, file-based loading.
