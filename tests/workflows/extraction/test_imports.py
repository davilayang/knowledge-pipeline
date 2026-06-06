"""Smoke test: workflows.extraction exports the public API cleanly.

If this fails, the workflows.extraction __init__.py is broken or the
underlying modules don't import.
"""

import pytest


def test_public_api_importable():
    from workflows.extraction import (
        ExtractionUsage,
        ExtractorProtocol,
        SingleShotOpenAIExtractor,
        ThreeCallOpenAIExtractor,
    )

    # Verify symbol identity (not just successful import)
    assert SingleShotOpenAIExtractor.__name__ == "SingleShotOpenAIExtractor"
    assert ThreeCallOpenAIExtractor.__name__ == "ThreeCallOpenAIExtractor"
    assert ExtractorProtocol.__name__ == "ExtractorProtocol"
    assert ExtractionUsage.__name__ == "ExtractionUsage"


def test_single_shot_accepts_prompt_text():
    """Contract: SingleShotOpenAIExtractor.__init__ takes prompt_text: str directly.
    No file/label resolution inside the extractor — that's an orchestration concern.
    """
    from workflows.extraction import SingleShotOpenAIExtractor
    import inspect

    sig = inspect.signature(SingleShotOpenAIExtractor.__init__)
    assert "prompt_text" in sig.parameters, (
        f"Expected 'prompt_text' kwarg in {sig}; "
        "extractor must accept prompt content as a string, not a label."
    )


def test_no_dagster_import_in_workflows_extraction():
    """workflows is forbidden from importing dagster (architectural rule)."""
    import workflows.extraction
    import workflows.extraction.openai_single_shot
    import workflows.extraction.three_call_openai
    import workflows.extraction.protocol

    import sys
    loaded = {name for name in sys.modules if "dagster" in name.lower()}
    assert not loaded, (
        f"workflows.extraction imports pulled in dagster modules: {loaded}. "
        "workflows must not depend on dagster."
    )
