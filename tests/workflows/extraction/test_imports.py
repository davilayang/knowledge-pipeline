"""Smoke test: workflows.extraction exports the public API cleanly.

If this fails, the workflows.extraction __init__.py is broken or the
underlying modules don't import.
"""


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
    import inspect

    from workflows.extraction import SingleShotOpenAIExtractor

    sig = inspect.signature(SingleShotOpenAIExtractor.__init__)
    assert "prompt_text" in sig.parameters, (
        f"Expected 'prompt_text' kwarg in {sig}; "
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
        "import workflows.extraction.openai_single_shot\n"
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
