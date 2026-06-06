"""Variant + RetrievalVariant primitives, identity hashing, corpus signature."""

import hashlib
import json
from pathlib import Path

import pytest
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


def test_variant_identity_matches_pinned_hash():
    """Cross-process determinism: a known input pins a known sha256.

    Regenerate this hex ONLY after confirming the new hash is identical across
    at least three separate process invocations under PYTHONHASHSEED=random.
    Drift here means the cache contract just broke.
    """
    v = Variant(
        name="pinned",
        config={"prompt": "v5", "max_tokens": 2048},
        provenance=VariantProvenance(
            prompt_versions={"extraction": "v5_2026_06_01"},
            model_versions={"extraction": "gpt-4o-mini"},
            code_revision="abc1234",
            corpus_anchor="backup/raw_store/2026-05-30",
            output_schema_version=1,
        ),
        run=_no_op_runner,
    )
    assert variant_identity(v) == "c1149c2528051709ffcad5f48d0c3a3a4a89eca3aab14cbea4908e19736576a1"


def test_variant_identity_rejects_set_in_config():
    """sets iterate in hash-randomised order; reject up-front."""
    v = Variant(
        name="A",
        config={"selected_fields": {"a", "b"}},
        provenance=_provenance(),
        run=_no_op_runner,
    )
    with pytest.raises(ValueError, match="set"):
        variant_identity(v)


def test_variant_identity_rejects_path_in_config():
    """Path objects need ad-hoc stringification; reject up-front."""
    v = Variant(
        name="A",
        config={"prompt_path": Path("/tmp/x")},
        provenance=_provenance(),
        run=_no_op_runner,
    )
    with pytest.raises(ValueError, match="Path"):
        variant_identity(v)


def test_variant_identity_rejects_non_string_dict_key():
    """JSON requires str keys; reject up-front."""
    v = Variant(
        name="A",
        config={1: "x"},
        provenance=_provenance(),
        run=_no_op_runner,
    )
    with pytest.raises(ValueError, match="str"):
        variant_identity(v)


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
