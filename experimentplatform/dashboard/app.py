import requests
import streamlit as st
import os
import pandas as pd


SEGMENT_COLUMNS = ["region", "device", "age_bucket"]
TECH_METRICS = ["conversion_rate", "revenue_per_user"]
HEALTH_METRICS = [
    "conversion_rate",
    "revenue_per_user",
    "readmission_30d_rate",
    "length_of_stay",
    "followup_completion_rate",
]

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")


def _infer_domain_from_experiment_id(experiment_id: str) -> str:
    exp = (experiment_id or "").lower()
    # NOTE: assumption – to be validated
    # Domain is inferred from naming convention when explicit metadata is unavailable.
    if any(token in exp for token in ["reminder", "health", "patient", "readmission"]):
        return "health"
    return "tech"


def filter_experiments_by_domain(experiments: list[dict], domain: str) -> list[dict]:
    domain_key = domain.lower()
    filtered = [
        e for e in experiments
        if (e.get("domain") or _infer_domain_from_experiment_id(e.get("experiment_id", ""))).lower() == domain_key
    ]
    return filtered


def build_compute_metrics_payload(
    experiment_id: str,
    metric: str,
    use_cuped: bool,
    control_variant: str | None,
    treatment_variant: str | None,
    segment_filters: dict,
    segment_by: list[str],
) -> dict:
    payload = {
        "experiment_id": experiment_id,
        "metric": metric,
        "use_cuped": use_cuped,
    }
    if control_variant and treatment_variant:
        payload["control_variant"] = control_variant
        payload["treatment_variant"] = treatment_variant
    if segment_filters:
        payload["segment_filters"] = segment_filters
    if segment_by:
        payload["segment_by"] = segment_by
    return payload


