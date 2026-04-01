"""Segmentation and statistics-focused tests for experimentation metrics."""

from typing import Any, Dict, List

import pandas as pd
import pytest

from experimentplatform.analytics.metrics import (
    MetricsError,
    _contrast_stats,
    _validate_segment_by,
    apply_bh_fdr,
    compute_segment_p_values,
)


@pytest.fixture()
def simple_user_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": list(range(200)),
            "variant": ["control"] * 100 + ["treatment"] * 100,
            "region": ["NA"] * 50 + ["EU"] * 50 + ["NA"] * 50 + ["EU"] * 50,
            "post_metric": [0] * 40 + [1] * 10 + [0] * 45 + [1] * 5
            + [0] * 35 + [1] * 15 + [0] * 40 + [1] * 10,
        }
    )


class TestValidateSegmentBy:
    def test_valid_columns_returned(self):
        assert _validate_segment_by(["region", "device"]) == ["region", "device"]

    def test_empty_input_returns_empty_list(self):
        assert _validate_segment_by(None) == []
        assert _validate_segment_by([]) == []

    def test_unsupported_column_raises(self):
        with pytest.raises(MetricsError, match="Unsupported segment_by columns"):
            _validate_segment_by(["region", "bad_column"])

    def test_all_valid_segment_columns_accepted(self):
        for col in ["region", "device", "age_bucket", "channel", "risk_segment"]:
            assert _validate_segment_by([col]) == [col]


class TestComputeSegmentPValues:
    def test_returns_one_row_per_segment(self, simple_user_df):
        rows = compute_segment_p_values(
            simple_user_df, "post_metric", ["region"], "control", "treatment"
        )
        assert len(rows) == 2
        assert {r["segment"]["region"] for r in rows} == {"NA", "EU"}

    def test_row_contains_required_keys(self, simple_user_df):
        rows = compute_segment_p_values(
            simple_user_df, "post_metric", ["region"], "control", "treatment"
        )
        required = {
            "segment",
            "control_n",
            "treatment_n",
            "diff",
            "p_value",
            "ci_95",
            "significant",
        }
        for row in rows:
            assert required.issubset(row.keys())

    def test_p_values_are_valid_probabilities(self, simple_user_df):
        rows = compute_segment_p_values(
            simple_user_df, "post_metric", ["region"], "control", "treatment"
        )
        for row in rows:
            assert 0.0 <= row["p_value"] <= 1.0

    def test_empty_segment_by_returns_empty(self, simple_user_df):
        assert (
            compute_segment_p_values(simple_user_df, "post_metric", [], "control", "treatment")
            == []
        )

    def test_segment_with_too_few_obs_is_skipped(self):
        df = pd.DataFrame(
            {
                "user_id": [1, 2, 3],
                "variant": ["control", "treatment", "control"],
                "region": ["tiny", "tiny", "tiny"],
                "post_metric": [0, 1, 0],
            }
        )
        assert compute_segment_p_values(df, "post_metric", ["region"], "control", "treatment") == []

    def test_ci_low_leq_ci_high(self, simple_user_df):
        rows = compute_segment_p_values(
            simple_user_df, "post_metric", ["region"], "control", "treatment"
        )
        for row in rows:
            assert row["ci_95"]["low"] <= row["ci_95"]["high"]


class TestApplyBhFdr:
    def _make_rows(self, p_values: List[float]) -> List[Dict[str, Any]]:
        return [
            {
                "segment": {"region": f"seg_{i}"},
                "control_n": 100,
                "treatment_n": 100,
                "diff": 0.05,
                "p_value": p,
                "ci_95": {"low": 0.0, "high": 0.1},
                "significant": p < 0.05,
            }
            for i, p in enumerate(p_values)
        ]

    def test_empty_input_returns_empty(self):
        assert apply_bh_fdr([]) == []

    def test_bh_fields_added(self):
        rows = self._make_rows([0.001, 0.03, 0.4])
        result = apply_bh_fdr(rows)
        for r in result:
            assert "bh_fdr_significant" in r
            assert "bh_q_value" in r

    def test_clearly_significant_p_values_pass_bh(self):
        rows = self._make_rows([0.001, 0.002, 0.003])
        result = apply_bh_fdr(rows)
        assert all(r["bh_fdr_significant"] for r in result)

    def test_clearly_non_significant_fails_bh(self):
        rows = self._make_rows([0.8, 0.9, 0.95])
        result = apply_bh_fdr(rows)
        assert not any(r["bh_fdr_significant"] for r in result)

    def test_bh_more_conservative_than_naive_for_mixed_pvalues(self):
        rows = self._make_rows([0.001, 0.03, 0.4])
        result = apply_bh_fdr(rows)
        by_p = {r["p_value"]: r for r in result}
        assert by_p[0.001]["bh_fdr_significant"]
        assert not by_p[0.4]["bh_fdr_significant"]

    def test_q_values_are_valid(self):
        rows = self._make_rows([0.01, 0.04, 0.3])
        result = apply_bh_fdr(rows)
        for r in result:
            assert 0.0 <= r["bh_q_value"] <= 1.0

    def test_single_row(self):
        rows = self._make_rows([0.01])
        result = apply_bh_fdr(rows)
        assert len(result) == 1
        assert "bh_fdr_significant" in result[0]

    @pytest.mark.parametrize(
        "p_values,expected_sig_count",
        [
            ([0.001, 0.002, 0.003], 3),
            ([0.8, 0.9, 0.95], 0),
            ([0.001, 0.5, 0.9], 1),
        ],
    )
    def test_known_bh_outcomes(self, p_values, expected_sig_count):
        rows = self._make_rows(p_values)
        result = apply_bh_fdr(rows)
        assert sum(1 for r in result if r["bh_fdr_significant"]) == expected_sig_count


class TestContrastStats:
    def test_known_diff(self):
        c = pd.Series([0.0] * 500 + [1.0] * 500)
        t = pd.Series([0.0] * 400 + [1.0] * 600)
        stats = _contrast_stats(c, t)
        assert abs(stats["diff"] - 0.1) < 1e-9

    def test_zero_se_when_single_obs(self):
        c = pd.Series([0.5])
        t = pd.Series([0.6])
        stats = _contrast_stats(c, t)
        assert stats["p_value"] == 1.0
        assert stats["standard_error"] == 0.0

    def test_significant_for_large_clear_difference(self):
        c = pd.Series([0.0] * 500 + [1.0] * 500)
        t = pd.Series([0.0] * 400 + [1.0] * 600)
        stats = _contrast_stats(c, t)
        assert stats["significant"] is True
        assert stats["p_value"] < 0.05

    def test_not_significant_for_tiny_difference(self):
        c = pd.Series([0.0] * 50 + [1.0] * 50)
        t = pd.Series([0.0] * 49 + [1.0] * 51)
        stats = _contrast_stats(c, t)
        assert stats["significant"] is False

    def test_diff_pct_relative_to_control(self):
        c = pd.Series([1.0] * 100)
        t = pd.Series([1.1] * 100)
        stats = _contrast_stats(c, t)
        assert abs(stats["diff_pct"] - 10.0) < 1e-6
