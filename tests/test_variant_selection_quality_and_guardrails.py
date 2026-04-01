"""Variant-selection, data-quality, and guardrail recommendation tests."""

from typing import Dict

import pandas as pd
import pytest

from experimentplatform.analytics.metrics import (
    MetricsError,
    _outlier_count_iqr,
    _select_comparison_variants,
    compute_data_quality,
    detect_srm,
    evaluate_guardrails,
)
from shared.schemas import ExperimentSpec


@pytest.fixture()
def two_variant_groups() -> Dict[str, Dict[str, float]]:
    return {
        "control": {"n": 500, "mean": 0.10},
        "treatment": {"n": 480, "mean": 0.13},
    }


@pytest.fixture()
def three_variant_groups() -> Dict[str, Dict[str, float]]:
    return {
        "No E-Mail": {"n": 21307, "mean": 0.063},
        "Mens E-Mail": {"n": 21387, "mean": 0.097},
        "Womens E-Mail": {"n": 21306, "mean": 0.109},
    }


class TestSelectComparisonVariants:
    def test_auto_mode_returns_two_largest_groups(self, three_variant_groups):
        ctrl, _, treat, _, mode = _select_comparison_variants(three_variant_groups)
        assert mode == "auto"
        assert ctrl == "Mens E-Mail"
        assert treat == "No E-Mail"

    def test_explicit_mode_respects_provided_variants(self, three_variant_groups):
        ctrl, _, treat, _, mode = _select_comparison_variants(
            three_variant_groups, "No E-Mail", "Mens E-Mail"
        )
        assert mode == "explicit"
        assert ctrl == "No E-Mail"
        assert treat == "Mens E-Mail"

    def test_spec_default_mode_uses_configured_pair(self, three_variant_groups):
        ctrl, _, treat, _, mode = _select_comparison_variants(
            three_variant_groups,
            default_control_variant="No E-Mail",
            default_treatment_variant="Womens E-Mail",
        )
        assert mode == "spec_default"
        assert ctrl == "No E-Mail"
        assert treat == "Womens E-Mail"

    def test_explicit_overrides_spec_default(self, three_variant_groups):
        ctrl, _, treat, _, mode = _select_comparison_variants(
            three_variant_groups,
            control_variant="No E-Mail",
            treatment_variant="Mens E-Mail",
            default_control_variant="No E-Mail",
            default_treatment_variant="Womens E-Mail",
        )
        assert mode == "explicit"
        assert ctrl == "No E-Mail"
        assert treat == "Mens E-Mail"

    def test_explicit_mode_returns_correct_data_dicts(self, three_variant_groups):
        _, ctrl_data, _, treat_data, _ = _select_comparison_variants(
            three_variant_groups, "No E-Mail", "Mens E-Mail"
        )
        assert ctrl_data == three_variant_groups["No E-Mail"]
        assert treat_data == three_variant_groups["Mens E-Mail"]

    def test_only_control_without_treatment_raises(self, two_variant_groups):
        with pytest.raises(MetricsError, match="must be provided together"):
            _select_comparison_variants(two_variant_groups, control_variant="control")

    def test_only_treatment_without_control_raises(self, two_variant_groups):
        with pytest.raises(MetricsError, match="must be provided together"):
            _select_comparison_variants(two_variant_groups, treatment_variant="treatment")

    def test_same_variant_for_both_raises(self, two_variant_groups):
        with pytest.raises(MetricsError, match="must differ"):
            _select_comparison_variants(two_variant_groups, "control", "control")

    def test_nonexistent_variant_raises(self, two_variant_groups):
        with pytest.raises(MetricsError, match="Explicit variants not found"):
            _select_comparison_variants(two_variant_groups, "ghost_control", "treatment")

    def test_only_default_control_without_default_treatment_raises(self, two_variant_groups):
        with pytest.raises(
            MetricsError, match="default_control_variant and default_treatment_variant must be provided together"
        ):
            _select_comparison_variants(
                two_variant_groups,
                default_control_variant="control",
            )

    def test_default_variants_not_found_raises(self, two_variant_groups):
        with pytest.raises(MetricsError, match="ExperimentSpec default variants not found"):
            _select_comparison_variants(
                two_variant_groups,
                default_control_variant="ghost_control",
                default_treatment_variant="treatment",
            )

    def test_auto_mode_with_two_variants(self, two_variant_groups):
        ctrl, _, treat, _, mode = _select_comparison_variants(two_variant_groups)
        assert mode == "auto"
        assert ctrl == "control"
        assert treat == "treatment"


