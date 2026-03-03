from dataclasses import dataclass
from typing import Dict, Literal
import math
import duckdb
import logging

logger = logging.getLogger(__name__)

DB_PATH = "data/events.duckdb"
SUPPORTED_METRICS = {"conversion_rate", "revenue_per_user"}

class MetricsError(Exception):
    pass

@dataclass
class GroupStats:
    n: int
    mean: float

def _get_con():
    return duckdb.connect(DB_PATH)

def compute_ab_metric(
    experiment_id: str,
    metric: Literal["conversion_rate", "revenue_per_user"],
) -> Dict[str, Dict]:
    
    # error handling for unsupported metrics
    if metric not in SUPPORTED_METRICS:
        raise MetricsError(f"Unsupported metric: {metric}")
    
    con = _get_con()
    if metric == "conversion_rate":
        query = """
        WITH user_conv AS (
            SELECT
                user_id,
                variant,
                /* 1 if user has any conversion event */
                CASE
                    WHEN SUM(CASE WHEN event_name = 'conversion' THEN 1 ELSE 0 END) > 0
                    THEN 1 ELSE 0
                END AS converted
            FROM events
            WHERE experiment_id = ?
            GROUP BY user_id, variant
        )
        SELECT
            variant,
            COUNT(*) AS n,
            AVG(converted) AS mean
        FROM user_conv
        GROUP BY variant
        """
    elif metric == "revenue_per_user":
        query = """
        WITH user_rev AS (
            SELECT user_id,
                   SUM(COALESCE(value, 0.0)) AS revenue,
                   variant
            FROM events
            WHERE experiment_id = ?
            GROUP BY user_id, variant
        )
        SELECT variant,
               COUNT(*) AS n,
               AVG(revenue) AS mean
        FROM user_rev
        GROUP BY variant
        """

    df = con.execute(query, [experiment_id]).fetch_df()
    con.close()

    if df.empty:
        raise MetricsError(f"No data for experiment_id={experiment_id}")

    variant_groups: Dict[str, Dict[str, float]] = {}
    for _, row in df.iterrows():
        variant_groups[row["variant"]] = {
            "n": int(row["n"]),
            "mean": float(row["mean"]),
        }

    if len(variant_groups) < 2:
        raise MetricsError(f"Need ≥2 variants for experiment_id={experiment_id}, got {len(variant_groups)}")

    # Auto-select: largest 2 groups (handles 3-arm Hillstrom)
    sorted_groups = sorted(variant_groups.items(), key=lambda x: x[1]["n"], reverse=True)
    control_name, control_data = sorted_groups[0]
    treatment_name, treatment_data = sorted_groups[1]

    logger.info(f"Compared {control_name}(n={control_data['n']}) vs {treatment_name}(n={treatment_data['n']})")

    diff = treatment_data["mean"] - control_data["mean"]
    diff_pct = (diff / control_data["mean"] * 100) if control_data["mean"] > 0 else 0

    return {
        "experiment_id": experiment_id,
        "metric": metric,
        "control": control_data,
        "treatment": treatment_data,
        "diff": diff,
        "diff_pct": round(diff_pct, 2),
        "all_groups": variant_groups,
    }


def required_sample_size(
    baseline_rate: float,
    target_effect: float,
    alpha: float = 0.05,
    power: float = 0.8,
) -> int:

    # Z values for two-sided test (approx)
    z_alpha = 1.96  # alpha=0.05
    z_beta = 0.84   # power=0.8

    p1 = baseline_rate
    p2 = baseline_rate + target_effect
    p_bar = (p1 + p2) / 2

    se = math.sqrt(2 * p_bar * (1 - p_bar))
    num = (z_alpha + z_beta) * se
    n_per_group = (num / target_effect) ** 2
    return math.ceil(2 * n_per_group)  # total across groups