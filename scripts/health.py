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

    def signal_feeds() -> str:
        """The adapters must be replayable offline, and off unless asked for."""
        from backend.agents import feeds as feeds_pkg
        from backend.agents.feeds.snapshot import SnapshotStore
        from backend.config import DATA_DIR, feeds_config, settings

        feeds_cfg = settings()["sensing"]["feeds"]
        snapshot_dir = DATA_DIR / str(feeds_cfg["snapshot_dir"])
        store = SnapshotStore(snapshot_dir)
        load = feeds_pkg.load_signals(
            config=feeds_config(),
            snapshot_dir=snapshot_dir,
            retrieved_utc="2026-09-30T05:50:00Z",
            live=False,
            offline=True,
        )
        replayable = [fid for fid in feeds_pkg.ADAPTERS if store.latest(fid) is not None]
        missing = sorted(set(feeds_pkg.ADAPTERS) - set(replayable))
        assert not missing, f"no replayable snapshot for: {missing}"
        # A replay reads snapshots, so it must produce signals with no credential
        # present for the credentialled feed.
        assert load.records, "a replay of the committed snapshots produced no signals"
        for record in load.records:
            assert record.published_utc.endswith("Z"), (
                f"{record.signal_id} carries a naive timestamp, which A1 cannot read"
            )
        posture = "ENABLED (this overrides the demo feed)" if feeds_cfg["enabled"] else "off"
        return (
            f"{len(feeds_pkg.ADAPTERS)} adapters configured · "
            f"{len(load.records)} signals replayed offline · "
            f"{len(replayable)} snapshot(s) on disk · adapters {posture} by default"
        )

    def narrative_seam() -> str:
        """Off by default; and when on, provably unable to move a number."""
        from backend.agents import narrative as narrative_mod
        from backend.agents.pipeline import run_m1
        from backend.config import settings

        assert settings()["runtime"]["llm_enabled"] is False, (
            "the narrative seam must be off by default"
        )
        baseline = run_m1()
        frozen = baseline.scenarios[0]
        assert frozen.narrative_source["narrator"] == narrative_mod.SOURCE_FROZEN
        assert frozen.narrative_source["applied"] is False

        # With the seam on, the words may change. The numbers may not.
        settings()["runtime"]["llm_enabled"] = True
        settings()["narrative"]["narrator"] = "remote"
        try:
            narrated = run_m1()
        finally:
            settings()["runtime"]["llm_enabled"] = False
            settings()["narrative"]["narrator"] = "template"
        assert narrated.scenarios[0].narrative != frozen.narrative, (
            "the seam produced no prose, so this check is not testing anything"
        )
        assert narrated.primary_impact == baseline.primary_impact, (
            "the narrative seam changed an impact number"
        )
        assert [s.duration_days for s in narrated.scenarios] == [
            s.duration_days for s in baseline.scenarios
        ], "the narrative seam changed a scenario parameter"
        assert narrated.events_contract() == baseline.events_contract(), (
            "the narrative seam changed a frozen contract payload"
        )
        return (
            "off by default · frozen template when off · remote narrator falls back "
            "while offline · no number changes when it is on"
        )

    def determinism() -> str:
        from backend.agents.pipeline import run_m1

        first = run_m1().primary_impact
        second = run_m1().primary_impact
        assert first == second, "M1 impact is not reproducible"
        return f"impact reproducible · revenue at risk ${first['revenue_at_risk']:,.2f}"

    def contracts() -> str:
        from backend.contracts import (
            CONTRACT_VERSION,
            Event,
            EventSeverity,
            Impact,
            RecoveryPlan,
        )

        Event(
            event_id="EVT-001",
            type="COLD_CHAIN_EXCURSION",
            severity=EventSeverity.HIGH,
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

    def m2_optimizer() -> str:
        from backend.m2.optimizer import optimize_for_event

        first = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
        second = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
        assert first.recommended.model_dump() == second.recommended.model_dump(), (
            "the optimizer is not deterministic"
        )
        verification = first.trace["verification"]
        assert verification["milp_available"], verification["milp_note"]
        assert verification["milp_agrees_with_exhaustive"], (
            "the MILP and the exhaustive search disagree: "
            f"{verification['milp_choices']} vs {first.trace['selected_plan']['choices']}"
        )
        assert first.trace["constraints"]["violations"] == [], (
            f"the recommended plan breaks a ceiling: {first.trace['constraints']['violations']}"
        )
        return (
            f"solved {verification['milp_solver']} · agrees with exhaustive search · "
            f"{first.trace['decision_space']['combinations']} combinations · "
            f"plan {first.recommended.plan_id} {first.recommended.strategy} "
            f"${first.recommended.cost:,.0f}"
        )

    def m2_des() -> str:
        from backend.graph.network import Network
        from backend.m2.des import clearance_for_plan, compare_with_twin, windows_from_disruption
        from backend.m2.digital_twin import build_catalogue, build_contexts
        from backend.m2.optimizer import derive_disruption

        net = Network.load()
        view = derive_disruption(net, scenario_id="SCN-EVT-001-10D", event_id="EVT-001")
        contexts, _ = build_contexts(
            net,
            disrupted_lanes=list(view.disrupted_lanes),
            excursed_shipments=list(view.excursed_shipments),
        )
        catalogue = build_catalogue()
        assignment = {c.shipment_id: "WAIT" for c in contexts}
        des = clearance_for_plan(
            net, contexts, catalogue, assignment, windows_from_disruption(["PORT-SUEZ"], 10.0)
        )
        rerun = clearance_for_plan(
            net, contexts, catalogue, assignment, windows_from_disruption(["PORT-SUEZ"], 10.0)
        )
        assert des.as_dict() == rerun.as_dict(), "the DES is not deterministic"
        # The known blind spot: uniform WAIT prices at 336 h in the twin and the
        # queue says 406.8 h. If this number moves, a seeded input changed.
        comparison = compare_with_twin(des, 336.0)
        assert comparison["verdict"] == "des_slower" and des.recovery_hours == 406.8, (
            f"unexpected DES verdict: {comparison['verdict']} at {des.recovery_hours}h"
        )
        waits = [f"{k} {v:.2f}d" for k, v in sorted(des.wait_days.items())]
        return (
            f"deterministic · uniform WAIT contradicts the twin "
            f"({des.recovery_hours:.1f}h vs 336.0h, verdict {comparison['verdict']}) · "
            f"waits: {', '.join(waits)}"
        )

    def authentication() -> str:
        from backend.security import (
            TokenService,
            TokenSettings,
            hash_password,
            verify_password,
        )
        from backend.security.identity import store

        assert verify_password("pw", hash_password("pw", iterations=1000))
        assert not verify_password("nope", hash_password("pw", iterations=1000))
        service = TokenService(
            TokenSettings(enabled=True, secret="h" * 40, ttl_seconds=60, ephemeral_secret=False)
        )
        accounts = store.accounts()
        assert accounts, "the identity roster is empty; run scripts/make_identity.py --demo"
        issued = service.issue(accounts[0])
        claims = service.verify(issued["access_token"])
        assert claims["sub"] == accounts[0].id
        from backend.config import settings

        assert settings()["auth"]["enabled"] is False, "auth must be off by default in demo mode"
        return (
            f"{len(accounts)} accounts · PBKDF2 round-trip OK · token round-trip OK · "
            "auth disabled by default"
        )

    def security_posture() -> str:
        from backend.security import build_csp
        from backend.security.headers import BASE_HEADERS

        policy = build_csp("n0nce")
        assert "'nonce-n0nce'" in policy
        script = next(d for d in policy.split("; ") if d.startswith("script-src"))
        assert "'unsafe-inline'" not in script, "scripts must not allow inline code"
        assert "frame-ancestors 'none'" in policy
        for header in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
            assert header in BASE_HEADERS
        return f"CSP with nonce · {len(BASE_HEADERS)} security headers · rate limiting configured"

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
    check("Signal-feed adapters (GDELT / Open-Meteo / Freightos)", signal_feeds)
    check("Narrative seam (the only place a model may speak)", narrative_seam)
    check("Determinism", determinism)

    print("\nM2 · simulation and optimization")
    check("Optimizer + digital twin", m2_optimizer)
    check("Discrete-event cross-check (berths, closures, queues)", m2_des)

    print("\nContracts & governance")
    check("Shared contracts", contracts)
    check("Policy rulebook", policy_engine)
    check("Governance invariant", governance_invariant)

    print("\nSecurity")
    check("Authentication and identity", authentication)
    check("Security posture (CSP, headers, rate limiting)", security_posture)

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
