from pathlib import Path
import duckdb
import pandas as pd


DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "events.duckdb"
RAW_DIR = DATA_DIR / "raw"
HILLSTROM_CSV = RAW_DIR / "hillstrom_events.csv"
SAMPLE_CSV = RAW_DIR / "clickstream_sample.csv"


def load_sample_data(csv_path: Path = RAW_DIR / "clickstream_sample.csv") -> None:
    RAW_DIR.mkdir(exist_ok=True, parents=True)
    
    if not csv_path.exists():
        raise FileNotFoundError(f"Sample CSV not found: {csv_path}")
    
    _load_csv_to_db(csv_path)
    
    con = duckdb.connect(DB_PATH.as_posix())
    row_count = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    con.close()
    
    print(f"Loaded {row_count} sample events from {csv_path}")


def load_hillstrom_to_events() -> None:
    RAW_DIR.mkdir(exist_ok=True, parents=True)
    hillstrom_csv = RAW_DIR / "hillstrom_events.csv"
    
    # Skip download if CSV already exists
    if hillstrom_csv.exists():
        print(f"Reusing existing Hillstrom CSV: {hillstrom_csv}")
    else:
        print("Downloading Hillstrom dataset...")
        url = "http://www.minethatdata.com/Kevin_Hillstrom_MineThatData_E-MailAnalytics_DataMiningChallenge_2008.03.20.csv"
        df_customers = pd.read_csv(url)
        
        # Transform to events
        events = []
        for idx, row in df_customers.iterrows():
            events.append({
                "user_id": f"cust_{idx}",
                "timestamp": pd.Timestamp("2024-01-01"),
                "experiment_id": "exp_email",
                "variant": row["segment"],
                "event_name": "visit",
                "value": None,
            })
            if row["visit"] == 1:
                events.append({
                    "user_id": f"cust_{idx}",
                    "timestamp": pd.Timestamp("2024-01-01T01:00:00"),
                    "experiment_id": "exp_email",
                    "variant": row["segment"],
                    "event_name": "conversion",
                    "value": row["spend"],
                })

        events_df = pd.DataFrame(events)
        events_df.to_csv(hillstrom_csv, index=False)
        print(f"Downloaded + saved {len(events_df)} events to {hillstrom_csv}")
    
    # Always reload fresh to DuckDB
    _load_csv_to_db(hillstrom_csv)
    row_count = duckdb.connect(DB_PATH.as_posix()).execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"Loaded {row_count} Hillstrom events to DuckDB")


def _load_csv_to_db(csv_path: Path) -> None:
    con = duckdb.connect(DB_PATH.as_posix())
    csv_posix = csv_path.as_posix()
    
    con.execute(f"CREATE TABLE IF NOT EXISTS events AS SELECT * FROM read_csv_auto('{csv_posix}') LIMIT 0")
    con.execute("DELETE FROM events")
    con.execute(f"INSERT INTO events SELECT * FROM read_csv_auto('{csv_posix}')")
    con.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--sample":
        load_sample_data()
    elif len(sys.argv) > 1 and sys.argv[1] == "--hillstrom":
        load_hillstrom_to_events()
    else:
        print("Usage: python -m experimentplatform.analytics.ingest_clickstream [--sample | --hillstrom]")