class TestDetectSrm:
    def test_equal_split_no_srm(self):
        result = detect_srm({"control": 1000, "treatment": 1000})
        assert result["srm_flag"] is False
        assert result["srm_p_value"] > 0.01

    def test_severely_unequal_split_flags_srm(self):
        result = detect_srm({"control": 9000, "treatment": 1000})
        assert result["srm_flag"] is True
        assert result["srm_p_value"] < 0.001

    def test_custom_expected_split_used(self):
        result = detect_srm(
            {"control": 600, "treatment": 400},
            expected_split={"control": 0.6, "treatment": 0.4},
        )
        assert result["srm_flag"] is False

    def test_result_contains_required_keys(self):
        result = detect_srm({"control": 500, "treatment": 500})
        required = {
            "srm_flag",
            "srm_p_value",
            "expected_split",
            "observed_split",
            "chi_square",
            "degrees_of_freedom",
        }
        assert required.issubset(result.keys())

    def test_zero_total_returns_safe_defaults(self):
        result = detect_srm({"control": 0, "treatment": 0})
        assert result["srm_flag"] is False
        assert result["srm_p_value"] == 1.0
        assert result["chi_square"] == 0.0

    def test_observed_split_sums_to_one(self):
        result = detect_srm({"control": 600, "treatment": 400})
        total = sum(result["observed_split"].values())
        assert abs(total - 1.0) < 1e-9

    def test_expected_split_sums_to_one(self):
        result = detect_srm({"control": 600, "treatment": 400})
        total = sum(result["expected_split"].values())
        assert abs(total - 1.0) < 1e-9

    def test_three_variants_equal_split(self):
        result = detect_srm({"A": 333, "B": 333, "C": 334})
        assert result["srm_flag"] is False
        assert result["degrees_of_freedom"] == 2

    def test_chi_square_nonneg(self):
        result = detect_srm({"control": 700, "treatment": 300})
        assert result["chi_square"] >= 0.0

    @pytest.mark.parametrize(
        "counts,should_flag",
        [
            ({"c": 500, "t": 500}, False),
            ({"c": 950, "t": 50}, True),
            ({"c": 510, "t": 490}, False),
        ],
    )
    def test_srm_boundary_cases(self, counts, should_flag):
        result = detect_srm(counts)
        assert result["srm_flag"] == should_flag


