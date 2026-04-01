from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_OUTPUT_PATH = Path("data") / "raw" / "health_journey_events.csv"


def generate_health_events(
    num_patients: int = 12000,
    seed: int = 20260318,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    experiment_id: str = "health_exp_reminder_30d",
    start_date: str = "2024-01-01",
) -> pd.DataFrame:
    """Generate synthetic healthcare patient-journey events dataset.

    # This generator encodes stylized relationships (risk/age/reminder effects)
    # for experimentation demos and is not clinically calibrated.
    """
    rng = np.random.default_rng(seed)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    patient_ids = np.array([f"pt_{i:06d}" for i in range(num_patients)])
    regions = rng.choice(["NE", "MW", "S", "W"], size=num_patients, p=[0.22, 0.24, 0.31, 0.23])
    age_bucket = rng.choice(["18-39", "40-64", "65+"], size=num_patients, p=[0.34, 0.42, 0.24])
    risk_segment = rng.choice(["low", "medium", "high"], size=num_patients, p=[0.45, 0.38, 0.17])
    device = rng.choice(["portal", "phone"], size=num_patients, p=[0.62, 0.38])
    channel = np.where(device == "portal", "push", "sms")

    variant = rng.choice(["control", "reminder"], size=num_patients, p=[0.5, 0.5])
    admission_offset_days = rng.integers(0, 90, size=num_patients)
    admission_ts = pd.to_datetime(start_date) + pd.to_timedelta(admission_offset_days, unit="D")

    # Reminder impact is stronger for higher-risk and older patients.
    risk_readmit_base = {"low": 0.09, "medium": 0.17, "high": 0.29}
    age_readmit_bump = {"18-39": -0.01, "40-64": 0.0, "65+": 0.04}
    risk_followup_base = {"low": 0.78, "medium": 0.69, "high": 0.58}
    age_followup_bump = {"18-39": 0.02, "40-64": 0.0, "65+": -0.03}

    records: list[dict] = []
    for i in range(num_patients):
        pid = patient_ids[i]
        seg_region = regions[i]
        seg_age = age_bucket[i]
        seg_risk = risk_segment[i]
        seg_device = device[i]
        seg_channel = channel[i]
        arm = variant[i]
        admit_time = admission_ts[i]

        los_base = 2.7 + (0.7 if seg_risk == "medium" else 1.6 if seg_risk == "high" else 0.0)
        los_noise = float(rng.normal(0.0, 0.8))
        los_days = float(np.clip(los_base + los_noise - (0.18 if arm == "reminder" else 0.0), 1.0, 12.0))
        discharge_time = admit_time + pd.to_timedelta(los_days, unit="D")

        followup_prob = risk_followup_base[seg_risk] + age_followup_bump[seg_age]
        if arm == "reminder":
            uplift = 0.035 if seg_risk == "low" else 0.055 if seg_risk == "medium" else 0.085
            if seg_age == "65+":
                uplift += 0.015
            followup_prob += uplift
        followup_prob = float(np.clip(followup_prob, 0.02, 0.98))
        followup_completed = rng.random() < followup_prob

        readmit_prob = risk_readmit_base[seg_risk] + age_readmit_bump[seg_age]
        if followup_completed:
            readmit_prob -= 0.03
        if arm == "reminder":
            reduction = 0.012 if seg_risk == "low" else 0.022 if seg_risk == "medium" else 0.036
            readmit_prob -= reduction
        readmit_prob = float(np.clip(readmit_prob, 0.01, 0.8))
        readmitted_30d = rng.random() < readmit_prob

        common = {
            "patient_id": pid,
            "timestamp": admit_time,
            "experiment_id": experiment_id,
            "variant": arm,
            "region": seg_region,
            "device": seg_device,
            "age_bucket": seg_age,
            "channel": seg_channel,
            "risk_segment": seg_risk,
        }

        records.append({**common, "event_name": "admission", "value": None})
        records.append(
            {
                **common,
                "timestamp": admit_time + pd.to_timedelta(float(rng.uniform(0.05, 0.45)), unit="D"),
                "event_name": "lab_test",
                "value": float(np.clip(rng.normal(125.0, 22.0), 35.0, 300.0)),
            }
        )

        if arm == "reminder":
            records.append(
                {
                    **common,
                    "timestamp": discharge_time + pd.to_timedelta(1.0, unit="D"),
                    "event_name": "reminder_sms",
                    "value": 1.0,
                }
            )

        if followup_completed:
            records.append(
                {
                    **common,
                    "timestamp": discharge_time + pd.to_timedelta(float(rng.uniform(4.0, 15.0)), unit="D"),
                    "event_name": "followup_completed",
                    "value": 1.0,
                }
            )

        records.append(
            {
                **common,
                "timestamp": discharge_time,
                "event_name": "discharge",
                "value": None,
            }
        )
        records.append(
            {
                **common,
                "timestamp": discharge_time,
                "event_name": "length_of_stay",
                "value": los_days,
            }
        )

        if readmitted_30d:
            records.append(
                {
                    **common,
                    "timestamp": discharge_time + pd.to_timedelta(float(rng.uniform(2.0, 29.0)), unit="D"),
                    "event_name": "readmission_30d",
                    "value": 1.0,
                }
            )

    events_df = pd.DataFrame(records).sort_values(["patient_id", "timestamp", "event_name"])
    events_df.to_csv(output_path, index=False)
    return events_df


if __name__ == "__main__":
    print(
        "This module is an internal generator utility. "
        "Use `python -m experimentplatform.analytics.ingest_events --health-generate` instead."
    )