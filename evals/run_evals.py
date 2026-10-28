"""Eval gate — Rounds 1–3 (grid alarm triage agent).

Runs the planted streams through the real service graph on a
throwaway SQLite store and grades the R1–R3 guarantees. Every
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
  classification_errors          the flood must classify
                                 vegetation_contact under its rule;
                                 wrong class or rule counts
  forced_classifications         an anchor pattern matching no rule
                                 (the lone trip) must stay unknown —
                                 a forced class counts
  priors_order_errors            the flood's priors must be exactly
                                 [INC-HIST-0042 (10), INC-HIST-0057
                                 (7)] — the class-weighted order
  priors_included_irrelevant     the Feeder 12 record must never
                                 surface as a flood prior
  brief_priors_missing           the flood's brief must quote both
                                 priors, cited to their records
  brief_prior_misattributions_accepted
                                 a brief attributing a class to a
                                 prior that its record does not state
                                 must be refused, never stored
  memory_facts_altered           stored memory records differing
                                 from the planted facts, or a forged
                                 re-insert accepted
  pack_members_missing           the sealed flood pack must carry
                                 all 40 alarm members plus the
                                 correlation, severity, topology,
                                 brief and priors members
  pack_verify_failures           an untampered sealed pack must
                                 verify with zero problems
  pack_member_tamper_undetected  tampering with a stored pack
                                 member, or with a member alarm's
                                 payload, must be caught by verify

Run: uv run python evals/run_evals.py  (exit 0 = GATE: PASS)
"""

from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grid_triage.agent import (  # noqa: E402
    BandMismatchRefusal,
    IncidentAssembler,
    PriorAttributionRefusal,
)
from grid_triage.config import Settings  # noqa: E402
from grid_triage.models import BriefStatement, Citation, ToolShape  # noqa: E402
from grid_triage.seed import (  # noqa: E402
    FLOOD_ALARM_IDS,
    NUISANCE_ALARM_IDS,
    T0,
    history_records,
    lone_alarms,
)
from grid_triage.severity import (  # noqa: E402
    SeverityRefusal,
    band_for_score,
    compute_assessment,
)
from grid_triage.store import MemoryImmutableError  # noqa: E402
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
    # Round 3: the planted history is in memory before any brief is
    # assembled, so the brief flow exercises priors end to end.
    svc.memory.load_history()

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

    # ---- Round 3: classification, memory, priors, evidence packs ----
    flood_class = svc.cause.classify(flood_incident.incident_id, now)
    lone_class = svc.cause.classify(lone_incident.incident_id, now)
    counters["classification_errors"] = (
        0
        if (
            flood_class.cause_class == "vegetation_contact"
            and flood_class.rule_id == "lockout_with_downstream_fault"
        )
        else 1
    )
    counters["forced_classifications"] = (
        0 if (lone_class.cause_class == "unknown" and lone_class.rule_id is None) else 1
    )

    hits = svc.memory.retrieve_for_incident(flood_incident.incident_id, now)
    got_order = [(h.record.incident_id, h.score) for h in hits]
    counters["priors_order_errors"] = (
        0 if got_order == [("INC-HIST-0042", 10), ("INC-HIST-0057", 7)] else 1
    )
    counters["priors_included_irrelevant"] = sum(
        1 for h in hits if h.record.feeder_id != "FDR-07"
    )

    flood_brief = svc.store.list_briefs(flood_incident.incident_id)[-1]
    prior_statements = [
        s for s in flood_brief.statements if s.text.startswith("Prior incident")
    ]
    counters["brief_priors_missing"] = abs(len(prior_statements) - 2)

    hist42 = next(
        r for r in svc.memory_port.list_records() if r.incident_id == "INC-HIST-0042"
    )

    class _Misattributor:
        profile = "offline"

        def propose_brief(self, facts):
            return [
                BriefStatement(
                    text="A prior incident on this feeder was caused by lightning.",
                    citations=[
                        Citation(evidence_item_id=hist42.record_id, field="cause_class")
                    ],
                )
            ]

    briefs_before = len(svc.store.list_briefs(flood_incident.incident_id))
    try:
        IncidentAssembler(
            svc.store, svc.chain, _Misattributor(), cause=svc.cause, memory=svc.memory
        ).assemble(flood_incident.incident_id, now)
        counters["brief_prior_misattributions_accepted"] = 1
    except PriorAttributionRefusal:
        counters["brief_prior_misattributions_accepted"] = (
            0
            if len(svc.store.list_briefs(flood_incident.incident_id)) == briefs_before
            else 1
        )

    altered = 0
    for record in history_records():
        if svc.store.get_memory_record(record.record_id) != record:
            altered += 1
    try:
        svc.memory_port.put_record(
            hist42.model_copy(update={"resolution_summary": "A rewritten past."})
        )
        altered += 1  # a forged re-insert was accepted
    except MemoryImmutableError:
        pass
    counters["memory_facts_altered"] = altered

    pack = svc.packs.seal(flood_incident.incident_id, "op.dana", T0 + timedelta(minutes=35))
    member_types = [m["type"] for m in pack.members]
    counters["pack_members_missing"] = (
        abs(member_types.count("alarm") - 40)
        + sum(
            1
            for t in (
                "correlation_decisions",
                "severity_assessment",
                "topology_slice",
                "brief",
                "priors",
            )
            if member_types.count(t) != 1
        )
    )
    counters["pack_verify_failures"] = len(svc.packs.verify_pack(pack.pack_id))

    # Tamper probes run last: each must be caught by recomputation.
    undetected = 0
    db = sqlite3.connect(tmp / "grid.db")
    payload = json.loads(
        db.execute(
            "SELECT payload FROM evidence_packs WHERE pack_id = ?", (pack.pack_id,)
        ).fetchone()[0]
    )
    sev_member = next(
        m for m in payload["members"] if m["type"] == "severity_assessment"
    )
    sev_member["content_hash"] = "0" * 64
    db.execute(
        "UPDATE evidence_packs SET payload = ? WHERE pack_id = ?",
        (json.dumps(payload), pack.pack_id),
    )
    db.commit()
    db.close()
    if not svc.packs.verify_pack(pack.pack_id):
        undetected += 1

    lone_pack = svc.packs.seal(
        lone_incident.incident_id, "op.dana", T0 + timedelta(hours=8)
    )
    lone_item = svc.store.list_evidence(lone_incident.incident_id)[0]
    (svc.objects.root / lone_item.object_key).write_bytes(b'{"alarm_id": "forged"}')
    if not svc.packs.verify_pack(lone_pack.pack_id):
        undetected += 1
    counters["pack_member_tamper_undetected"] = undetected

    print("Evals — grid alarm triage agent (Rounds 1-3)")
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
