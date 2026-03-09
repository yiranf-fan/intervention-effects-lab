from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import NormalDist
from typing import Dict, List, Literal, Optional
import math
import duckdb
import logging
import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "events.duckdb"
SUPPORTED_METRICS = {"conversion_rate", "revenue_per_user"}

class MetricsError(Exception):
    pass

@dataclass
class GroupStats:
    n: int
    mean: float

def _get_con():
    return duckdb.connect(str(DB_PATH))


def _to_sql_timestamp(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def cuped_adjustment(pre_metric: pd.Series, post_metric: pd.Series) -> Dict[str, object]:
    """Apply CUPED adjustment and return adjusted series with diagnostics.

    CUPED: Y_adj = Y - theta * (X - mean(X)), theta = cov(X, Y) / var(X)
    """
    if len(pre_metric) != len(post_metric):
        raise MetricsError("CUPED requires equal-length pre and post metric vectors")

    x = pre_metric.astype(float)
    y = post_metric.astype(float)

    x_var = float(x.var(ddof=1)) if len(x) > 1 else 0.0
    y_var = float(y.var(ddof=1)) if len(y) > 1 else 0.0

    if x_var <= 0:
        return {
            "adjusted": y,
            "theta": 0.0,
            "variance_reduction_pct": 0.0,
            "applied": False,
            "reason": "pre_metric has zero variance",
        }

    cov_xy = float(x.cov(y))
    theta = cov_xy / x_var
    y_adj = y - theta * (x - float(x.mean()))
    y_adj_var = float(y_adj.var(ddof=1)) if len(y_adj) > 1 else 0.0

    var_reduction = 0.0
    if y_var > 0:
        var_reduction = max(0.0, (y_var - y_adj_var) / y_var * 100)

    return {
        "adjusted": y_adj,
        "theta": theta,
        "variance_reduction_pct": var_reduction,
        "applied": True,
        "reason": None,
    }


def _extract_user_metrics(
    con: duckdb.DuckDBPyConnection,
    experiment_id: str,
    metric: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> pd.DataFrame:
    """Return per-user pre/post metric table for experiment analysis."""
    filters = ["experiment_id = ?"]
    where_params = [experiment_id]

    if end_time is not None:
        filters.append("timestamp <= ?")
        where_params.append(_to_sql_timestamp(end_time))

    where_clause = " AND ".join(filters)

    if metric == "conversion_rate":
        metric_params = []
        post_condition = "event_name = 'conversion'"
        if start_time is not None:
            post_condition = f"{post_condition} AND timestamp >= ?"

        pre_condition = "FALSE"
        if start_time is not None:
            pre_condition = "event_name = 'conversion' AND timestamp < ?"
            # Placeholder order in query: pre_condition appears before post_condition
            metric_params.append(_to_sql_timestamp(start_time))
            metric_params.append(_to_sql_timestamp(start_time))

        query = f"""
        SELECT
            user_id,
            variant,
            CASE WHEN SUM(CASE WHEN {pre_condition} THEN 1 ELSE 0 END) > 0 THEN 1 ELSE 0 END AS pre_metric,
            CASE WHEN SUM(CASE WHEN {post_condition} THEN 1 ELSE 0 END) > 0 THEN 1 ELSE 0 END AS post_metric
        FROM events
        WHERE {where_clause}
        GROUP BY user_id, variant
        """
    elif metric == "revenue_per_user":
        metric_params = []
        post_condition = "TRUE"
        if start_time is not None:
            post_condition = "timestamp >= ?"

        pre_condition = "FALSE"
        if start_time is not None:
            pre_condition = "timestamp < ?"
            # Placeholder order in query: pre_condition appears before post_condition
            metric_params.append(_to_sql_timestamp(start_time))
            metric_params.append(_to_sql_timestamp(start_time))

        query = f"""
        SELECT
            user_id,
            variant,
            SUM(CASE WHEN {pre_condition} THEN COALESCE(value, 0.0) ELSE 0.0 END) AS pre_metric,
            SUM(CASE WHEN {post_condition} THEN COALESCE(value, 0.0) ELSE 0.0 END) AS post_metric
        FROM events
        WHERE {where_clause}
        GROUP BY user_id, variant
        """
    else:
        raise MetricsError(f"Unsupported metric: {metric}")

    df = con.execute(query, [*metric_params, *where_params]).fetch_df()
    if df.empty:
        raise MetricsError(f"No data for experiment_id={experiment_id}")

    return df


def _compute_data_as_of(
    con: duckdb.DuckDBPyConnection,
    experiment_id: str,
    end_time: Optional[datetime] = None,
) -> Optional[str]:
    filters = ["experiment_id = ?"]
    params = [experiment_id]
    if end_time is not None:
        filters.append("timestamp <= ?")
        params.append(_to_sql_timestamp(end_time))

    query = f"SELECT MAX(timestamp) AS data_as_of FROM events WHERE {' AND '.join(filters)}"
    row = con.execute(query, params).fetchone()
    value = row[0] if row else None
    return str(value) if value is not None else None


def list_experiments() -> List[Dict[str, object]]:
    con = _get_con()
    try:
        rows = con.execute(
            """
            SELECT
                experiment_id,
                COUNT(*) AS events_count,
                COUNT(DISTINCT user_id) AS users_count,
                MIN(timestamp) AS start_time,
                MAX(timestamp) AS end_time,
                LIST(DISTINCT variant) AS variants
            FROM events
            WHERE experiment_id IS NOT NULL
            GROUP BY experiment_id
            ORDER BY experiment_id
            """
        ).fetchall()

        experiments: List[Dict[str, object]] = []
        for row in rows:
            variants = [v for v in (row[5] or []) if v is not None]
            experiments.append(
                {
                    "experiment_id": row[0],
                    "events_count": int(row[1]),
                    "users_count": int(row[2]),
                    "start_time": str(row[3]) if row[3] is not None else None,
                    "end_time": str(row[4]) if row[4] is not None else None,
                    "variants": sorted(variants),
                }
            )
        return experiments
    finally:
        con.close()

def compute_ab_metric(
    experiment_id: str,
    metric: Literal["conversion_rate", "revenue_per_user"],
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    use_cuped: bool = False,
) -> Dict[str, Dict]:
    
    # error handling for unsupported metrics
    if metric not in SUPPORTED_METRICS:
        raise MetricsError(f"Unsupported metric: {metric}")
    
    con = _get_con()
    user_df = _extract_user_metrics(
        con=con,
        experiment_id=experiment_id,
        metric=metric,
        start_time=start_time,
        end_time=end_time,
    )

    cuped_meta: Dict[str, object] = {
        "requested": use_cuped,
        "applied": False,
        "theta": 0.0,
        "variance_reduction_pct": 0.0,
        "reason": None,
    }

    analysis_col = "post_metric"
    if use_cuped:
        cuped_result = cuped_adjustment(user_df["pre_metric"], user_df["post_metric"])
        user_df["metric_for_analysis"] = cuped_result["adjusted"]
        analysis_col = "metric_for_analysis"
        cuped_meta = {
            "requested": True,
            "applied": bool(cuped_result["applied"]),
            "theta": round(float(cuped_result["theta"]), 6),
            "variance_reduction_pct": round(float(cuped_result["variance_reduction_pct"]), 2),
            "reason": cuped_result["reason"],
        }

    df = (
        user_df.groupby("variant", as_index=False)
        .agg(n=(analysis_col, "size"), mean=(analysis_col, "mean"))
    )

    data_as_of = _compute_data_as_of(con, experiment_id=experiment_id, end_time=end_time)
    con.close()

    variant_groups: Dict[str, Dict[str, float]] = {}
    for _, row in df.iterrows():
        variant_groups[row["variant"]] = {
            "n": int(row["n"]),
            "mean": float(row["mean"]),
        }

    if len(variant_groups) < 2:
        raise MetricsError(f"Need ≥2 variants for experiment_id={experiment_id}, got {len(variant_groups)}")

    # Auto-select: largest 2 groups for now; later on allow users to select control/treatment arm 
    # or handle more than 2 variants with multiple comparisons adjustments
    sorted_groups = sorted(variant_groups.items(), key=lambda x: x[1]["n"], reverse=True)
    control_name, control_data = sorted_groups[0]
    treatment_name, treatment_data = sorted_groups[1]

    logger.info(f"Compared {control_name}(n={control_data['n']}) vs {treatment_name}(n={treatment_data['n']})")

    control_values = user_df.loc[user_df["variant"] == control_name, analysis_col].astype(float)
    treatment_values = user_df.loc[user_df["variant"] == treatment_name, analysis_col].astype(float)

    control_var = float(control_values.var(ddof=1)) if len(control_values) > 1 else 0.0
    treatment_var = float(treatment_values.var(ddof=1)) if len(treatment_values) > 1 else 0.0

    diff = treatment_data["mean"] - control_data["mean"]
    se_diff = math.sqrt((control_var / control_data["n"]) + (treatment_var / treatment_data["n"]))
    z_975 = NormalDist().inv_cdf(0.975)
    ci_low = diff - z_975 * se_diff
    ci_high = diff + z_975 * se_diff
    diff_pct = (diff / control_data["mean"] * 100) if control_data["mean"] > 0 else 0

    return {
        "experiment_id": experiment_id,
        "metric": metric,
        "control": control_data,
        "treatment": treatment_data,
        "diff": diff,
        "standard_error": se_diff,
        "ci_95": {
            "low": ci_low,
            "high": ci_high,
        },
        "diff_pct": round(diff_pct, 2),
        "all_groups": variant_groups,
        "cuped": cuped_meta,
        "data_as_of": data_as_of,
    }


def required_sample_size(
    baseline_rate: float,
    target_effect: float,
    alpha: float = 0.05,
    power: float = 0.8,
) -> int:
    if not (0 < baseline_rate < 1):
        raise MetricsError("baseline_rate must be in (0, 1)")
    if target_effect <= 0:
        raise MetricsError("target_effect must be > 0")
    if not (0 < alpha < 1):
        raise MetricsError("alpha must be in (0, 1)")
    if not (0 < power < 1):
        raise MetricsError("power must be in (0, 1)")

    # Z values for two-sided test
    normal = NormalDist()
    z_alpha = normal.inv_cdf(1 - alpha / 2)
    z_beta = normal.inv_cdf(power)

    p1 = baseline_rate
    p2 = baseline_rate + target_effect
    if not (0 < p2 < 1):
        raise MetricsError("baseline_rate + target_effect must be in (0, 1)")
    p_bar = (p1 + p2) / 2

    se = math.sqrt(2 * p_bar * (1 - p_bar))
    num = (z_alpha + z_beta) * se
    n_per_group = (num / target_effect) ** 2
    return math.ceil(2 * n_per_group)  # total across groups