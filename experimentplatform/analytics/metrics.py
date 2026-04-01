from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple
import logging
import math

import duckdb
import pandas as pd

from shared.schemas import ExperimentSpec

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "events.duckdb"
SUPPORTED_METRICS = {
    "conversion_rate",
    "revenue_per_user",
    "readmission_30d_rate",
    "followup_completion_rate",
    "length_of_stay",
}
SEGMENT_COLUMNS = {"region", "device", "age_bucket", "channel", "risk_segment"}
METRIC_HIGHER_IS_BETTER = {
    "conversion_rate": True,
    "revenue_per_user": True,
    "followup_completion_rate": True,
    "readmission_30d_rate": False,
    "length_of_stay": False,
}


class MetricsError(Exception):
    pass


@dataclass
class GroupStats:
    n: int
    mean: float


def _get_con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH))


def _to_sql_timestamp(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _normal_cdf(x: float) -> float:
    return NormalDist().cdf(x)


def _two_sided_p_from_z(z: float) -> float:
    return 2 * (1 - _normal_cdf(abs(z)))


def _validate_segment_by(segment_by: Optional[Sequence[str]]) -> List[str]:
    seg_cols = list(segment_by or [])
    bad = [c for c in seg_cols if c not in SEGMENT_COLUMNS]
    if bad:
        raise MetricsError(f"Unsupported segment_by columns: {bad}")
    return seg_cols


def _build_filters(
    experiment_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    segment_filters: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], List[Any]]:
    filters = ["experiment_id = ?"]
    params: List[Any] = [experiment_id]

    if start_time is not None:
        filters.append("timestamp >= ?")
        params.append(_to_sql_timestamp(start_time))
    if end_time is not None:
        filters.append("timestamp <= ?")
        params.append(_to_sql_timestamp(end_time))

    for col, value in (segment_filters or {}).items():
        if col not in SEGMENT_COLUMNS:
            raise MetricsError(f"Unsupported segment filter column: {col}")
        filters.append(f"{col} = ?")
        params.append(value)

    return filters, params


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
    segment_filters: Optional[Dict[str, Any]] = None,
    segment_by: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Return per-user pre/post metric table for experiment analysis."""
    seg_cols = _validate_segment_by(segment_by)
    filters, where_params = _build_filters(
        experiment_id=experiment_id,
        start_time=None,
        end_time=end_time,
        segment_filters=segment_filters,
    )
    where_clause = " AND ".join(filters)

    segment_select = ", ".join(seg_cols)
    segment_select_clause = f", {segment_select}" if segment_select else ""
    group_by_clause = f", {segment_select}" if segment_select else ""

    metric_params: List[Any] = []

    if metric in {"conversion_rate", "readmission_30d_rate", "followup_completion_rate"}:
        metric_event = {
            "conversion_rate": "conversion",
            "readmission_30d_rate": "readmission_30d",
            "followup_completion_rate": "followup_completed",
        }[metric]

        post_condition = f"event_name = '{metric_event}'"
        if start_time is not None:
            post_condition = f"{post_condition} AND timestamp >= ?"

        pre_condition = "FALSE"
        if start_time is not None:
            pre_condition = f"event_name = '{metric_event}' AND timestamp < ?"
            # Placeholder order in query: pre_condition appears before post_condition
            metric_params.append(_to_sql_timestamp(start_time))
            metric_params.append(_to_sql_timestamp(start_time))

        query = f"""
        SELECT
            user_id,
            variant
            {segment_select_clause},
            CASE WHEN SUM(CASE WHEN {pre_condition} THEN 1 ELSE 0 END) > 0 THEN 1 ELSE 0 END AS pre_metric,
            CASE WHEN SUM(CASE WHEN {post_condition} THEN 1 ELSE 0 END) > 0 THEN 1 ELSE 0 END AS post_metric
        FROM events
        WHERE {where_clause}
        GROUP BY user_id, variant{group_by_clause}
        """
    elif metric == "revenue_per_user":
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
            variant
            {segment_select_clause},
            SUM(CASE WHEN {pre_condition} THEN COALESCE(value, 0.0) ELSE 0.0 END) AS pre_metric,
            SUM(CASE WHEN {post_condition} THEN COALESCE(value, 0.0) ELSE 0.0 END) AS post_metric
        FROM events
        WHERE {where_clause}
        GROUP BY user_id, variant{group_by_clause}
        """
    elif metric == "length_of_stay":
        post_condition = "event_name = 'length_of_stay'"
        if start_time is not None:
            post_condition = f"{post_condition} AND timestamp >= ?"
            metric_params.append(_to_sql_timestamp(start_time))

        query = f"""
        SELECT
            user_id,
            variant
            {segment_select_clause},
            0.0 AS pre_metric,
            MAX(CASE WHEN {post_condition} THEN COALESCE(value, NULL) ELSE NULL END) AS post_metric
        FROM events
        WHERE {where_clause}
        GROUP BY user_id, variant{group_by_clause}
        """
    else:
        raise MetricsError(f"Unsupported metric: {metric}")

    df = con.execute(query, [*metric_params, *where_params]).fetch_df()
    if df.empty:
        raise MetricsError(f"No data for experiment_id={experiment_id}")
    return df


