"""Offline checks for sprint/stats.py helpers (no API key, no runs needed)."""

import sys
from pathlib import Path

import numpy as np
from scipy import stats as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sprint"))
import stats as sprint_stats  # noqa: E402


def test_holm_matches_textbook_example():
    p = {"a": 0.01, "b": 0.04, "c": 0.03}
    out = sprint_stats.holm(p)
    assert np.isclose(out["a"], 0.03)
    assert np.isclose(out["c"], 0.06)
    assert np.isclose(out["b"], 0.06)  # monotone: never below the previous adjusted value


def test_exact_perm_floor_and_separation():
    x, y = np.array([1.0, 2, 3, 4, 5]), np.array([6.0, 7, 8, 9, 10])
    p, floor = sprint_stats.exact_perm(x, y)
    assert np.isclose(floor, 2 / 252)
    assert np.isclose(p, 2 / 252)  # complete separation gives the smallest two-sided p


def test_welch_matches_scipy():
    rng = np.random.default_rng(0)
    x, y = rng.normal(0, 1, 5), rng.normal(1, 2, 5)
    diff, lo, hi, t, df, p = sprint_stats.welch(x, y)
    ref = st.ttest_ind(x, y, equal_var=False)
    assert np.isclose(t, ref.statistic) and np.isclose(p, ref.pvalue)
    assert lo < diff < hi


def test_ols_hc3_recovers_coefficients():
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(40), rng.integers(0, 2, 40)]).astype(float)
    y = X @ np.array([0.5, -0.2]) + rng.normal(0, 0.01, 40)
    beta, se, df = sprint_stats.ols_hc3(y, X)
    assert np.allclose(beta, [0.5, -0.2], atol=0.01)
    assert df == 38 and np.all(se > 0)
