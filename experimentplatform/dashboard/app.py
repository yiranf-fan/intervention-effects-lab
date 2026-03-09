import requests
import streamlit as st
import os

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")


def main():
    st.title("Experiment Metrics Dashboard (Tech)")

    experiments = []
    try:
        experiments_resp = requests.get(f"{API_BASE}/experiments", timeout=8)
        if experiments_resp.status_code == 200:
            experiments = experiments_resp.json().get("experiments", [])
        else:
            st.warning("Could not load experiment list from API; using default experiment id.")
    except requests.RequestException:
        st.warning("API is unavailable for experiment listing; using default experiment id.")

    experiment_options = [e["experiment_id"] for e in experiments] or ["exp_banner"]
    experiment_id = st.selectbox("Experiment ID", experiment_options)

    selected_meta = next((e for e in experiments if e["experiment_id"] == experiment_id), None)
    if selected_meta:
        st.caption(
            f"Users: {selected_meta['users_count']} | "
            f"Events: {selected_meta['events_count']} | "
            f"Variants: {', '.join(selected_meta.get('variants', []))}"
        )
    metric = st.selectbox(
        "Metric",
        ["conversion_rate", "revenue_per_user"],
    )
    use_cuped = st.toggle("Use CUPED variance reduction", value=False)

    if st.button("Compute metrics"):
        resp = requests.post(
            f"{API_BASE}/compute_metrics",
            json={
                "experiment_id": experiment_id,
                "metric": metric,
                "use_cuped": use_cuped,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            st.error(resp.json())
        else:
            data = resp.json()
            st.subheader("Metric Result")
            st.write(f"Data as of: {data.get('data_as_of', 'N/A')}")
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
