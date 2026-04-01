from pathlib import Path
import hashlib

import duckdb
import pandas as pd

from experimentplatform.analytics.generate_health_events import generate_health_events


DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "events.duckdb"
RAW_DIR = DATA_DIR / "raw"
HILLSTROM_CSV = RAW_DIR / "hillstrom_events.csv"
SAMPLE_CSV = RAW_DIR / "clickstream_sample.csv"
HEALTH_JOURNEY_CSV = RAW_DIR / "health_journey_events.csv"

UNIFIED_EVENT_COLUMNS = [
    "user_id",
    "timestamp",
    "experiment_id",
    "variant",
    "event_name",
    "value",
    "region",
    "device",
    "age_bucket",
    "channel",
    "risk_segment",
]


def load_sample_data(csv_path: Path = SAMPLE_CSV) -> None:
    RAW_DIR.mkdir(exist_ok=True, parents=True)

    if not csv_path.exists():
        raise FileNotFoundError(f"Sample CSV not found: {csv_path}")

    _load_csv_to_db(csv_path, mode="replace")

    with duckdb.connect(DB_PATH.as_posix()) as con:
        row_count = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    print(f"Loaded {row_count} sample events from {csv_path}")


def load_hillstrom_to_events(mode: str = "replace") -> None:
    RAW_DIR.mkdir(exist_ok=True, parents=True)
    hillstrom_csv = HILLSTROM_CSV

    if hillstrom_csv.exists():
        print(f"Reusing existing Hillstrom CSV: {hillstrom_csv}")
    else:
        print("Downloading Hillstrom dataset...")
        url = "http://www.minethatdata.com/Kevin_Hillstrom_MineThatData_E-MailAnalytics_DataMiningChallenge_2008.03.20.csv"
        df_customers = pd.read_csv(url)

        events = []
        for idx, row in df_customers.iterrows():
            events.append(
                {
                    "user_id": f"cust_{idx}",
                    "timestamp": pd.Timestamp("2024-01-01"),
                    "experiment_id": "exp_email",
                    "variant": row["segment"],
                    "event_name": "visit",
                    "value": None,
                    "region": None,
                    "device": None,
                    "age_bucket": None,
                    "channel": None,
                    "risk_segment": None,
                }
            )
            if row["visit"] == 1:
                events.append(
                    {
                        "user_id": f"cust_{idx}",
                        "timestamp": pd.Timestamp("2024-01-01T01:00:00"),
                        "experiment_id": "exp_email",
                        "variant": row["segment"],
                        "event_name": "conversion",
                        "value": row["spend"],
                        "region": None,
                        "device": None,
                        "age_bucket": None,
                        "channel": None,
                        "risk_segment": None,
                    }
                )

        events_df = pd.DataFrame(events)
        events_df.to_csv(hillstrom_csv, index=False)
        print(f"Downloaded + saved {len(events_df)} events to {hillstrom_csv}")

    _load_csv_to_db(hillstrom_csv, mode=mode)
    with duckdb.connect(DB_PATH.as_posix()) as con:
        row_count = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"Loaded {row_count} Hillstrom events to DuckDB")


def load_health_journey_to_events(
    csv_path: Path = HEALTH_JOURNEY_CSV,
    mode: str = "append",
    health_experiment_id: str = "exp_reminder_30_day",
    force_experiment_id: bool = False,
    num_patients: int = 12000,
    seed: int = 20260318,
) -> None:
    """Load health journey events into unified events schema.

    # Variant can be synthesized from user_id hash when source variant is missing.
    """
    RAW_DIR.mkdir(exist_ok=True, parents=True)
    if csv_path.exists():
        print(f"Reusing existing health events CSV: {csv_path}")
    else:
        print(f"Generating health events CSV: {csv_path}")
        generate_health_events(
            num_patients=num_patients,
            seed=seed,
            output_path=csv_path,
            experiment_id=health_experiment_id,
        )

    raw_df = pd.read_csv(csv_path)
    events_df = _map_health_events_to_unified(
        raw_df,
        health_experiment_id=health_experiment_id,
        force_experiment_id=force_experiment_id,
    )
    _load_df_to_db(events_df, mode=mode)

    inserted = len(events_df)
    experiment_breakdown = (
        events_df["experiment_id"].fillna("<null>").value_counts().sort_index().to_dict()
        if inserted
        else {}
    )
    print(
        f"Loaded {inserted} health events from {csv_path} "
        f"(default_experiment_id={health_experiment_id}, force_experiment_id={force_experiment_id}, "
        f"by_experiment={experiment_breakdown})"
    )


def load_tech_and_health_events() -> None:
    """Rebuild events table with tech + health domains."""
    load_hillstrom_to_events(mode="replace")

    if HEALTH_JOURNEY_CSV.exists():
        # Raw health file is the preferred single source.
        # NOTE: ingestion policy choice – to be validated with downstream ownership.
        load_health_journey_to_events(mode="append")
    else:
        print(f"Raw health file missing; generating canonical file: {HEALTH_JOURNEY_CSV}")
        load_health_journey_to_events(
            csv_path=HEALTH_JOURNEY_CSV,
            mode="append",
            health_experiment_id="health_exp_reminder_30d",
            force_experiment_id=True,
        )


