"""Smoke test: workflows.extraction exports the public API cleanly.

If this fails, the workflows.extraction __init__.py is broken or the
underlying modules don't import.
"""


def test_public_api_importable():
    from workflows.extraction import (
        ExtractionUsage,
        ExtractorProtocol,
        ThreeCallOpenAIExtractor,
    )

    # Verify symbol identity (not just successful import)
    assert ThreeCallOpenAIExtractor.__name__ == "ThreeCallOpenAIExtractor"
    assert ExtractorProtocol.__name__ == "ExtractorProtocol"
    assert ExtractionUsage.__name__ == "ExtractionUsage"


def test_three_call_accepts_per_role_prompts():
    """Contract: ThreeCallOpenAIExtractor.__init__ takes per-role *_prompt: str
    arguments directly. No file/label resolution inside the extractor — that's
    an orchestration concern.
    """
    import inspect

    from workflows.extraction import ThreeCallOpenAIExtractor

    sig = inspect.signature(ThreeCallOpenAIExtractor.__init__)
    for kwarg in ("narrative_prompt", "topic_card_prompt", "followups_prompt"):
        assert kwarg in sig.parameters, (
            f"Expected '{kwarg}' kwarg in {sig}; "
            "extractor must accept prompt content as a string, not a label."
        )


def test_no_dagster_import_in_workflows_extraction():
    """workflows is forbidden from importing dagster (architectural rule).

    Runs in a fresh subprocess so the check isn't polluted by other tests in
    the same pytest run that legitimately import dagster (e.g. orchestrators
    tests).
    """
    import subprocess
    import sys

    probe = (
        "import workflows.extraction\n"
        "import workflows.extraction.three_call_openai\n"
        "import workflows.extraction.protocol\n"
        "import sys\n"
        "leaked = sorted(n for n in sys.modules if 'dagster' in n.lower())\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "workflows.extraction transitively imports dagster:\n" f"stderr:\n{result.stderr}"
    )
