# `prompts/` — Versioned prompt assets

Markdown prompt bodies consumed by the workflow layer. Treated as **content artefacts**, not code — versioned in git, reviewable in PRs, decoupled from package boundaries.

## Layout

````
prompts/
  extraction/     # consumed by workflows.extraction (SingleShotOpenAIExtractor, ThreeCallOpenAIExtractor)
  # future: wiki_synthesis/, knowledge_graph/, etc.
````

## Resolution

Consumers resolve the prompts root via the `KP_PROMPTS_ROOT` env var, defaulting to the repo-root `prompts/` directory. The orchestrator's `ExtractorRegistry` adapter uses:

```python
PROMPTS_ROOT = Path(os.environ.get("KP_PROMPTS_ROOT", DEFAULT_PROMPTS_ROOT))
_PROMPTS_DIR = PROMPTS_ROOT / "extraction"
```

Where `DEFAULT_PROMPTS_ROOT` is computed from a known relative path anchor inside the orchestrators package.

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
