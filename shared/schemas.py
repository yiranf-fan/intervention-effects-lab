from datetime import datetime
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field

class Event(BaseModel):
    user_id: str
    timestamp: datetime
    experiment_id: Optional[str] = None
    variant: Optional[str] = None  # "control", "treatmentA", etc.
    event_name: str  # "pageview", "purchase", "adherence", etc.
    value: Optional[float] = None  # revenue, time, etc.

    # Segments (used more in later weeks but define now)
    channel: Optional[str] = None
    device: Optional[str] = None
    region: Optional[str] = None
    age_bucket: Optional[str] = None
    risk_segment: Optional[str] = None

class Experiment(BaseModel):
    experiment_id: str
    name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    description: Optional[str] = None

class MetricRequest(BaseModel):
    experiment_id: str
    metric: str  # e.g. "conversion_rate", "revenue_per_user"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    use_cuped: bool = False
    segment_filters: Optional[Dict[str, Any]] = None  # e.g. {"region": "NA"}
    segment_by: Optional[List[str]] = None  # e.g. ["region", "device"]
    control_variant: Optional[str] = None
    treatment_variant: Optional[str] = None


class GuardrailMetricSpec(BaseModel):
    name: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    description: Optional[str] = None


class ExperimentSpec(BaseModel):
    experiment_id: str
    domain: Optional[str] = None
    owner: str
    hypothesis: str
    primary_metric: str
    guardrail_metrics: List[GuardrailMetricSpec] = Field(default_factory=list)
    min_run_time_days: int
    min_sample_size: int
    stopping_rule_note: Optional[str] = None
    expected_split: Optional[Dict[str, float]] = None
    default_control_variant: Optional[str] = None
    default_treatment_variant: Optional[str] = None