class TestComputeDataQuality:
    def _make_dfs(self, n_control=50, n_treatment=50, missing_frac=0.0):
        n = n_control + n_treatment
        post_vals = [0.0] * n
        if missing_frac > 0:
            n_missing = int(n * missing_frac)
            post_vals[:n_missing] = [None] * n_missing
        user_df = pd.DataFrame(
            {
                "user_id": [f"u{i}" for i in range(n)],
                "variant": ["control"] * n_control + ["treatment"] * n_treatment,
                "post_metric": post_vals,
            }
        )
        event_df = pd.DataFrame(
            {
                "user_id": [f"u{i}" for i in range(n)],
                "variant": ["control"] * n_control + ["treatment"] * n_treatment,
                "event_name": ["visit"] * n,
                "value": [None] * n,
            }
        )
        return event_df, user_df

    def test_missing_rate_reflects_null_fraction(self):
        event_df, user_df = self._make_dfs(missing_frac=0.25)
        result = compute_data_quality(event_df, user_df, "conversion_rate", "post_metric")
        assert abs(result["missing_rate"] - 0.25) < 0.01

    def test_returns_required_keys(self):
        event_df, user_df = self._make_dfs()
        result = compute_data_quality(event_df, user_df, "conversion_rate", "post_metric")
        for key in (
            "srm_flag",
            "srm_p_value",
            "missing_rate",
            "duplicate_rate",
            "outlier_counts",
        ):
            assert key in result

    def test_zero_missing_when_no_nulls(self):
        event_df, user_df = self._make_dfs()
        result = compute_data_quality(event_df, user_df, "conversion_rate", "post_metric")
        assert result["missing_rate"] == 0.0

    def test_zero_duplicate_when_all_unique(self):
        event_df, user_df = self._make_dfs()
        result = compute_data_quality(event_df, user_df, "conversion_rate", "post_metric")
        assert result["duplicate_rate"] == 0.0

    def test_duplicate_rate_detected(self):
        event_df = pd.DataFrame(
            {
                "user_id": ["u1", "u1", "u2", "u2"],
                "variant": ["control", "control", "treatment", "treatment"],
                "event_name": ["visit"] * 4,
                "value": [None] * 4,
            }
        )
        user_df = pd.DataFrame(
            {
                "user_id": ["u1", "u2"],
                "variant": ["control", "treatment"],
                "post_metric": [0.0, 1.0],
            }
        )
        result = compute_data_quality(event_df, user_df, "conversion_rate", "post_metric")
        assert result["duplicate_rate"] > 0.0

    def test_outlier_counts_keyed_by_metric(self):
        event_df, user_df = self._make_dfs()
        result = compute_data_quality(event_df, user_df, "conversion_rate", "post_metric")
        assert "conversion_rate" in result["outlier_counts"]

    def test_srm_propagated_from_variant_counts(self):
        event_df, user_df = self._make_dfs(n_control=900, n_treatment=100)
        result = compute_data_quality(event_df, user_df, "conversion_rate", "post_metric")
        assert result["srm_flag"] is True


class TestOutlierCountIqr:
    def test_no_outliers_in_uniform_series(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        assert _outlier_count_iqr(s) == 0

    def test_extreme_value_counted_as_outlier(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 1000.0])
        assert _outlier_count_iqr(s) >= 1

    def test_fewer_than_four_obs_returns_zero(self):
        for n in range(1, 4):
            assert _outlier_count_iqr(pd.Series(list(range(n)))) == 0

    def test_zero_iqr_series_returns_zero(self):
        s = pd.Series([5.0, 5.0, 5.0, 5.0, 5.0])
        assert _outlier_count_iqr(s) == 0


