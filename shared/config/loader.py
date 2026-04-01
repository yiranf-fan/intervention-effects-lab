from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from shared.schemas import ExperimentSpec


ROOT_DIR = Path(__file__).resolve().parents[2]
EXPERIMENTS_CONFIG_PATH = ROOT_DIR / "experimentplatform" / "config" / "experiments.yaml"
METRICS_CONFIG_PATH = ROOT_DIR / "shared" / "config" / "metrics.yaml"


class ConfigError(Exception):
    pass


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    if not isinstance(payload, dict):
        raise ConfigError(f"Config payload must be a mapping: {path}")
    return payload


@lru_cache(maxsize=1)
def load_experiment_specs() -> Dict[str, ExperimentSpec]:
    payload = _read_yaml(EXPERIMENTS_CONFIG_PATH)
    rows = payload.get("experiments", [])
    if not isinstance(rows, list):
        raise ConfigError("experiments.yaml must include list field `experiments`")

    specs: Dict[str, ExperimentSpec] = {}
    for row in rows:
        spec = ExperimentSpec.model_validate(row)
        specs[spec.experiment_id] = spec
    return specs


def get_experiment_spec(experiment_id: str) -> Optional[ExperimentSpec]:
    return load_experiment_specs().get(experiment_id)


@lru_cache(maxsize=1)
def load_metric_registry() -> Dict[str, Dict[str, Any]]:
    payload = _read_yaml(METRICS_CONFIG_PATH)
    rows = payload.get("metrics", [])
    if not isinstance(rows, list):
        raise ConfigError("metrics.yaml must include list field `metrics`")

    by_name: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        name = row.get("name")
        if not name or not isinstance(name, str):
            raise ConfigError("Each metric registry entry needs a string `name`")
        by_name[name] = row
    return by_name


def validate_metric_in_registry(metric_name: str) -> Dict[str, Any]:
    registry = load_metric_registry()
    if metric_name not in registry:
        raise ConfigError(f"Metric `{metric_name}` missing from metric registry")
    return registry[metric_name]
