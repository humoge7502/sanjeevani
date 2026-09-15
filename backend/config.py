"""Configuration and path resolution.

Everything the system needs to know about *where* it runs and *what its
thresholds are* is resolved here, once. Modules never hardcode thresholds; they
read them from this module so every number in the UI is traceable to a config
key (the API surfaces those keys as evidence).

Offline-first: no configuration value causes a network call.

DEPLOYMENT. `config/*.yaml` stays the source of truth for *thresholds* — the
numbers a domain expert should be able to audit and diff. A small, deliberately
limited set of *deployment* knobs can be overridden by environment variable, so
the same image runs on a laptop, in a container and behind a proxy without
editing files baked into the image. Those overrides are applied in one place
(`_apply_env_overrides`) and listed in `.env.example` and docs/deployment.md.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml


def _env_path(name: str, default: Path) -> Path:
    """Resolve a directory from the environment, falling back to the default."""
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


# backend/config.py -> backend/ -> repo root
REPO_ROOT: Final[Path] = _env_path(
    "SANJEEVANI_REPO_ROOT", Path(__file__).resolve().parent.parent
)
CONFIG_DIR: Final[Path] = REPO_ROOT / "config"
DATA_DIR: Final[Path] = REPO_ROOT / "data"
# Overridable so the append-only ledger can live on a mounted volume rather than
# inside the image, which is what a container deployment needs.
RUNTIME_DIR: Final[Path] = _env_path("SANJEEVANI_RUNTIME_DIR", REPO_ROOT / "runtime")
# Not a DOCS_DIR: nothing in the running application reads docs/, which is why
# the deployment image excludes that directory entirely. Documenting the code is
# not the same as the code depending on the documentation.
FRONTEND_DIR: Final[Path] = REPO_ROOT / "frontend"

#: Built frontend assets. When this directory exists the API serves it, so the
#: whole product is one deployable unit with no separate web server.
FRONTEND_DIST: Final[Path] = _env_path(
    "SANJEEVANI_FRONTEND_DIST", FRONTEND_DIR / "dist"
)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():  # pragma: no cover - configuration error path
        raise FileNotFoundError(
            f"Required configuration file missing: {path}. "
            "Run from the repository root or restore the file."
        )
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _apply_env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    """Overlay deployment knobs from the environment onto the YAML settings.

    Only the keys below are overridable. Everything else — every threshold, every
    policy parameter, every scenario horizon — comes from the YAML, because those
    are the numbers the interface claims are auditable. A deployment should not be
    able to silently change an impact calculation through an env var.

    Precedence is environment > YAML. Values that fail to parse raise, rather
    than falling back to the YAML default, because a typo'd port silently
    running on the wrong port is worse than a startup failure.
    """
    api = cfg.setdefault("api", {})

    host = os.environ.get("SANJEEVANI_API_HOST")
    if host:
        api["host"] = host

    port = os.environ.get("SANJEEVANI_API_PORT")
    if port:
        api["port"] = int(port)

    # Comma-separated. Required when the UI is served from a different origin to
    # the API; with the single-container layout it is unnecessary.
    origins = os.environ.get("SANJEEVANI_CORS_ORIGINS")
    if origins:
        api["cors_origins"] = [o.strip() for o in origins.split(",") if o.strip()]

    ledger_path = os.environ.get("SANJEEVANI_LEDGER_PATH")
    if ledger_path:
        cfg.setdefault("audit", {})["ledger_path"] = ledger_path

    # "false"/"0"/"no" disable; anything else enables. Empty means "leave as-is".
    offline = os.environ.get("SANJEEVANI_OFFLINE")
    if offline:
        cfg.setdefault("runtime", {})["offline"] = offline.strip().lower() not in {
            "false",
            "0",
            "no",
        }

    return cfg


@functools.cache
def settings() -> dict[str, Any]:
    """Runtime settings (config/settings.yaml) with env overrides applied."""
    return _apply_env_overrides(_read_yaml(CONFIG_DIR / "settings.yaml"))


def cors_origins() -> list[str]:
    """Allowed browser origins, resolved from config or the environment."""
    return list(settings()["api"]["cors_origins"])


@functools.cache
def policies() -> dict[str, Any]:
    """The policy rulebook (config/policies.yaml)."""
    return _read_yaml(CONFIG_DIR / "policies.yaml")


@functools.cache
def scenarios_config() -> dict[str, Any]:
    """Horizons, markets, approvers and the disruption library."""
    return _read_yaml(CONFIG_DIR / "scenarios.yaml")


@functools.cache
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
    return datetime.now(UTC)


def isoformat(dt: datetime) -> str:
    """Canonical ISO-8601 UTC string used in every contract field."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    def load(cls) -> Thresholds:
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
