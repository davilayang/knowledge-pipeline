"""Public-API smoke: every advertised name is re-importable from evals.core
and evals.core.judges."""


def test_top_level_imports():
    import evals.core as m

    expected = {
        "BudgetExceededError",
        "CostBudget",
        "CostEstimatorProtocol",
        "DiffReport",
        "FieldScore",
        "FixtureHeader",
        "FixtureRef",
        "FixtureRun",
        "RetrievalVariant",
        "RunRecord",
        "RunStatus",
        "SchemaVersionMismatch",
        "ScoreReport",
        "StageTrace",
        "Variant",
        "VariantProvenance",
        "corpus_signature",
        "diff_runs",
        "load_fixtures",
        "load_run",
        "render_diff_html",
        "render_diff_text",
        "run_dir",
        "save_fixtures",
        "save_run",
        "snapshot",
        "variant_identity",
    }
    assert expected <= set(dir(m))


def test_judges_imports():
    from evals.core.judges import (
        EmbeddingSimilarityJudge,
        ExactMatchJudge,
        JudgeProtocol,
        LLMJudge,
    )

    for cls in (EmbeddingSimilarityJudge, ExactMatchJudge, LLMJudge):
        assert hasattr(cls, "score")
    assert JudgeProtocol is not None
