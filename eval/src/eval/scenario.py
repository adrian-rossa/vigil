"""Scenario definitions for the vigil eval framework."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class HealthProbe(BaseModel):
    kind: Literal["http_ok", "kube_condition", "host_cmd"]
    window_s: int = 300
    url: str | None = None
    expect: str | int | None = None
    resource: str | None = None
    namespace: str | None = None
    host: str | None = None
    command: str | None = None


class FaultSpec(BaseModel):
    id: str
    component: str
    severity: Literal["user_facing", "internal"] = "user_facing"
    health_probe: HealthProbe
    inject_params: dict[str, Any] = Field(default_factory=dict)
    alert_name: str | None = None


class WorkloadSpec(BaseModel):
    enabled: bool = False
    mode: Literal["stepped", "locust"] = "stepped"
    profile: list[int] = Field(default_factory=lambda: [50,200,50])
    ramp_s: int = 10


class NoiseSpec(BaseModel):
    enabled: bool = False
    schedule_s: int = 300
    duration_s: int = 120
    patterns: int = 2
    kinds: list[str] = Field(default_factory=lambda: ["pod_delete","cpu_stress"])
    target: str = "unrelated"


class ScenarioDefinition(BaseModel):
    id: str
    name: str
    layer: Literal["k8s", "os", "cross", "boundary"]
    root_cause_component: str
    expected_action: str
    expected_resolution_path: str
    expected_outcome: str | None = None
    expected_drift_classification: (
        Literal["live_only_drift", "declared_drift", "both_drift", "no_drift"] | None
    ) = None
    alert_name: str
    inject_params: dict[str, Any] = Field(default_factory=dict)
    forbidden_actions: list[str] = Field(default_factory=list)
    relationship: Literal["single", "independent", "cascading"] | None = None
    detection: Literal["alert", "synthetic"] = "synthetic"
    faults: list[FaultSpec] = Field(default_factory=list)
    workload: WorkloadSpec = Field(default_factory=WorkloadSpec)
    noise: NoiseSpec = Field(default_factory=NoiseSpec)

def load_scenarios(scenarios_dir: Path) -> list[ScenarioDefinition]:
    """Load every scenarios/{id}/scenario.yaml into a ScenarioDefinition."""
    scenarios: list[ScenarioDefinition] = []
    for yaml_path in sorted(scenarios_dir.glob("*/scenario.yaml")):
        with yaml_path.open() as f:
            data = yaml.safe_load(f)
        scenarios.append(ScenarioDefinition.model_validate(data))
    return scenarios