class TestEvaluateGuardrails:
    def _primary(self, n_control=500, n_treatment=500, diff=0.03, significant=True):
        return {
            "control": {"n": n_control, "mean": 0.10},
            "treatment": {"n": n_treatment, "mean": 0.10 + diff},
            "diff": diff,
            "diff_pct": diff / 0.10 * 100,
            "significant": significant,
        }

    def _dq_clean(self):
        return {"srm_flag": False, "missing_rate": 0.01}

    def _dq_srm(self):
        return {"srm_flag": True, "missing_rate": 0.01}

    def _spec(self, min_sample_size=100):
        return ExperimentSpec(
            experiment_id="test",
            owner="ds@example.com",
            hypothesis="h",
            primary_metric="conversion_rate",
            min_run_time_days=7,
            min_sample_size=min_sample_size,
        )

    def test_ship_when_primary_significant_positive_no_failures(self):
        result = evaluate_guardrails(
            primary_result=self._primary(significant=True),
            primary_metric="conversion_rate",
            guardrail_results=[{"metric": "revenue_per_user", "status": "pass"}],
            experiment_spec=self._spec(),
            data_quality=self._dq_clean(),
        )
        assert result["recommendation"] == "ship"

    def test_ship_sets_primary_positive_significant_true(self):
        result = evaluate_guardrails(
            primary_result=self._primary(significant=True),
            primary_metric="conversion_rate",
            guardrail_results=[],
            experiment_spec=None,
            data_quality=self._dq_clean(),
        )
        assert result["primary_positive_significant"] is True

    def test_hold_when_primary_not_significant(self):
        result = evaluate_guardrails(
            primary_result=self._primary(significant=False),
            primary_metric="conversion_rate",
            guardrail_results=[],
            experiment_spec=None,
            data_quality=self._dq_clean(),
        )
        assert result["recommendation"] == "hold"

    def test_hold_when_below_min_sample_size(self):
        result = evaluate_guardrails(
            primary_result=self._primary(n_control=10, n_treatment=10, significant=True),
            primary_metric="conversion_rate",
            guardrail_results=[],
            experiment_spec=self._spec(min_sample_size=10000),
            data_quality=self._dq_clean(),
        )
        assert result["recommendation"] == "hold"
        assert result["meets_min_sample_size"] is False

    def test_hold_includes_below_sample_size_rationale(self):
        result = evaluate_guardrails(
            primary_result=self._primary(n_control=5, n_treatment=5, significant=True),
            primary_metric="conversion_rate",
            guardrail_results=[],
            experiment_spec=self._spec(min_sample_size=10000),
            data_quality=self._dq_clean(),
        )
        assert any("minimum sample size" in r for r in result["rationale"])

    def test_hold_when_high_missing_rate(self):
        result = evaluate_guardrails(
            primary_result=self._primary(significant=True),
            primary_metric="conversion_rate",
            guardrail_results=[],
            experiment_spec=None,
            data_quality={"srm_flag": False, "missing_rate": 0.30},
        )
        assert result["recommendation"] == "hold"
        assert any("missing" in r.lower() for r in result["rationale"])

    def test_stop_when_srm_detected(self):
        result = evaluate_guardrails(
            primary_result=self._primary(significant=True),
            primary_metric="conversion_rate",
            guardrail_results=[],
            experiment_spec=None,
            data_quality=self._dq_srm(),
        )
        assert result["recommendation"] == "stop"

    def test_stop_when_guardrail_fails(self):
        result = evaluate_guardrails(
            primary_result=self._primary(significant=True),
            primary_metric="conversion_rate",
            guardrail_results=[{"metric": "revenue_per_user", "status": "fail"}],
            experiment_spec=None,
            data_quality=self._dq_clean(),
        )
        assert result["recommendation"] == "stop"

    def test_stop_when_both_srm_and_guardrail_fail(self):
        result = evaluate_guardrails(
            primary_result=self._primary(significant=True),
            primary_metric="conversion_rate",
            guardrail_results=[{"metric": "revenue_per_user", "status": "fail"}],
            experiment_spec=None,
            data_quality=self._dq_srm(),
        )
        assert result["recommendation"] == "stop"

    def test_negative_diff_in_lower_is_better_metric_is_positive_sig(self):
        result = evaluate_guardrails(
            primary_result=self._primary(diff=-0.05, significant=True),
            primary_metric="readmission_30d_rate",
            guardrail_results=[],
            experiment_spec=None,
            data_quality=self._dq_clean(),
        )
        assert result["primary_positive_significant"] is True

    def test_positive_diff_in_lower_is_better_metric_not_positive(self):
        result = evaluate_guardrails(
            primary_result=self._primary(diff=0.05, significant=True),
            primary_metric="readmission_30d_rate",
            guardrail_results=[],
            experiment_spec=None,
            data_quality=self._dq_clean(),
        )
        assert result["primary_positive_significant"] is False

    def test_result_contains_required_keys(self):
        result = evaluate_guardrails(
            primary_result=self._primary(),
            primary_metric="conversion_rate",
            guardrail_results=[],
            experiment_spec=None,
            data_quality=self._dq_clean(),
        )
        for key in (
            "recommendation",
            "rationale",
            "primary_positive_significant",
            "meets_min_sample_size",
        ):
            assert key in result

    def test_recommendation_is_one_of_valid_values(self):
        for sig, srm in [(True, False), (False, False), (True, True)]:
            result = evaluate_guardrails(
                primary_result=self._primary(significant=sig),
                primary_metric="conversion_rate",
                guardrail_results=[],
                experiment_spec=None,
                data_quality={"srm_flag": srm, "missing_rate": 0.0},
            )
            assert result["recommendation"] in {"ship", "hold", "stop"}
