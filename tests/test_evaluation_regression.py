"""
Guards the quality numbers reported in docs/EVALUATION.md. Thresholds sit
slightly BELOW the measured values so unrelated changes don't flake, but a
real regression in extraction or resolution fails loudly.
"""

import pytest

from scripts.evaluate import evaluate_set, load_generated, load_labelled


@pytest.fixture(scope="module")
def labelled():
    return evaluate_set(load_labelled())


def test_labelled_extraction_quality(labelled):
    micro = labelled["extraction"]["MICRO"]
    assert micro["precision"] >= 0.95 and micro["recall"] >= 0.95
    for t in ("PERSON", "PHONE", "VEHICLE"):
        assert labelled["extraction"][t]["f1"] >= 0.95, t


def test_labelled_resolution_quality(labelled):
    r = labelled["resolution"]
    assert r["precision"] >= 0.97 and r["recall"] >= 0.9
    assert r["false_merged_unflagged"] == 0


def test_labelled_relation_quality(labelled):
    assert labelled["relations"]["precision"] >= 0.9 and labelled["relations"]["recall"] >= 0.9


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_generated_rings_never_produce_a_silent_false_merge(seed):
    r = evaluate_set(load_generated(seed))
    assert r["resolution"]["false_merged_unflagged"] == 0
    assert r["resolution"]["precision"] >= 0.95
    assert r["extraction"]["MICRO"]["f1"] >= 0.93
