"""Eval gate — Rounds 1–2 (grid alarm triage agent).

Runs the planted streams through the real service graph on a
throwaway SQLite store and grades the R1 and R2 guarantees. Every
counter is zero-tolerance unless marked otherwise:

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
  flood_severity_band_error      Feeder 7 flood at T+30min must be
                                 SEV-1 (the hospital feeder)
  flood_severity_score_error     its score must be the hand-computed
                                 85 (error = |score - 85|)
  lone_severity_band_error       the lone Feeder 3 trip at +15min
                                 must be SEV-3
  lone_severity_score_error      its score must be the hand-computed
                                 28 (error = |score - 28|)
  severity_band_mismatches       stored assessments whose band does
                                 not match their recorded score
  unrated_but_banded             a probe with customer counts removed
                                 from the topology must come back
                                 UNRATED with the input named missing
  brief_band_violations_accepted a brief restating a different band
                                 must be refused, never stored
  acknowledgement_flow_errors    the SEV-1 requirement appears in the
                                 queue, a wrong-role ack is refused, a
                                 named operator's ack clears it

Run: uv run python evals/run_evals.py  (exit 0 = GATE: PASS)
"""

from __future__ import annotations

import copy
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grid_triage.agent import BandMismatchRefusal, IncidentAssembler  # noqa: E402
from grid_triage.config import Settings  # noqa: E402
from grid_triage.models import BriefStatement, Citation, ToolShape  # noqa: E402
from grid_triage.seed import (  # noqa: E402
    FLOOD_ALARM_IDS,
    NUISANCE_ALARM_IDS,
    T0,
    lone_alarms,
)
from grid_triage.severity import (  # noqa: E402
    SeverityRefusal,
    band_for_score,
    compute_assessment,
)
from grid_triage.tools import TOOL_REGISTRY  # noqa: E402
from grid_triage.topology import TopologyView  # noqa: E402
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

    # ---- Round 2: severity, graded against hand-computed truth ----
    flood_incident = flood[0]
    flood_a = svc.severity.assess(flood_incident.incident_id, T0 + timedelta(minutes=30))
    counters["flood_severity_band_error"] = 0 if flood_a.band == "SEV-1" else 1
    counters["flood_severity_score_error"] = abs((flood_a.score or -1) - 85)
    lone_incident = next(i for i in incidents if i.feeder_id == "FDR-03")
    lone_a = svc.severity.assess(
        lone_incident.incident_id,
        lone_alarms()[0].raised_at + timedelta(minutes=15),
    )
    counters["lone_severity_band_error"] = 0 if lone_a.band == "SEV-3" else 1
    counters["lone_severity_score_error"] = abs((lone_a.score or -1) - 28)

    mismatches = 0
    for incident in incidents:
        for a in svc.store.list_severity_assessments(incident.incident_id):
            if a.score is not None and band_for_score(a.score) != a.band:
                mismatches += 1
    counters["severity_band_mismatches"] = mismatches

    stripped = copy.deepcopy(svc.store.get_topology_payload("v1"))
    for device in stripped["devices"]:
        device.pop("customers", None)
    member_alarms = {
        m.alarm_id: svc.store.get_alarm(m.alarm_id)
        for m in svc.store.list_memberships(flood_incident.incident_id)
    }
    probe = compute_assessment(
        flood_incident,
        member_alarms,
        TopologyView(stripped),
        T0 + timedelta(minutes=30),
    )
    counters["unrated_but_banded"] = (
        0
        if (
            probe.band == "UNRATED"
            and probe.score is None
            and "customers_affected" in probe.missing_inputs
        )
        else 1
    )

    class _WrongBand:
        profile = "offline"

        def propose_brief(self, facts):
            return [
                BriefStatement(
                    text="Severity: SEV-4 — a minor event, stood down.",
                    citations=[
                        Citation(evidence_item_id="EV-ALM-FLD-001", field="kind")
                    ],
                )
            ]

    try:
        IncidentAssembler(svc.store, svc.chain, _WrongBand()).assemble(
            flood_incident.incident_id, datetime.now(timezone.utc)
        )
        counters["brief_band_violations_accepted"] = 1
    except BandMismatchRefusal:
        counters["brief_band_violations_accepted"] = 0

    ack_errors = 0
    pending_ids = [p.assessment_id for p in svc.severity.pending_acknowledgements()]
    if flood_a.assessment_id not in pending_ids:
        ack_errors += 1
    try:
        svc.severity.acknowledge(
            flood_a.assessment_id, "crew.lee", "field_crew", datetime.now(timezone.utc)
        )
        ack_errors += 1  # a wrong-role acknowledgement was accepted
    except SeverityRefusal:
        pass
    try:
        svc.severity.acknowledge(
            flood_a.assessment_id, "op.dana", "operator", datetime.now(timezone.utc)
        )
    except SeverityRefusal:
        ack_errors += 1
    pending_ids = [p.assessment_id for p in svc.severity.pending_acknowledgements()]
    if flood_a.assessment_id in pending_ids:
        ack_errors += 1
    counters["acknowledgement_flow_errors"] = ack_errors

    print("Evals — grid alarm triage agent (Rounds 1-2)")
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
