"""M1 pipeline facade (Member 1).

One clean entry point that runs the whole upstream intelligence chain and emits
the shared contracts:

    sense -> verify -> scenario -> network impact -> Event[] + Impact

`run_m1()` is the only function M2 and M3 call. Everything else in this package
is an implementation detail, which is what the acceptance criterion "Member 2 can
run your module without importing private internals" demands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.agents import impact as impact_mod
from backend.agents import verification as verification_mod
from backend.config import isoformat, scenario_clock, scenarios_config
from backend.graph.network import Network


@dataclass
class M1Result:
    network: Network
    events: list[verification_mod.VerifiedEvent]
    scenarios: list[verification_mod.Scenario]
    impacts: list[dict[str, Any]]
    verification: dict[str, Any]
    primary_impact: dict[str, Any]
    primary_horizon_days: int
    watchlist: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    deduplicated: list[dict[str, Any]]

    def events_contract(self) -> list[dict[str, Any]]:
        """The frozen `Event` payloads."""
        return [e.as_event_dict() for e in self.events]


def run_m1(network: Network | None = None) -> M1Result:
    """Run the complete M1 chain deterministically."""
    net = network or Network.load()

    from backend.agents.sensing import sense

    sensing = sense(network=net)
    verified = verification_mod.verify(sensing["candidates"], net)
    scenarios = verification_mod.generate_scenarios(verified["events"])
    impacts = impact_mod.impact_for_all_horizons(verified["events"], scenarios, net)

    hero = scenarios_config()["hero_scenario"]
    primary_horizon = int(hero["primary_horizon_days"])
    primary = next(
        (i for i in impacts if i["horizon_days"] == primary_horizon),
        impacts[0] if impacts else {},
    )

    return M1Result(
        network=net,
        events=verified["events"],
        scenarios=scenarios,
        impacts=impacts,
        verification=verified,
        primary_impact=primary,
        primary_horizon_days=primary_horizon,
        watchlist=sensing["watchlist"],
        rejected=verified["rejected"],
        deduplicated=verified["deduplicated"],
    )


def m1_snapshot(result: M1Result) -> dict[str, Any]:
    """Serialise an M1 run for the API / UI."""
    return {
        "agent_chain": ["A1_SENSING", "A2_VERIFICATION_AND_SCENARIO", "A3_NETWORK_IMPACT"],
        "generated_at": isoformat(scenario_clock()),
        "events": [e.as_dict() for e in result.events],
        "events_contract": result.events_contract(),
        "scenarios": [s.as_dict() for s in result.scenarios],
        "impacts": result.impacts,
        "primary_impact": result.primary_impact,
        "primary_horizon_days": result.primary_horizon_days,
        "watchlist": result.watchlist,
        "rejected": result.rejected,
        "deduplicated": result.deduplicated,
        "rules": result.verification["rules"],
    }