def _hash_to_binary_variant(value: str) -> str:
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()
    return "reminder" if int(digest[:8], 16) % 2 == 0 else "control"


def _map_health_events_to_unified(
    raw_df: pd.DataFrame,
    health_experiment_id: str,
    force_experiment_id: bool = False,
) -> pd.DataFrame:
    normalized = raw_df.copy()
    rename_map = {
        "patient_id": "user_id",
        "event_timestamp": "timestamp",
        "ts": "timestamp",
        "event_type": "event_name",
        "patient_region": "region",
    }
    normalized = normalized.rename(columns={k: v for k, v in rename_map.items() if k in normalized.columns})

    if "user_id" not in normalized.columns:
        raise ValueError("health journey input must contain user_id or patient_id")
    if "timestamp" not in normalized.columns:
        raise ValueError("health journey input must contain timestamp-like column")

    if "event_name" not in normalized.columns:
        normalized["event_name"] = "admission"

    normalized["user_id"] = normalized["user_id"].astype(str)
    normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], errors="coerce")
    if "experiment_id" not in normalized.columns:
        normalized["experiment_id"] = health_experiment_id
    normalized["experiment_id"] = normalized["experiment_id"].fillna(health_experiment_id)
    if force_experiment_id:
        # Canonicalization keeps all rows under one experiment_id for deterministic demos.
        normalized["experiment_id"] = health_experiment_id

    if "variant" not in normalized.columns:
        normalized["variant"] = normalized["user_id"].map(_hash_to_binary_variant)
    else:
        normalized["variant"] = normalized["variant"].fillna(normalized["user_id"].map(_hash_to_binary_variant))

    if "value" not in normalized.columns:
        normalized["value"] = normalized["event_name"].astype(str).str.lower().isin({"conversion", "intervention", "reminder"}).map(
            {True: 1.0, False: None}
        )

    for col, default in {
        "region": "NA",
        "device": "desktop",
        "age_bucket": "35-54",
        "channel": "ehr",
        "risk_segment": "medium",
    }.items():
        if col not in normalized.columns:
            normalized[col] = default
        normalized[col] = normalized[col].fillna(default)

    if "event_name" in normalized.columns:
        normalized["event_name"] = normalized["event_name"].astype(str)

    out = normalized[UNIFIED_EVENT_COLUMNS].copy()
    return out


def _ensure_events_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            user_id VARCHAR,
            timestamp TIMESTAMP,
            experiment_id VARCHAR,
            variant VARCHAR,
            event_name VARCHAR,
            value DOUBLE,
            region VARCHAR,
            device VARCHAR,
            age_bucket VARCHAR,
            channel VARCHAR,
            risk_segment VARCHAR
        )
        """
    )


def _load_df_to_db(events_df: pd.DataFrame, mode: str = "replace") -> None:
    with duckdb.connect(DB_PATH.as_posix()) as con:
        _ensure_events_table(con)
        if mode == "replace":
            con.execute("DELETE FROM events")
        elif mode != "append":
            raise ValueError(f"Unsupported load mode: {mode}")

        con.register("events_df_tmp", events_df)
        con.execute(
            """
            INSERT INTO events (user_id, timestamp, experiment_id, variant, event_name, value, region, device, age_bucket, channel, risk_segment)
            SELECT user_id, timestamp, experiment_id, variant, event_name, value, region, device, age_bucket, channel, risk_segment
            FROM events_df_tmp
            """
        )


def _load_csv_to_db(csv_path: Path, mode: str = "replace") -> None:
    events_df = pd.read_csv(csv_path)
    for col in UNIFIED_EVENT_COLUMNS:
        if col not in events_df.columns:
            events_df[col] = None
    events_df = events_df[UNIFIED_EVENT_COLUMNS].copy()
    events_df["timestamp"] = pd.to_datetime(events_df["timestamp"], errors="coerce")
    _load_df_to_db(events_df, mode=mode)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--sample":
        load_sample_data()
    elif len(sys.argv) > 1 and sys.argv[1] == "--hillstrom":
        load_hillstrom_to_events()
    elif len(sys.argv) > 1 and sys.argv[1] == "--health":
        load_health_journey_to_events()
    elif len(sys.argv) > 1 and sys.argv[1] == "--health-generate":
        load_health_journey_to_events(
            csv_path=HEALTH_JOURNEY_CSV,
            health_experiment_id="health_exp_reminder_30d",
            force_experiment_id=True,
        )
    elif len(sys.argv) > 1 and sys.argv[1] == "--all-domains":
        load_tech_and_health_events()
    else:
        print(
            "Usage: python -m experimentplatform.analytics.ingest_events "
            "[--sample | --hillstrom | --health | --health-generate | --all-domains]"
        )