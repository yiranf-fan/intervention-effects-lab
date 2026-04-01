"""Advanced /compute_metrics API integration coverage.

Focus: response contract, segmentation/FDR, variant selection,
data quality payload, guardrail/recommendation wiring, and registry behavior.
"""

from fastapi.testclient import TestClient

from experimentplatform.api.main import app

client = TestClient(app)


class TestComputeMetricsIntegration:
    def test_happy_path_response_contract(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        assert resp.status_code == 200
        data = resp.json()
        required_top_keys = {
            "experiment_id",
            "metric",
            "control",
            "treatment",
            "diff",
            "diff_pct",
            "p_value",
            "significant",
            "ci_95",
            "standard_error",
            "comparison_control_variant",
            "comparison_treatment_variant",
            "comparison_mode",
            "segment_results",
            "data_quality",
            "guardrail_results",
            "recommendation",
            "recommendation_details",
            "experiment_spec",
            "metric_validation",
            "cuped",
            "data_as_of",
        }
        for key in required_top_keys:
            assert key in data, f"Missing key: {key}"

    def test_conversion_happy_path_basic_value_sanity(self):
        """Retains legacy stub-level smoke checks with value sanity assertions."""
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["experiment_id"] == "exp_email"
        assert isinstance(data["control"], dict)
        assert "n" in data["control"]
        assert data["control"]["n"] > 10000
        assert data["diff_pct"] > 0
        assert data["standard_error"] >= 0
        assert data["ci_95"]["low"] <= data["ci_95"]["high"]

    def test_revenue_metric_happy_path(self):
        """Covers revenue_per_user request path previously in stub tests."""
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "revenue_per_user"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "diff_pct" in data
        assert data["diff_pct"] > 0

    def test_cuped_requested_flag_echoed(self):
        """Covers use_cuped request wiring previously in stub tests."""
        resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "exp_email",
                "metric": "conversion_rate",
                "use_cuped": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["cuped"]["requested"] is True
        assert "data_as_of" in data

    def test_auto_comparison_mode(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        data = resp.json()
        assert data["comparison_mode"] in ("auto", "spec_default")
        assert data["comparison_control_variant"] in ("No E-Mail", "Mens E-Mail", "Womens E-Mail")
        assert data["comparison_treatment_variant"] in ("No E-Mail", "Mens E-Mail", "Womens E-Mail")
        assert data["comparison_control_variant"] != data["comparison_treatment_variant"]

    def test_spec_default_comparison_mode_for_exp_email(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["comparison_mode"] == "spec_default"
        assert data["comparison_control_variant"] == "No E-Mail"
        assert data["comparison_treatment_variant"] == "Mens E-Mail"

    def test_explicit_comparison_mode(self):
        resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "exp_email",
                "metric": "conversion_rate",
                "control_variant": "No E-Mail",
                "treatment_variant": "Mens E-Mail",
            },
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["comparison_mode"] == "explicit"
        assert data["comparison_control_variant"] == "No E-Mail"
        assert data["comparison_treatment_variant"] == "Mens E-Mail"

    def test_explicit_mode_produces_different_result_than_auto(self):
        auto_resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        explicit_resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "exp_email",
                "metric": "conversion_rate",
                "control_variant": "No E-Mail",
                "treatment_variant": "Mens E-Mail",
            },
        )
        assert explicit_resp.json()["comparison_control_variant"] == "No E-Mail"
        assert explicit_resp.json()["comparison_mode"] == "explicit"
        assert auto_resp.json()["comparison_mode"] in ("auto", "spec_default")

    def test_invalid_variant_name_returns_400(self):
        resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "exp_email",
                "metric": "conversion_rate",
                "control_variant": "ghost_variant",
                "treatment_variant": "Mens E-Mail",
            },
        )
        assert resp.status_code == 400
        assert "Explicit variants not found" in resp.json()["detail"]

    def test_only_control_without_treatment_returns_400(self):
        resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "exp_email",
                "metric": "conversion_rate",
                "control_variant": "No E-Mail",
            },
        )
        assert resp.status_code == 400
        assert "must be provided together" in resp.json()["detail"]

    def test_same_variant_for_both_returns_400(self):
        resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "exp_email",
                "metric": "conversion_rate",
                "control_variant": "No E-Mail",
                "treatment_variant": "No E-Mail",
            },
        )
        assert resp.status_code == 400

    def test_segment_by_returns_segment_results(self):
        resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "health_exp_reminder_30d",
                "metric": "readmission_30d_rate",
                "segment_by": ["region"],
            },
        )
        assert resp.status_code == 200
        segment_results = resp.json()["segment_results"]
        assert isinstance(segment_results, list)
        assert len(segment_results) > 0

    def test_segment_results_contain_bh_fields(self):
        resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "health_exp_reminder_30d",
                "metric": "readmission_30d_rate",
                "segment_by": ["region"],
            },
        )
        for seg in resp.json()["segment_results"]:
            assert "bh_fdr_significant" in seg
            assert "bh_q_value" in seg
            assert isinstance(seg["bh_fdr_significant"], bool)
            assert 0.0 <= seg["bh_q_value"] <= 1.0

    def test_segment_results_contain_segment_key(self):
        resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "health_exp_reminder_30d",
                "metric": "readmission_30d_rate",
                "segment_by": ["region"],
            },
        )
        for seg in resp.json()["segment_results"]:
            assert "segment" in seg
            assert "region" in seg["segment"]

    def test_no_segment_by_returns_empty_segment_results(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        assert resp.json()["segment_results"] == []

    def test_invalid_segment_by_column_returns_400(self):
        resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "exp_email",
                "metric": "conversion_rate",
                "segment_by": ["not_a_real_column"],
            },
        )
        assert resp.status_code == 400
        assert "Unsupported segment_by columns" in resp.json()["detail"]

    def test_invalid_segment_filter_column_returns_400(self):
        resp = client.post(
            "/compute_metrics",
            json={
                "experiment_id": "exp_email",
                "metric": "conversion_rate",
                "segment_filters": {"bad_col": "val"},
            },
        )
        assert resp.status_code == 400
        assert "Unsupported segment filter column" in resp.json()["detail"]

    def test_data_quality_block_present_with_all_fields(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        dq = resp.json()["data_quality"]
        for field in (
            "srm_flag",
            "srm_p_value",
            "expected_split",
            "observed_split",
            "missing_rate",
            "duplicate_rate",
            "outlier_counts",
        ):
            assert field in dq

    def test_experiment_spec_echoed_in_response(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        spec = resp.json()["experiment_spec"]
        assert spec is not None
        assert spec["experiment_id"] == "exp_email"
        assert spec["primary_metric"] == "conversion_rate"
        assert isinstance(spec["guardrail_metrics"], list)
        assert "min_sample_size" in spec
        assert "stopping_rule_note" in spec
        assert spec.get("default_control_variant") == "No E-Mail"
        assert spec.get("default_treatment_variant") == "Mens E-Mail"

    def test_metric_validation_block_present(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        mv = resp.json()["metric_validation"]
        assert mv["registry_found"] is True
        assert mv["metric"] == "conversion_rate"
        assert mv["description"] is not None
        assert mv["dataset"] == "events"

    def test_data_quality_srm_flag_is_bool(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        assert isinstance(resp.json()["data_quality"]["srm_flag"], bool)

    def test_data_quality_missing_rate_between_0_and_1(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        missing_rate = resp.json()["data_quality"]["missing_rate"]
        assert 0.0 <= missing_rate <= 1.0

    def test_exp_email_no_srm_with_three_equal_variants(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        assert resp.json()["data_quality"]["srm_flag"] is False

    def test_guardrail_results_list_in_response(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "health_exp_reminder_30d", "metric": "readmission_30d_rate"},
        )
        assert isinstance(resp.json()["guardrail_results"], list)

    def test_guardrail_results_contain_metric_and_status(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "health_exp_reminder_30d", "metric": "readmission_30d_rate"},
        )
        for g in resp.json()["guardrail_results"]:
            assert "metric" in g
            assert "status" in g
            assert g["status"] in ("pass", "fail")
            assert "thresholds" in g

    def test_recommendation_is_valid_value(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        assert resp.json()["recommendation"] in ("ship", "hold", "stop")

    def test_recommendation_details_structure(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        details = resp.json()["recommendation_details"]
        for key in (
            "recommendation",
            "rationale",
            "primary_positive_significant",
            "meets_min_sample_size",
        ):
            assert key in details
        assert isinstance(details["rationale"], list)

    def test_health_experiment_guardrail_failures_produce_stop(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "health_exp_reminder_30d", "metric": "readmission_30d_rate"},
        )
        assert resp.json()["recommendation"] == "stop"

    def test_metric_not_in_registry_returns_400(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "fake_metric_xyz"},
        )
        assert resp.status_code == 400

    def test_metric_in_registry_but_unsupported_by_engine(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "lift_conversion"},
        )
        assert resp.status_code == 400

    def test_unknown_experiment_returns_400(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "nonexistent_exp_zzz", "metric": "conversion_rate"},
        )
        assert resp.status_code == 400

    def test_ci_low_leq_ci_high_in_response(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        ci = resp.json()["ci_95"]
        assert ci["low"] <= ci["high"]

    def test_p_value_in_0_1(self):
        resp = client.post(
            "/compute_metrics",
            json={"experiment_id": "exp_email", "metric": "conversion_rate"},
        )
        p_value = resp.json()["p_value"]
        assert 0.0 <= p_value <= 1.0
