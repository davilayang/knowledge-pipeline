"""Variant + RetrievalVariant primitives, identity hashing, corpus signature."""

import hashlib
import json

from evals.core.types import VariantProvenance
from evals.core.variants import (
    RetrievalVariant,
    Variant,
    corpus_signature,
    variant_identity,
)


def _provenance(code_revision: str = "abc1234") -> VariantProvenance:
    return VariantProvenance(
        prompt_versions={"extraction.youtube": "v5_2026_06_01"},
        model_versions={"extraction": "gpt-4o-mini"},
        code_revision=code_revision,
        corpus_anchor="backup_readings/raw_store/2026-05-30",
        output_schema_version=1,
    )


def _no_op_runner(_fixture):
    raise NotImplementedError


def test_variant_identity_is_deterministic():
    v = Variant(
        name="v5_baseline",
        config={"prompt_label": "v5_kp_copy_2026_06_01"},
        provenance=_provenance(),
        run=_no_op_runner,
    )
    assert variant_identity(v) == variant_identity(v)


def test_variant_identity_ignores_name_and_runner():
    """Display name and the callable identity must NOT influence the hash."""
    v1 = Variant(name="A", config={"x": 1}, provenance=_provenance(), run=_no_op_runner)
    v2 = Variant(name="B", config={"x": 1}, provenance=_provenance(), run=lambda f: None)
    assert variant_identity(v1) == variant_identity(v2)


def test_variant_identity_changes_with_config():
    v1 = Variant(name="A", config={"x": 1}, provenance=_provenance(), run=_no_op_runner)
    v2 = Variant(name="A", config={"x": 2}, provenance=_provenance(), run=_no_op_runner)
    assert variant_identity(v1) != variant_identity(v2)


def test_variant_identity_changes_with_provenance():
    v1 = Variant(name="A", config={"x": 1}, provenance=_provenance("rev_a"), run=_no_op_runner)
    v2 = Variant(name="A", config={"x": 1}, provenance=_provenance("rev_b"), run=_no_op_runner)
    assert variant_identity(v1) != variant_identity(v2)


def test_corpus_signature_is_content_id_sorted_sha():
    ids = ["c", "a", "b"]
    expected = hashlib.sha256(json.dumps(sorted(ids), sort_keys=True).encode()).hexdigest()
    assert corpus_signature(ids) == expected


def test_corpus_signature_order_independent():
    assert corpus_signature(["a", "b", "c"]) == corpus_signature(["c", "b", "a"])


def test_retrieval_variant_has_setup_and_query_callables():
    def _setup(_corpus):
        return object()

    def _query(_index, _q):
        return []

    rv = RetrievalVariant(
        name="bge-small",
        config={"model": "bge-small", "dim": 384},
        provenance=_provenance(),
        setup=_setup,
        query=_query,
    )
    assert rv.config["model"] == "bge-small"
