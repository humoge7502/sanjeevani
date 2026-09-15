"""Configuration and path resolution.

Everything the system needs to know about *where* it runs and *what its
thresholds are* is resolved here, once. Modules never hardcode thresholds; they
read them from this module so every number in the UI is traceable to a config
key (the API surfaces those keys as evidence).

Offline-first: no configuration value causes a network call.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# backend/config.py -> backend/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
RUNTIME_DIR = REPO_ROOT / "runtime"
DOCS_DIR = REPO_ROOT / "docs"
FRONTEND_DIR = REPO_ROOT / "frontend"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():  # pragma: no cover - configuration error path
        raise FileNotFoundError(
            f"Required configuration file missing: {path}. "
            "Run from the repository root or restore the file."
        )
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@functools.lru_cache(maxsize=None)
def settings() -> dict[str, Any]:
    """Runtime settings (config/settings.yaml). Cached; call `reload()` in tests."""
    return _read_yaml(CONFIG_DIR / "settings.yaml")


@functools.lru_cache(maxsize=None)
def policies() -> dict[str, Any]:
    """The policy rulebook (config/policies.yaml)."""
    return _read_yaml(CONFIG_DIR / "policies.yaml")


@functools.lru_cache(maxsize=None)
def scenarios_config() -> dict[str, Any]:
    """Horizons, markets, approvers and the disruption library."""
    return _read_yaml(CONFIG_DIR / "scenarios.yaml")


@functools.lru_cache(maxsize=None)
def m2_fixture() -> dict[str, Any]:
    """The M2 RecoveryPlan fixture (see docs/mock-boundaries.md)."""
    return _read_yaml(REPO_ROOT / "backend" / "m2" / "fixtures" / "recovery_plans.yaml")


def reload_config() -> None:
    """Drop cached config. Used by the demo reset endpoint and by tests."""
    settings.cache_clear()
    policies.cache_clear()
    scenarios_config.cache_clear()
    m2_fixture.cache_clear()


def scenario_clock() -> datetime:
    """The scenario's 'now'.

    In demo mode the clock is FROZEN so timestamps and therefore audit hashes are
    reproducible across runs. Freezing the clock is a deliberate determinism
    decision, not a bug.
    """
    cfg = settings()
    frozen = cfg["meta"]["scenario_clock_utc"]
    if cfg["runtime"].get("freeze_clock", True):
        return datetime.fromisoformat(frozen.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    """Canonical ISO-8601 UTC string used in every contract field."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Thresholds:
    """Typed view over the hot thresholds so callers cannot typo a key."""

    escalate_confidence: float
    min_source_classes: int
    max_signal_age_hours: float
    dedup_window_hours: float
    min_source_reliability: float
    service_recovery_days: float
    horizons_days: tuple[int, ...]

    @classmethod
    def load(cls) -> "Thresholds":
        cfg = settings()
        return cls(
            escalate_confidence=cfg["sensing"]["escalate_confidence"],
            min_source_classes=cfg["verification"]["min_source_classes"],
            max_signal_age_hours=cfg["verification"]["max_signal_age_hours"],
            dedup_window_hours=cfg["verification"]["dedup_window_hours"],
            min_source_reliability=cfg["verification"]["min_source_reliability"],
            service_recovery_days=cfg["impact"]["service_recovery_days"],
            horizons_days=tuple(cfg["scenario"]["horizons_days"]),
        )


def ensure_runtime_dir() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR
