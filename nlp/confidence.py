"""
nlp/confidence.py

Shared confidence-scoring utilities used by nlp/extraction.py and
nlp/resolution.py.

Entity-resolution confidence is built from *independent pieces of
evidence* (name similarity, a shared phone, an explicit "alias" in the
source text, shared location...). combine_evidence() merges them with a
noisy-OR: each piece of evidence is a probability that the two mentions
are the same real-world entity, and the combined probability is the
chance that at least one of them is right. It is transparent (every
input is shown to the investigator as a reason), monotonic (more
independent evidence never lowers confidence), and needs no training
data. The weights that feed it are documented and evaluated in
docs/EVALUATION.md -- they are hand-set and checked against the labelled
set in data/eval/, not learned.
"""

import math
from typing import Iterable

# Merge policy for entity resolution (see nlp/resolution.py):
#   score >= AUTO_MERGE_THRESHOLD    -> merged, no flag
#   REVIEW_THRESHOLD <= score < AUTO -> merged AND flagged for investigator review
#   score < REVIEW_THRESHOLD         -> not merged
AUTO_MERGE_THRESHOLD = 0.80
REVIEW_THRESHOLD = 0.60


def normalize_confidence(raw_score: float, method: str = "minmax") -> float:
    """Normalize a raw score into [0.0, 1.0].

    "minmax" clips a score already roughly in [0.0, 1.0]. "sigmoid"
    logistic-squashes an unbounded score (e.g. a weighted sum of several
    signals) into range.

    Raises:
        ValueError: if method is not "minmax" or "sigmoid".
    """
    if method == "minmax":
        return max(0.0, min(1.0, raw_score))
    if method == "sigmoid":
        return 1.0 / (1.0 + math.exp(-raw_score))
    raise ValueError(f"Unknown normalization method: {method!r}. Use 'minmax' or 'sigmoid'.")


def below_review_threshold(confidence: float, threshold: float = 0.6) -> bool:
    """True if confidence is below threshold (should be flagged for review)."""
    return confidence < threshold


def combine_evidence(probabilities: Iterable) -> float:
    """Noisy-OR combination of independent evidence probabilities, each
    in [0, 1]. Empty input -> 0.0. Result is clamped to [0, 1].
    """
    remaining = 1.0
    for p in probabilities:
        remaining *= 1.0 - max(0.0, min(1.0, float(p)))
    return round(1.0 - remaining, 4)


def merge_tier(score: float) -> str:
    """Classify a resolution score: "auto", "review" or "reject"."""
    if score >= AUTO_MERGE_THRESHOLD:
        return "auto"
    if score >= REVIEW_THRESHOLD:
        return "review"
    return "reject"


def needs_manual_review(score: float) -> bool:
    """True for a score in the merged-but-flagged band."""
    return merge_tier(score) == "review"