def _extract_event_slice(
    con: duckdb.DuckDBPyConnection,
    experiment_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    segment_filters: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    filters, params = _build_filters(
        experiment_id=experiment_id,
        start_time=start_time,
        end_time=end_time,
        segment_filters=segment_filters,
    )
    where_clause = " AND ".join(filters)
    query = f"""
    SELECT user_id, variant, event_name, value, region, device, age_bucket, channel, risk_segment
    FROM events
    WHERE {where_clause}
    """
    return con.execute(query, params).fetch_df()


def _compute_data_as_of(
    con: duckdb.DuckDBPyConnection,
    experiment_id: str,
    end_time: Optional[datetime] = None,
    segment_filters: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    filters, params = _build_filters(
        experiment_id=experiment_id,
        start_time=None,
        end_time=end_time,
        segment_filters=segment_filters,
    )
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
            experiment_id = row[0]
            domain = _infer_experiment_domain(experiment_id)
            experiments.append(
                {
                    "experiment_id": experiment_id,
                    "events_count": int(row[1]),
                    "users_count": int(row[2]),
                    "start_time": str(row[3]) if row[3] is not None else None,
                    "end_time": str(row[4]) if row[4] is not None else None,
                    "variants": sorted(variants),
                    "domain": domain,
                }
            )
        return experiments
    finally:
        con.close()


def _infer_experiment_domain(experiment_id: str) -> str:
    exp = (experiment_id or "").lower()
    # NOTE: assumption – to be validated
    # Domain is inferred from experiment naming convention for lightweight dashboard filtering.
    if any(token in exp for token in ["reminder", "health", "patient", "readmission"]):
        return "health"
    return "tech"


def _select_comparison_variants(
    variant_groups: Dict[str, Dict[str, float]],
    control_variant: Optional[str] = None,
    treatment_variant: Optional[str] = None,
    default_control_variant: Optional[str] = None,
    default_treatment_variant: Optional[str] = None,
) -> Tuple[str, Dict[str, float], str, Dict[str, float], str]:
    if bool(control_variant) ^ bool(treatment_variant):
        raise MetricsError("control_variant and treatment_variant must be provided together")

    if control_variant and treatment_variant:
        if control_variant not in variant_groups or treatment_variant not in variant_groups:
            raise MetricsError(
                f"Explicit variants not found. Available variants: {sorted(variant_groups.keys())}"
            )
        if control_variant == treatment_variant:
            raise MetricsError("control_variant and treatment_variant must differ")
        return (
            control_variant,
            variant_groups[control_variant],
            treatment_variant,
            variant_groups[treatment_variant],
            "explicit",
        )

    if bool(default_control_variant) ^ bool(default_treatment_variant):
        raise MetricsError(
            "ExperimentSpec default_control_variant and default_treatment_variant must be provided together"
        )

    if default_control_variant and default_treatment_variant:
        if (
            default_control_variant not in variant_groups
            or default_treatment_variant not in variant_groups
        ):
            raise MetricsError(
                "ExperimentSpec default variants not found. "
                f"Available variants: {sorted(variant_groups.keys())}"
            )
        if default_control_variant == default_treatment_variant:
            raise MetricsError(
                "ExperimentSpec default_control_variant and default_treatment_variant must differ"
            )
        return (
            default_control_variant,
            variant_groups[default_control_variant],
            default_treatment_variant,
            variant_groups[default_treatment_variant],
            "spec_default",
        )

    sorted_groups = sorted(variant_groups.items(), key=lambda x: x[1]["n"], reverse=True)
    control_name, control_data = sorted_groups[0]
    treatment_name, treatment_data = sorted_groups[1]
    return control_name, control_data, treatment_name, treatment_data, "auto"


def _contrast_stats(control_values: pd.Series, treatment_values: pd.Series) -> Dict[str, float]:
    control_n = len(control_values)
    treatment_n = len(treatment_values)
    control_mean = float(control_values.mean())
    treatment_mean = float(treatment_values.mean())
    control_var = float(control_values.var(ddof=1)) if control_n > 1 else 0.0
    treatment_var = float(treatment_values.var(ddof=1)) if treatment_n > 1 else 0.0

    diff = treatment_mean - control_mean
    se_diff = math.sqrt((control_var / max(control_n, 1)) + (treatment_var / max(treatment_n, 1)))
    z_975 = NormalDist().inv_cdf(0.975)
    ci_low = diff - z_975 * se_diff
    ci_high = diff + z_975 * se_diff

    # NOTE: stats choice – to be validated (normal approximation for all metric types)
    z_score = diff / se_diff if se_diff > 0 else 0.0
    p_value = _two_sided_p_from_z(z_score) if se_diff > 0 else 1.0
    significant = p_value < 0.05
    diff_pct = (diff / control_mean * 100) if control_mean != 0 else 0.0

    return {
        "diff": diff,
        "standard_error": se_diff,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "diff_pct": diff_pct,
        "p_value": p_value,
        "significant": significant,
    }


def compute_segment_p_values(
    user_df: pd.DataFrame,
    analysis_col: str,
    segment_by: Sequence[str],
    control_variant: str,
    treatment_variant: str,
) -> List[Dict[str, Any]]:
    """
    Compute per-segment p-values for a fixed control-treatment contrast.

    # NOTE: stats choice – to be validated
    # Uses normal-approximate p-values for speed/consistency with current API.
    """
    if not segment_by:
        return []

    rows: List[Dict[str, Any]] = []
    grouped = user_df.groupby(list(segment_by), dropna=False)
    for segment_vals, seg_df in grouped:
        if not isinstance(segment_vals, tuple):
            segment_vals = (segment_vals,)

        cvals = seg_df.loc[seg_df["variant"] == control_variant, analysis_col].astype(float)
        tvals = seg_df.loc[seg_df["variant"] == treatment_variant, analysis_col].astype(float)
        if len(cvals) < 2 or len(tvals) < 2:
            continue

        stats = _contrast_stats(cvals, tvals)
        rows.append(
            {
                "segment": {k: (None if pd.isna(v) else v) for k, v in zip(segment_by, segment_vals)},
                "control_n": int(len(cvals)),
                "treatment_n": int(len(tvals)),
                "diff": float(stats["diff"]),
                "p_value": float(stats["p_value"]),
                "ci_95": {"low": float(stats["ci_low"]), "high": float(stats["ci_high"])},
                "significant": bool(stats["significant"]),
            }
        )
    return rows


def apply_bh_fdr(segment_rows: List[Dict[str, Any]], alpha: float = 0.05) -> List[Dict[str, Any]]:
    """
    Apply Benjamini-Hochberg FDR adjustment to segment-level p-values.

    # TODO-stats: revisit FDR method choices (BH vs BY/hierarchical) for correlated segments.
    """
    if not segment_rows:
        return segment_rows

    indexed = sorted(enumerate(segment_rows), key=lambda x: x[1]["p_value"])
    m = len(indexed)
    passed_until = -1
    for rank, (_, row) in enumerate(indexed, start=1):
        if row["p_value"] <= (rank / m) * alpha:
            passed_until = rank

    bh_q_values = [1.0] * m
    running_min = 1.0
    for i in range(m - 1, -1, -1):
        rank = i + 1
        p = indexed[i][1]["p_value"]
        q = min(1.0, p * m / rank)
        running_min = min(running_min, q)
        bh_q_values[i] = running_min

    for i, (original_idx, row) in enumerate(indexed, start=1):
        row["bh_fdr_significant"] = i <= passed_until
        row["bh_q_value"] = float(bh_q_values[i - 1])
        segment_rows[original_idx] = row
    return segment_rows


def detect_srm(
    variant_counts: Dict[str, int],
    expected_split: Optional[Dict[str, float]] = None,
    alpha: float = 0.01,
) -> Dict[str, Any]:
    """
    Detect sample-ratio mismatch (SRM) using chi-square goodness-of-fit.

    # NOTE: stats choice – to be validated
    # Uses Wilson-Hilferty approximation for chi-square tail p-value (no scipy dependency).
    """
    total = sum(variant_counts.values())
    if total <= 0:
        return {
            "srm_flag": False,
            "srm_p_value": 1.0,
            "expected_split": {},
            "observed_split": {},
            "chi_square": 0.0,
            "degrees_of_freedom": 0,
        }

    variants = list(variant_counts.keys())
    if expected_split:
        split = {k: float(v) for k, v in expected_split.items() if k in variant_counts and v > 0}
        if not split:
            split = {k: 1 / len(variants) for k in variants}
    else:
        split = {k: 1 / len(variants) for k in variants}

    split_sum = sum(split.values())
    split = {k: v / split_sum for k, v in split.items()}
    for k in variants:
        split.setdefault(k, 0.0)

    chi_square = 0.0
    for variant in variants:
        expected_n = max(1e-12, split[variant] * total)
        observed_n = variant_counts[variant]
        chi_square += (observed_n - expected_n) ** 2 / expected_n

    df = max(1, len(variants) - 1)
    # NOTE: stats choice – to be validated (approximation to avoid SciPy dependency).
    z = ((chi_square / df) ** (1 / 3) - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))
    p_value = max(0.0, min(1.0, 1 - _normal_cdf(z)))
    observed_split = {k: variant_counts[k] / total for k in variants}

    return {
        "srm_flag": p_value < alpha,
        "srm_p_value": float(p_value),
        "expected_split": split,
        "observed_split": observed_split,
        "chi_square": float(chi_square),
        "degrees_of_freedom": int(df),
    }


def _outlier_count_iqr(values: pd.Series) -> int:
    if len(values) < 4:
        return 0
    q1 = float(values.quantile(0.25))
    q3 = float(values.quantile(0.75))
    iqr = q3 - q1
    if iqr <= 0:
        return 0
    # TODO-stats: revisit robust outlier thresholding and heavy-tail handling.
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    return int(((values < lo) | (values > hi)).sum())


def compute_data_quality(
    event_df: pd.DataFrame,
    user_df: pd.DataFrame,
    metric: str,
    analysis_col: str,
    expected_split: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Compute lightweight quality checks surfaced with metric results.

    # NOTE: stats choice – to be validated
    # duplicate_rate here treats repeated user-variant event rows as potential duplication signal.
    """
    if event_df.empty:
        missing_rate = 0.0
        duplicate_rate = 0.0
    else:
        if metric == "revenue_per_user":
            missing_rate = float(event_df["value"].isna().mean())
        else:
            missing_rate = float(user_df["post_metric"].isna().mean())

        unique_pairs = event_df[["user_id", "variant"]].drop_duplicates().shape[0]
        duplicate_rate = float((len(event_df) - unique_pairs) / len(event_df)) if len(event_df) else 0.0

    variant_counts = user_df.groupby("variant")["user_id"].nunique().to_dict()
    srm = detect_srm(variant_counts=variant_counts, expected_split=expected_split)
    outlier_counts = {metric: _outlier_count_iqr(user_df[analysis_col].astype(float)) if len(user_df) else 0}

    return {
        **srm,
        "missing_rate": missing_rate,
        "duplicate_rate": duplicate_rate,
        "outlier_counts": outlier_counts,
    }


def _compute_metric_contrast(
    con: duckdb.DuckDBPyConnection,
    experiment_id: str,
    metric: str,
    control_variant: str,
    treatment_variant: str,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    segment_filters: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if metric not in SUPPORTED_METRICS:
        return None

    user_df = _extract_user_metrics(
        con=con,
        experiment_id=experiment_id,
        metric=metric,
        start_time=start_time,
        end_time=end_time,
        segment_filters=segment_filters,
        segment_by=None,
    )
    cvals = user_df.loc[user_df["variant"] == control_variant, "post_metric"].astype(float)
    tvals = user_df.loc[user_df["variant"] == treatment_variant, "post_metric"].astype(float)
    if len(cvals) == 0 or len(tvals) == 0:
        return None

    stats = _contrast_stats(cvals, tvals)
    return {
        "metric": metric,
        "control_mean": float(cvals.mean()),
        "treatment_mean": float(tvals.mean()),
        "diff": float(stats["diff"]),
        "diff_pct": float(stats["diff_pct"]),
        "ci_95": {"low": float(stats["ci_low"]), "high": float(stats["ci_high"])},
        "p_value": float(stats["p_value"]),
    }


def evaluate_guardrails(
    primary_result: Dict[str, Any],
    primary_metric: str,
    guardrail_results: List[Dict[str, Any]],
    experiment_spec: Optional[ExperimentSpec],
    data_quality: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Produce recommendation from primary + guardrails + quality checks.

    # TODO-stats: refine decision policy and map to org/product risk appetite.
    """
    reasons: List[str] = []
    any_hard_guardrail_fail = False

    for g in guardrail_results:
        if g.get("status") == "fail":
            any_hard_guardrail_fail = True
            reasons.append(f"Guardrail {g['metric']} failed threshold")

    if data_quality.get("srm_flag"):
        reasons.append("SRM detected")
    if data_quality.get("missing_rate", 0.0) > 0.20:
        reasons.append("High missing outcome rate")

    meets_min_sample = True
    if experiment_spec is not None:
        total_n = primary_result["control"]["n"] + primary_result["treatment"]["n"]
        meets_min_sample = total_n >= experiment_spec.min_sample_size
        if not meets_min_sample:
            reasons.append("Below minimum sample size from experiment spec")

    higher_is_better = METRIC_HIGHER_IS_BETTER.get(primary_metric, True)
    primary_positive_sig = (
        (primary_result["diff"] > 0 if higher_is_better else primary_result["diff"] < 0)
        and primary_result["significant"]
    )
    # NOTE: synthetic assumption – to be validated
    # Metric direction is hardcoded in METRIC_HIGHER_IS_BETTER for lightweight policy logic.

    # NOTE: stats choice – to be validated (simple rules-based recommendation tiering).
    if data_quality.get("srm_flag") or any_hard_guardrail_fail:
        recommendation = "stop"
    elif primary_positive_sig and meets_min_sample and not reasons:
        recommendation = "ship"
    else:
        recommendation = "hold"

    return {
        "recommendation": recommendation,
        "rationale": reasons,
        "primary_positive_significant": primary_positive_sig,
        "meets_min_sample_size": meets_min_sample,
    }


def compute_ab_metric(
    experiment_id: str,
    metric: Literal["conversion_rate", "revenue_per_user"],
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    use_cuped: bool = False,
    segment_filters: Optional[Dict[str, Any]] = None,
    segment_by: Optional[Sequence[str]] = None,
    control_variant: Optional[str] = None,
    treatment_variant: Optional[str] = None,
    experiment_spec: Optional[ExperimentSpec] = None,
) -> Dict[str, Any]:
    if metric not in SUPPORTED_METRICS:
        raise MetricsError(f"Unsupported metric: {metric}")

    seg_cols = _validate_segment_by(segment_by)
    con = _get_con()
    try:
        user_df = _extract_user_metrics(
            con=con,
            experiment_id=experiment_id,
            metric=metric,
            start_time=start_time,
            end_time=end_time,
            segment_filters=segment_filters,
            segment_by=seg_cols,
        )
        event_df = _extract_event_slice(
            con=con,
            experiment_id=experiment_id,
            start_time=start_time,
            end_time=end_time,
            segment_filters=segment_filters,
        )

        cuped_meta: Dict[str, Any] = {
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

        grouped = user_df.groupby("variant", as_index=False).agg(n=(analysis_col, "size"), mean=(analysis_col, "mean"))
        variant_groups: Dict[str, Dict[str, float]] = {}
        for _, row in grouped.iterrows():
            variant_groups[row["variant"]] = {"n": int(row["n"]), "mean": float(row["mean"])}

        if len(variant_groups) < 2:
            raise MetricsError(f"Need ≥2 variants for experiment_id={experiment_id}, got {len(variant_groups)}")

        control_name, control_data, treatment_name, treatment_data, comparison_mode = _select_comparison_variants(
            variant_groups=variant_groups,
            control_variant=control_variant,
            treatment_variant=treatment_variant,
            default_control_variant=experiment_spec.default_control_variant if experiment_spec else None,
            default_treatment_variant=experiment_spec.default_treatment_variant if experiment_spec else None,
        )
        logger.info("Compared %s vs %s", control_name, treatment_name)

        control_values = user_df.loc[user_df["variant"] == control_name, analysis_col].astype(float)
        treatment_values = user_df.loc[user_df["variant"] == treatment_name, analysis_col].astype(float)
        stats = _contrast_stats(control_values, treatment_values)

        segment_rows = compute_segment_p_values(
            user_df=user_df,
            analysis_col=analysis_col,
            segment_by=seg_cols,
            control_variant=control_name,
            treatment_variant=treatment_name,
        )
        segment_rows = apply_bh_fdr(segment_rows)

        data_quality = compute_data_quality(
            event_df=event_df,
            user_df=user_df,
            metric=metric,
            analysis_col=analysis_col,
            expected_split=experiment_spec.expected_split if experiment_spec else None,
        )

        guardrail_results: List[Dict[str, Any]] = []
        if experiment_spec is not None:
            for guardrail in experiment_spec.guardrail_metrics:
                g = _compute_metric_contrast(
                    con=con,
                    experiment_id=experiment_id,
                    metric=guardrail.name,
                    control_variant=control_name,
                    treatment_variant=treatment_name,
                    start_time=start_time,
                    end_time=end_time,
                    segment_filters=segment_filters,
                )
                if g is None:
                    continue

                diff_ratio = g["diff_pct"] / 100.0
                status = "pass"
                threshold_reason = None
                if guardrail.min_value is not None and diff_ratio < guardrail.min_value:
                    status = "fail"
                    threshold_reason = f"below min_value={guardrail.min_value}"
                if guardrail.max_value is not None and diff_ratio > guardrail.max_value:
                    status = "fail"
                    threshold_reason = f"above max_value={guardrail.max_value}"

                # NOTE: stats choice – to be validated (thresholds evaluated on point estimate).
                guardrail_results.append(
                    {
                        **g,
                        "status": status,
                        "threshold_reason": threshold_reason,
                        "thresholds": {
                            "min_value": guardrail.min_value,
                            "max_value": guardrail.max_value,
                        },
                    }
                )

        primary_payload = {
            "control": control_data,
            "treatment": treatment_data,
            "diff": float(stats["diff"]),
            "standard_error": float(stats["standard_error"]),
            "ci_95": {"low": float(stats["ci_low"]), "high": float(stats["ci_high"])},
            "diff_pct": round(float(stats["diff_pct"]), 2),
            "p_value": float(stats["p_value"]),
            "significant": bool(stats["significant"]),
        }

        recommendation_details = evaluate_guardrails(
            primary_result=primary_payload,
            primary_metric=metric,
            guardrail_results=guardrail_results,
            experiment_spec=experiment_spec,
            data_quality=data_quality,
        )

        data_as_of = _compute_data_as_of(
            con=con,
            experiment_id=experiment_id,
            end_time=end_time,
            segment_filters=segment_filters,
        )

        return {
            "experiment_id": experiment_id,
            "metric": metric,
            **primary_payload,
            "all_groups": variant_groups,
            "comparison_control_variant": control_name,
            "comparison_treatment_variant": treatment_name,
            "comparison_mode": comparison_mode,
            "segment_results": segment_rows,
            "cuped": cuped_meta,
            "data_as_of": data_as_of,
            "data_quality": data_quality,
            "guardrail_results": guardrail_results,
            "recommendation": recommendation_details["recommendation"],
            "recommendation_details": recommendation_details,
        }
    finally:
        con.close()


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
    return math.ceil(2 * n_per_group)