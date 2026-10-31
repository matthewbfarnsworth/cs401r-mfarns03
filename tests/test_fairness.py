"""
NorthStar Retail — Fairness Check
CS 401R Lab 4

A manual slice evaluation of recall by loyalty tier. A gap above the threshold
FLAGS (a warning in the build log, for the human approver) but does not block.
Missing or malformed slice results DO fail: a check that cannot run is not a
check that passed.

Run locally:
    pytest tests/test_fairness.py -v -rw --eval-metrics-path /tmp/out/evaluation_metrics.json
"""

import json
import os
import warnings
from pathlib import Path

import pytest

HIGHEST_TIER = "Platinum"
LOWEST_TIER = "Bronze"
ALL_TIERS = {"Bronze", "Silver", "Gold", "Platinum"}
MAX_RECALL_GAP = 0.10


def flag_recall_gap(slices, threshold=MAX_RECALL_GAP):
    """Return the highest-to-lowest tier recall gap, warning if it is too wide."""
    by_tier = {row["tier"]: row for row in slices}
    high = by_tier[HIGHEST_TIER]["recall"]
    low = by_tier[LOWEST_TIER]["recall"]
    gap = abs(high - low)
    if gap > threshold:
        warnings.warn(
            f"FAIRNESS FLAG: recall gap {gap:.1%} between {HIGHEST_TIER} ({high:.1%}) "
            f"and {LOWEST_TIER} ({low:.1%}) exceeds {threshold:.0%}. "
            f"Review before approving."
        )
    return gap


@pytest.fixture(scope="module")
def slices():
    path = os.environ.get("EVAL_METRICS_PATH", "/tmp/eval_metrics.json")
    if not Path(path).exists():
        message = f"Evaluation metrics not found at {path}."
        if os.environ.get("ALLOW_MISSING_DATA") == "1":
            pytest.skip(message)
        pytest.fail(message, pytrace=False)
    with open(path) as f:
        return json.load(f).get("slices")


def test_every_tier_is_reported(slices):
    assert slices, "evaluation_metrics.json has no slices. train() must add them."
    assert {row["tier"] for row in slices} == ALL_TIERS


def test_tier_recalls_are_valid(slices):
    for row in slices:
        assert 0 <= row["recall"] <= 1, f"{row['tier']} recall out of range: {row['recall']}"


def test_recall_gap_is_reported(slices):
    """Flags, never blocks. Expect the warning on this data."""
    gap = flag_recall_gap(slices)
    print(f"Recall gap {HIGHEST_TIER} vs {LOWEST_TIER}: {gap:.1%}")
    assert gap >= 0


def test_wide_gap_raises_the_flag():
    wide = [{"tier": "Platinum", "recall": 0.10}, {"tier": "Bronze", "recall": 0.45}]
    with pytest.warns(UserWarning, match="FAIRNESS FLAG"):
        assert flag_recall_gap(wide) == pytest.approx(0.35)


def test_narrow_gap_stays_quiet():
    narrow = [{"tier": "Platinum", "recall": 0.30}, {"tier": "Bronze", "recall": 0.35}]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert flag_recall_gap(narrow) == pytest.approx(0.05)