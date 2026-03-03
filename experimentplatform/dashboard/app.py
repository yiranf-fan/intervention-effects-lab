import requests
import streamlit as st

API_BASE = "http://127.0.0.1:8000"


def main():
    st.title("Experiment Metrics Dashboard (Tech)")

    experiment_id = st.text_input("Experiment ID", "exp_banner")
    metric = st.selectbox(
        "Metric",
        ["conversion_rate", "revenue_per_user"],
    )

    if st.button("Compute metrics"):
        resp = requests.post(
            f"{API_BASE}/computemetrics",
            json={"experiment_id": experiment_id, "metric": metric},
        )
        if resp.status_code != 200:
            st.error(resp.json())
        else:
            data = resp.json()
            st.json(data)

    st.subheader("Power / MDE")
    baseline = st.number_input("Baseline rate", value=0.1)
    target_effect = st.number_input("Target effect (abs diff)", value=0.02)
    if st.button("Compute sample size"):
        resp = requests.post(
            f"{API_BASE}/power",
            json={
                "baseline_rate": baseline,
                "target_effect": target_effect,
            },
        )
        if resp.status_code != 200:
            st.error(resp.json())
        else:
            st.write("Required sample size:", resp.json()["required_sample_size"])


if __name__ == "__main__":
    main()
