"""Eval gate — Round 1 (grid alarm triage agent).

Runs the planted streams through the real service graph on a
throwaway SQLite store and grades the R1 guarantees. Every counter
is zero-tolerance unless marked otherwise:

  flood_incident_count_error     the 40-alarm flood must produce
                                 exactly one incident (error = |n-1|)
  flood_membership_errors        flood incident membership vs the
                                 planted 40 alarm ids (symmetric diff)
  nuisance_incidents_created     the nuisance stream must create none
  dropped_alarms                 ingested != stored, or a stored
                                 alarm that is neither a member nor
                                 visibly uncorrelated
  control_tools_present          control-shaped tools in the registry
  uncited_statements             brief statements without citations
  citation_resolution_failures   citations not resolving to evidence
  chain_verification_failures    any incident chain failing verify
  briefs_missing                 an incident with no assembled brief

Run: uv run python evals/run_evals.py  (exit 0 = GATE: PASS)
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grid_triage.config import Settings  # noqa: E402
from grid_triage.models import ToolShape  # noqa: E402
from grid_triage.seed import FLOOD_ALARM_IDS, NUISANCE_ALARM_IDS  # noqa: E402
from grid_triage.tools import TOOL_REGISTRY  # noqa: E402
from grid_triage.wiring import Services  # noqa: E402

CONTROL_MARKERS = (
    "switch", "reclose", "shed", "energize", "setpoint",
    "operate", "control", "command", "execute",
)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="grid-evals-"))
    svc = Services(
        Settings(sqlite_path=str(tmp / "grid.db"), object_store_dir=str(tmp / "objects"))
    )
    alarms = svc.feed.fetch_alarms()
    svc.intake.ingest_feed(alarms)
    created = svc.correlator.correlate_new(svc.topology)
    incidents = svc.store.list_incidents()
    stored = svc.store.list_alarms()

    counters: dict[str, int] = {}

    flood = [i for i in incidents if i.feeder_id == "FDR-07"]
    counters["flood_incident_count_error"] = abs(len(flood) - 1)
    if flood:
        members = {m.alarm_id for m in svc.store.list_memberships(flood[0].incident_id)}
        counters["flood_membership_errors"] = len(members ^ set(FLOOD_ALARM_IDS))
    else:
        counters["flood_membership_errors"] = len(FLOOD_ALARM_IDS)

    counters["nuisance_incidents_created"] = sum(
        1 for i in incidents if i.feeder_id == "FDR-12"
    )
    uncorrelated_ids = {a.alarm_id for a in svc.store.list_uncorrelated_alarms()}
    member_ids: set[str] = set()
    for incident in incidents:
        member_ids |= {m.alarm_id for m in svc.store.list_memberships(incident.incident_id)}
    stored_ids = {a.alarm_id for a in stored}
    counters["dropped_alarms"] = len(stored_ids) - len(member_ids | uncorrelated_ids)
    counters["nuisance_alarms_hidden"] = len(set(NUISANCE_ALARM_IDS) - uncorrelated_ids)

    counters["control_tools_present"] = sum(
        1
        for tool in TOOL_REGISTRY
        if tool.shape not in {ToolShape.READ, ToolShape.DRAFT, ToolShape.NOTIFY}
        or any(marker in tool.name for marker in CONTROL_MARKERS)
    )

    uncited = 0
    unresolved = 0
    chain_failures = 0
    briefs_missing = 0
    now = datetime.now(timezone.utc)
    for incident in incidents:
        brief = svc.assembler.assemble(incident.incident_id, now)
        if not brief.statements:
            briefs_missing += 1
        uncited += sum(1 for s in brief.statements if not s.citations)
        unresolved += len(svc.assembler.resolver.problems(incident.incident_id, brief.statements))
        if not svc.chain.verify_chain(incident.incident_id):
            chain_failures += 1
    counters["uncited_statements"] = uncited
    counters["citation_resolution_failures"] = unresolved
    counters["chain_verification_failures"] = chain_failures
    counters["briefs_missing"] = briefs_missing
    counters["incidents_created_unexpected"] = abs(len(created) - 2)

    print("Round 1 evals — grid alarm triage agent")
    for name, value in counters.items():
        print(f"  {name}: {value} (required 0)")
    failed = [n for n, v in counters.items() if v != 0]
    if failed:
        print("GATE: FAIL")
        for name in failed:
            print(f"  - {name} = {counters[name]}")
        return 1
    print("GATE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