def main():
    st.title("Experiment Metrics Dashboard")

    experiments = []
    try:
        experiments_resp = requests.get(f"{API_BASE}/experiments", timeout=8)
        if experiments_resp.status_code == 200:
            experiments = experiments_resp.json().get("experiments", [])
        else:
            st.warning("Could not load experiment list from API; using default experiment id.")
    except requests.RequestException:
        st.warning("API is unavailable for experiment listing; using default experiment id.")

    domain = st.selectbox("Domain", ["Tech", "Health"], index=0)
    domain_experiments = filter_experiments_by_domain(experiments, domain)
    if not domain_experiments:
        st.info(f"No experiments found for {domain}. Showing all experiments instead.")
        domain_experiments = experiments

    experiment_options = [e["experiment_id"] for e in domain_experiments] or ["exp_banner"]
    experiment_id = st.selectbox("Experiment ID", experiment_options)

    selected_meta = next((e for e in domain_experiments if e["experiment_id"] == experiment_id), None)
    if selected_meta:
        st.caption(
            f"Users: {selected_meta['users_count']} | "
            f"Events: {selected_meta['events_count']} | "
            f"Variants: {', '.join(selected_meta.get('variants', []))} | "
            f"Domain: {(selected_meta.get('domain') or _infer_domain_from_experiment_id(experiment_id)).title()}"
        )

    variants = selected_meta.get("variants", []) if selected_meta else []
    st.subheader("Comparison Setup")
    variant_mode = st.radio("Variant comparison mode", ["Auto", "Explicit"], horizontal=True)
    explicit_variant_options = [None] + variants
    control_variant = None
    treatment_variant = None
    if variant_mode == "Explicit":
        control_variant = st.selectbox("Control variant", explicit_variant_options, format_func=lambda x: x or "Select...")
        treatment_variant = st.selectbox("Treatment variant", explicit_variant_options, format_func=lambda x: x or "Select...")
        if control_variant and treatment_variant and control_variant == treatment_variant:
            st.warning("Control and treatment should be different variants.")

    metric_options = TECH_METRICS if domain.lower() == "tech" else HEALTH_METRICS
    metric = st.selectbox("Metric", metric_options)
    use_cuped = st.toggle("Use CUPED variance reduction", value=False)

    st.subheader("Segmentation")
    segment_by = st.multiselect("Segment results by", SEGMENT_COLUMNS, default=[])
    seg_col1, seg_col2, seg_col3 = st.columns(3)
    region_filter = seg_col1.text_input("Region filter", value="")
    device_filter = seg_col2.text_input("Device filter", value="")
    age_filter = seg_col3.text_input("Age bucket filter", value="")

    segment_filters = {}
    if region_filter.strip():
        segment_filters["region"] = region_filter.strip()
    if device_filter.strip():
        segment_filters["device"] = device_filter.strip()
    if age_filter.strip():
        segment_filters["age_bucket"] = age_filter.strip()

    if st.button("Compute metrics"):
        payload = build_compute_metrics_payload(
            experiment_id=experiment_id,
            metric=metric,
            use_cuped=use_cuped,
            control_variant=control_variant,
            treatment_variant=treatment_variant,
            segment_filters=segment_filters,
            segment_by=segment_by,
        )

        resp = requests.post(
            f"{API_BASE}/compute_metrics",
            json=payload,
            timeout=15,
        )
        if resp.status_code != 200:
            st.error(resp.json())
        else:
            data = resp.json()
            st.subheader("Metric Result")
            st.write(f"Data as of: {data.get('data_as_of', 'N/A')}")
            st.write(
                f"Comparison: {data.get('comparison_control_variant')} vs "
                f"{data.get('comparison_treatment_variant')} ({data.get('comparison_mode')})"
            )
            st.write(
                f"Diff: {round(data['diff'], 6)} ({data['diff_pct']}%)"
            )
            st.write(
                f"SE: {round(data.get('standard_error', 0.0), 6)} | "
                f"95% CI: [{round(data.get('ci_95', {}).get('low', 0.0), 6)}, "
                f"{round(data.get('ci_95', {}).get('high', 0.0), 6)}]"
            )

            cuped = data.get("cuped", {})
            if cuped.get("requested"):
                st.caption(
                    f"CUPED applied={cuped.get('applied')} | "
                    f"theta={cuped.get('theta')} | "
                    f"variance_reduction_pct={cuped.get('variance_reduction_pct')}"
                )

            st.subheader("Recommendation & Data Quality")
            recommendation = data.get("recommendation", "hold").upper()
            st.metric("Recommendation", recommendation)

            guardrail_results = data.get("guardrail_results", [])
            guardrail_failures = [g for g in guardrail_results if g.get("status") == "fail"]
            if guardrail_failures:
                st.error("Guardrail failures detected")
                st.table(pd.DataFrame(guardrail_failures))

            data_quality = data.get("data_quality", {})
            if data_quality.get("srm_flag"):
                st.warning(f"SRM flag is ON (p={data_quality.get('srm_p_value', 1.0):.4f})")
            st.write(
                f"Missing rate: {data_quality.get('missing_rate', 0.0):.3f} | "
                f"Duplicate rate: {data_quality.get('duplicate_rate', 0.0):.3f}"
            )

            segment_results = data.get("segment_results", [])
            if segment_results:
                st.subheader("Segment-level Results")
                rows = []
                for r in segment_results:
                    segment_label = ", ".join(f"{k}={v}" for k, v in (r.get("segment") or {}).items())
                    rows.append(
                        {
                            "segment": segment_label,
                            "diff": r.get("diff"),
                            "ci_low": (r.get("ci_95") or {}).get("low"),
                            "ci_high": (r.get("ci_95") or {}).get("high"),
                            "p_value": r.get("p_value"),
                            "bh_q_value": r.get("bh_q_value"),
                            "bh_fdr_significant": r.get("bh_fdr_significant"),
                        }
                    )
                st.dataframe(pd.DataFrame(rows), use_container_width=True)

            st.json(data)

    st.subheader("Power / MDE")
    baseline = st.number_input("Baseline rate", value=0.1)
    target_effect = st.number_input("Target effect (abs diff)", value=0.02)
    alpha = st.number_input("Alpha", value=0.05, min_value=0.0001, max_value=0.5)
    power = st.number_input("Desired power", value=0.8, min_value=0.5, max_value=0.9999)
    if st.button("Compute sample size"):
        resp = requests.post(
            f"{API_BASE}/power",
            json={
                "baseline_rate": baseline,
                "target_effect": target_effect,
                "alpha": alpha,
                "power": power,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            st.error(resp.json())
        else:
            st.write("Required sample size:", resp.json()["required_sample_size"])


if __name__ == "__main__":
    main()
