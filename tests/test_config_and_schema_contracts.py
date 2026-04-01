"""Config-loader and schema contract tests.

These were extracted from week-oriented tests into content-focused coverage.
"""

import pytest

from shared.config.loader import (
    ConfigError,
    get_experiment_spec,
    load_experiment_specs,
    load_metric_registry,
    validate_metric_in_registry,
)
from shared.schemas import ExperimentSpec, GuardrailMetricSpec, MetricRequest


@pytest.fixture(autouse=True)
def clear_config_loader_caches():
    """Prevent cross-test contamination from @lru_cache-backed config loaders."""
    from shared.config import loader as loader_mod

    loader_mod.load_experiment_specs.cache_clear()
    loader_mod.load_metric_registry.cache_clear()
    yield
    loader_mod.load_experiment_specs.cache_clear()
    loader_mod.load_metric_registry.cache_clear()


@pytest.fixture()
def minimal_experiment_spec() -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="test_exp",
        owner="ds@example.com",
        hypothesis="Treatment improves conversion rate.",
        primary_metric="conversion_rate",
        guardrail_metrics=[
            GuardrailMetricSpec(name="revenue_per_user", min_value=-0.05)
        ],
        min_run_time_days=7,
        min_sample_size=200,
    )


class TestExperimentSpec:
    def test_valid_spec_loads(self):
        spec = ExperimentSpec(
            experiment_id="exp_test",
            owner="owner@example.com",
            hypothesis="hypothesis text",
            primary_metric="conversion_rate",
            min_run_time_days=14,
            min_sample_size=1000,
        )
        assert spec.experiment_id == "exp_test"
        assert spec.guardrail_metrics == []

    def test_guardrail_metrics_populated(self, minimal_experiment_spec):
        assert len(minimal_experiment_spec.guardrail_metrics) == 1
        assert minimal_experiment_spec.guardrail_metrics[0].name == "revenue_per_user"

    def test_guardrail_min_value_set(self, minimal_experiment_spec):
        assert minimal_experiment_spec.guardrail_metrics[0].min_value == -0.05

    def test_optional_fields_default_to_none(self):
        spec = ExperimentSpec(
            experiment_id="x",
            owner="o",
            hypothesis="h",
            primary_metric="conversion_rate",
            min_run_time_days=7,
            min_sample_size=100,
        )
        assert spec.domain is None
        assert spec.stopping_rule_note is None
        assert spec.expected_split is None

    def test_from_dict_via_model_validate(self):
        data = {
            "experiment_id": "exp_reminder",
            "domain": "health",
            "owner": "health@example.com",
            "hypothesis": "Reminders improve adherence.",
            "primary_metric": "conversion_rate",
            "guardrail_metrics": [{"name": "revenue_per_user", "min_value": -0.10}],
            "min_run_time_days": 7,
            "min_sample_size": 200,
            "expected_split": {"control": 0.5, "reminder": 0.5},
        }
        spec = ExperimentSpec.model_validate(data)
        assert spec.domain == "health"
        assert spec.expected_split == {"control": 0.5, "reminder": 0.5}


class TestConfigLoader:
    def test_load_experiment_specs_returns_all_experiments(self):
        specs = load_experiment_specs()
        assert "exp_email" in specs
        assert "exp_reminder" in specs
        assert "health_exp_reminder_30d" in specs

    def test_experiment_spec_fields_correct(self):
        specs = load_experiment_specs()
        email_spec = specs["exp_email"]
        assert email_spec.owner == "growth_ds@company.example"
        assert email_spec.primary_metric == "conversion_rate"
        assert email_spec.min_sample_size == 20000

    def test_get_experiment_spec_returns_spec(self):
        spec = get_experiment_spec("exp_email")
        assert spec is not None
        assert spec.experiment_id == "exp_email"

    def test_get_experiment_spec_returns_none_for_missing(self):
        assert get_experiment_spec("nonexistent_experiment_xyz") is None

    def test_load_metric_registry_has_expected_metrics(self):
        registry = load_metric_registry()
        for expected in (
            "conversion_rate",
            "revenue_per_user",
            "readmission_30d_rate",
            "lift_conversion",
        ):
            assert expected in registry

    def test_validate_metric_in_registry_returns_meta(self):
        meta = validate_metric_in_registry("conversion_rate")
        assert meta["name"] == "conversion_rate"
        assert "description" in meta
        assert "dataset" in meta
        assert "denominator_definition" in meta

    def test_validate_missing_metric_raises_config_error(self):
        with pytest.raises(ConfigError, match="missing from metric registry"):
            validate_metric_in_registry("totally_fake_metric")

    def test_guardrail_spec_from_experiments_yaml(self):
        specs = load_experiment_specs()
        health_spec = specs["health_exp_reminder_30d"]
        guardrail_names = [g.name for g in health_spec.guardrail_metrics]
        assert "length_of_stay" in guardrail_names
        assert "followup_completion_rate" in guardrail_names

    def test_expected_split_loaded_for_exp_email(self):
        specs = load_experiment_specs()
        email_spec = specs["exp_email"]
        assert email_spec.expected_split is not None
        assert "No E-Mail" in email_spec.expected_split
        assert abs(sum(email_spec.expected_split.values()) - 1.0) < 0.01

    def test_default_comparison_variants_loaded_for_exp_email(self):
        specs = load_experiment_specs()
        email_spec = specs["exp_email"]
        assert email_spec.default_control_variant == "No E-Mail"
        assert email_spec.default_treatment_variant == "Mens E-Mail"

    def test_config_error_on_missing_file(self, tmp_path, monkeypatch):
        from shared.config import loader as loader_mod

        fake_path = tmp_path / "nonexistent.yaml"
        monkeypatch.setattr(loader_mod, "EXPERIMENTS_CONFIG_PATH", fake_path)
        # lru_cache note: clear cache before and after monkeypatch use to avoid bleed-through.
        loader_mod.load_experiment_specs.cache_clear()
        try:
            with pytest.raises(ConfigError, match="Config not found"):
                loader_mod.load_experiment_specs()
        finally:
            loader_mod.load_experiment_specs.cache_clear()

    def test_metric_registry_entries_have_name_field(self):
        registry = load_metric_registry()
        for name, entry in registry.items():
            assert entry.get("name") == name


class TestMetricRequest:
    def test_minimal_metric_request(self):
        req = MetricRequest(experiment_id="exp_email", metric="conversion_rate")
        assert req.control_variant is None
        assert req.treatment_variant is None

    def test_explicit_variant_fields(self):
        req = MetricRequest(
            experiment_id="exp_email",
            metric="conversion_rate",
            control_variant="No E-Mail",
            treatment_variant="Mens E-Mail",
        )
        assert req.control_variant == "No E-Mail"
        assert req.treatment_variant == "Mens E-Mail"

    def test_segment_by_field(self):
        req = MetricRequest(
            experiment_id="exp_email",
            metric="conversion_rate",
            segment_by=["region", "device"],
        )
        assert req.segment_by == ["region", "device"]

    def test_segment_filters_field(self):
        req = MetricRequest(
            experiment_id="exp_email",
            metric="conversion_rate",
            segment_filters={"region": "NA"},
        )
        assert req.segment_filters == {"region": "NA"}


class TestExperimentSpecDefaults:
    def test_optional_default_variants_default_to_none(self):
        spec = ExperimentSpec(
            experiment_id="exp_without_defaults",
            owner="owner@example.com",
            hypothesis="h",
            primary_metric="conversion_rate",
            min_run_time_days=7,
            min_sample_size=100,
        )
        assert spec.default_control_variant is None
        assert spec.default_treatment_variant is None
