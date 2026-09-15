#!/usr/bin/env python3
"""Pre-flight health check.

    python scripts/health.py

Answers the only question that matters five minutes before a demo: will it run?
Verifies the environment, the seeded data, the contracts and the governance
invariant WITHOUT needing a server, and exits non-zero if anything is wrong.

Run this on the demo machine before every rehearsal.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OK = "  PASS"
BAD = "  FAIL"

failures: list[str] = []


def check(label: str, fn) -> None:
    try:
        detail = fn()
        print(f"{OK}  {label}{f' — {detail}' if detail else ''}")
    except Exception as exc:  # noqa: BLE001 - a health check reports, never crashes
        failures.append(label)
        print(f"{BAD}  {label} — {type(exc).__name__}: {exc}")


def main() -> int:
    print("SANJEEVANI health check\n")

    def environment() -> str:
        import sys as _sys

        major, minor = _sys.version_info[:2]
        if (major, minor) < (3, 11):
            raise RuntimeError(f"Python {major}.{minor} is too old; 3.11+ required")
        return f"Python {major}.{minor}"

    def dependencies() -> str:
        import fastapi
        import networkx
        import pydantic
        import yaml

        return (
            f"fastapi {fastapi.__version__} · pydantic {pydantic.__version__} · "
            f"networkx {networkx.__version__} · pyyaml {yaml.__version__}"
        )

    def config() -> str:
        from backend.config import policies, scenarios_config, settings

        return (
            f"settings v{settings()['meta']['config_version']} · "
            f"rulebook v{policies()['meta']['rulebook_version']} · "
            f"scenario library v{scenarios_config()['meta']['library_version']}"
        )

    def network() -> str:
        from backend.graph.network import Network

        net = Network.load()
        assert len(net.nodes) == 12, f"expected 12 nodes, found {len(net.nodes)}"
        assert len(net.lanes) == 6, f"expected 6 lanes, found {len(net.lanes)}"
        assert len(net.skus) == 12, f"expected 12 SKUs, found {len(net.skus)}"
        assert len(net.shipments) == 8, f"expected 8 consignments, found {len(net.shipments)}"
        exposed = [lane.id for lane in net.lanes.values() if lane.red_sea_exposed]
        assert exposed == ["LANE-MUM-BIO-EU"], f"unexpected Red Sea lanes: {exposed}"
        return "12 nodes · 6 lanes (1 Red Sea) · 12 SKUs · 8 consignments"

    def telemetry() -> str:
        from backend.agents import telemetry as t

        analysis = t.analyse("SHIP-001", 8.0)
        assert analysis and analysis.breached, "hero consignment must show an excursion"
        assert abs(analysis.peak_temp_c - 11.4) < 1e-6, "peak should be 11.4C"
        return f"SHIP-001 peak {analysis.peak_temp_c}C, {analysis.minutes_above_limit:.1f} min above limit"

    def m1_pipeline() -> str:
        from backend.agents.pipeline import run_m1

        result = run_m1()
        assert len(result.events) == 2, f"expected 2 verified events, got {len(result.events)}"
        # A credible single-source claim must escalate and then FAIL the 2-source
        # rule. If this ever passes verification, the credibility control is gone.
        assert result.rejected, "the two-source rejection case should be present"
        assert result.watchlist, "the watchlist case should be present"
        assert result.deduplicated, "the dedup case should be present"
        assert sorted(s.horizon_days for s in result.scenarios) == [3, 10, 30]
        return (
            f"{len(result.events)} verified · {len(result.rejected)} rejected · "
            f"{len(result.watchlist)} watchlisted · {len(result.deduplicated)} deduplicated"
        )

    def determinism() -> str:
        from backend.agents.pipeline import run_m1

        first = run_m1().primary_impact
        second = run_m1().primary_impact
        assert first == second, "M1 impact is not reproducible"
        return f"impact reproducible · revenue at risk ${first['revenue_at_risk']:,.2f}"

    def contracts() -> str:
        from backend.contracts import CONTRACT_VERSION, Event, Impact, RecoveryPlan

        Event(
            event_id="EVT-001",
            type="COLD_CHAIN_EXCURSION",
            severity="HIGH",
            confidence=0.96,
            timestamp="2026-09-30T06:00:00Z",
            source="IoT",
        )
        Impact(event_id="E", revenue_at_risk=0, stockout_probability=0, service_level_risk=0)
        RecoveryPlan(
            plan_id="PLAN-001",
            strategy="REROUTE_MUMBAI_AIR",
            cost=184000,
            service_level=0.965,
            resilience_score=0.81,
            temperature_risk=0.22,
            recovery_time_hours=18,
        )
        return f"contracts v{CONTRACT_VERSION} validate"

    def policy_engine() -> str:
        from backend.config import policies as policies_cfg
        from backend.governance import policy_engine as pe

        catalogue = pe.rule_catalogue()
        unimplemented = [r["id"] for r in catalogue if not r["implemented"]]
        assert not unimplemented, f"rules without an evaluator: {unimplemented}"
        assert len(catalogue) == len(policies_cfg()["rules"])
        return f"{len(catalogue)} rules, all with evaluators"

    def governance_invariant() -> str:
        from backend.governance.approval import ALLOWED, ApprovalState

        reachable = [
            state.value for state, targets in ALLOWED.items() if ApprovalState.EXECUTING in targets
        ]
        assert reachable == ["APPROVED"], f"EXECUTING reachable from {reachable}"
        return "EXECUTING reachable only from APPROVED"

    def demo_path() -> str:
        from backend.contracts import ApprovalDecision
        from backend.governance.approval import GovernanceError
        from backend.orchestrator import orchestrator

        orchestrator.reset()
        state = orchestrator.run()
        assert state["errors"] == [], f"run produced errors: {state['errors']}"
        assert state["compliance"]["compliance_status"] == "PASSED"
        try:
            orchestrator.execute()
            raise AssertionError("execution succeeded without approval")
        except GovernanceError:
            pass
        orchestrator.decide(ApprovalDecision.APPROVE, "meera.iyer", "health check")
        state = orchestrator.execute()
        receipt = state["execution"]["receipt"]
        assert receipt["ibp"] == receipt["tm"] == receipt["ariba"] == "SUCCESS"
        assert state["ledger"]["coverage"]["complete"], "loop stages incomplete"
        assert state["ledger"]["chain"]["intact"], "ledger chain broken"
        orchestrator.reset()
        return (
            f"reset → run → approve → execute → audit OK · "
            f"{state['ledger']['length']} ledger records"
        )

    def offline_guarantee() -> str:
        from backend.config import settings

        cfg = settings()["runtime"]
        assert cfg["offline"] is True, "offline mode must be on by default"
        assert cfg["llm_enabled"] is False, "LLM must be disabled by default"
        return "offline=true · llm_enabled=false · no external service required"

    def frontend_assets() -> str:
        dist = REPO_ROOT / "frontend" / "dist"
        if not dist.exists():
            return "not built yet (optional — run: npm --prefix frontend run build)"
        index = dist / "index.html"
        assert index.exists(), "dist/index.html missing"
        return "built"

    print("Environment")
    check("Python version", environment)
    check("Backend dependencies", dependencies)
    check("Configuration files", config)

    print("\nSeeded data")
    check("Network seed", network)
    check("Telemetry seed", telemetry)

    print("\nM1 · upstream intelligence")
    check("Sensing / verification / scenario pipeline", m1_pipeline)
    check("Determinism", determinism)

    print("\nContracts & governance")
    check("Shared contracts", contracts)
    check("Policy rulebook", policy_engine)
    check("Governance invariant", governance_invariant)

    print("\nEnd-to-end")
    check("Hero demo path", demo_path)
    check("Offline guarantee", offline_guarantee)
    check("Frontend assets", frontend_assets)

    print()
    if failures:
        print(f"HEALTH CHECK FAILED — {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("ALL CHECKS PASSED — the demo is ready to run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
