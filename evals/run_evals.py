"""Eval gate — Rounds 1–6 (grid alarm triage agent).

Runs the planted streams through the real service graph on a
throwaway SQLite store and grades the R1–R5 guarantees. Every
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
  etr_fixture_errors             the flood ETR at T+30min must
                                 equal the hand-computed estimate:
                                 wait 45 + travel 25 + repair 150
                                 = 220, restored 06:23, duration 250
  etr_revision_errors            re-estimating at T+120min must
                                 supersede with delta -45 and the
                                 changed input named (crew_wait)
  etr_not_computable_errors      the lone trip (unknown cause)
                                 must be not_computable naming
                                 cause_class; synthetic probes must
                                 name base_rate / travel_minutes
  etr_brief_basis_missing        the flood's brief must carry its
                                 ETR with the basis label attached
  etr_basis_violations_accepted  a brief stating a bare restoration
                                 time must be refused, never stored
  closure_flow_errors            closing the flood must compare
                                 its actual 270 against the
                                 estimated 250; a second closure
                                 must be refused
  etr_accuracy_errors            the ledger after both closures
                                 must read exactly: vegetation x1,
                                 mean signed +20, unestimated 1
  work_order_fixture_errors      the flood draft must carry every
                                 pack-derived section exactly: 8
                                 devices in order, 5 safety kinds,
                                 vegetation_clearing, the pack
                                 materials, the T+30 ETR pinned,
                                 no gaps; the lone draft must name
                                 its required_qualification gap
  work_order_assignment_errors   the roster-committed window is
                                 refused naming INC-EXT-2201; an
                                 unqualified crew is refused; a
                                 cross-order overlap is refused
                                 naming the live assignment; the
                                 unclassified draft cannot be
                                 assigned at all
  work_order_approval_errors     approval without assignment,
                                 self-approval of a human revision,
                                 and a wrong-role approval are all
                                 refused; a clean approval pins the
                                 exact version, drafts exactly 2
                                 notifications, and a second
                                 decision on the version is refused
  work_order_rejection_errors    rejection without a reason is
                                 refused; a rejected version cannot
                                 be approved; approving a
                                 superseded version is refused; a
                                 post-approval revision stands in
                                 draft while the approval stays
                                 pinned to the old version
  notification_send_surfaces     every notification body is a
                                 marked draft, and no route, action
                                 or function in the package offers
                                 to send one
  pinned_topology_update_errors  a field update carrying an alarm
                                 from SEN-0723 (a device that exists
                                 only in topology v2) against a
                                 v1-pinned incident must record
                                 unresolvable_under_pinned_topology,
                                 and the alarm must not join
  v1_incident_mutation_errors    declaring v2 must leave every
                                 v1 incident artefact byte-identical
  closed_recorrelation_accepted  re-correlating the closed flood
                                 or lone incidents must be refused
                                 naming the closure — accepted
                                 attempts count
  revision_diff_errors           the next-day incident's
                                 re-correlation diff must be exact:
                                 the two split-section alarms
                                 removed, the SEN-0723 update alarm
                                 added, devices matching, severity
                                 SEV-1 84 -> SEV-2 64 with the
                                 customer input named as moved
  lone_v2_severity_errors        the lone trip recomputed purely
                                 under v2 at +15min must be SEV-2
                                 with score 58 — the annex hospital
                                 load's +30 critical points on the
                                 hand-computed v1 figure of 28

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
    EtrBasisRefusal,
    IncidentAssembler,
    PriorAttributionRefusal,
)
from grid_triage.cause import classify_cause  # noqa: E402
from grid_triage.closure import ClosureRefusal  # noqa: E402
from grid_triage.config import Settings  # noqa: E402
from grid_triage.etr import ETR_PACK, compute_estimate  # noqa: E402
from grid_triage.models import (  # noqa: E402
    Alarm,
    BriefStatement,
    CauseClassification,
    Citation,
    Incident,
    ToolShape,
)
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
from grid_triage.revisions import RevisionRefusal  # noqa: E402
from grid_triage.work_orders import WORK_PACK, WorkOrderRefusal  # noqa: E402

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

    # ---- Round 4: ETR, graded against hand-computed truth ----
    # Flood at T0+30min: vegetation crew committed until 03:28
    # (wait 45) + SUB-ALDER -> SUB-ALDER travel 25 + base rate
    # vegetation_contact|feeder_breaker 150 = 220; restored 06:23;
    # duration from the 02:13 anchor = 250.
    flood_etr = svc.etr.estimate(flood_incident.incident_id, T0 + timedelta(minutes=30))
    etr_errors = 0
    if flood_etr.status != "computed" or flood_etr.basis != "computed_from_rates":
        etr_errors += 1
    if {c["name"]: c["minutes"] for c in flood_etr.components} != {
        "crew_wait": 45,
        "travel_access": 25,
        "base_repair": 150,
    }:
        etr_errors += 1
    if flood_etr.total_minutes != 220:
        etr_errors += 1
    if flood_etr.estimated_restored_at != T0 + timedelta(minutes=250):
        etr_errors += 1
    if flood_etr.estimated_duration_minutes != 250.0:
        etr_errors += 1
    if flood_etr.inputs.get("crew_id") != "CREW-NORTH-VEG":
        etr_errors += 1
    if flood_etr.missing_inputs != []:
        etr_errors += 1
    counters["etr_fixture_errors"] = etr_errors

    # Revision, as a pure computation: at T0+120 the crew is free,
    # so only crew_wait changes (45 -> 0) and the total moves -45.
    flood_anchor = svc.store.get_alarm(flood_incident.anchor_alarm_id)
    flood_classification = classify_cause(
        flood_incident, member_alarms, T0 + timedelta(minutes=30)
    )
    rev1 = compute_estimate(
        flood_incident, flood_anchor, flood_classification,
        svc.topology, svc.roster, T0 + timedelta(minutes=30),
    )
    rev2 = compute_estimate(
        flood_incident, flood_anchor, flood_classification,
        svc.topology, svc.roster, T0 + timedelta(minutes=120),
        supersedes=rev1.estimate_id, prior=rev1,
    )
    counters["etr_revision_errors"] = (
        0
        if (
            rev2.supersedes == rev1.estimate_id
            and rev2.total_minutes == 175
            and rev2.revision["total_minutes_delta"] == -45
            and rev2.revision["estimated_restored_at_delta_minutes"] == 45.0
            and rev2.revision["changed_inputs"] == ["crew_wait"]
        )
        else 1
    )

    # Not-computable probes: each missing input named, no time.
    nc_errors = 0
    lone_etr = svc.etr.estimate(
        lone_incident.incident_id,
        lone_alarms()[0].raised_at + timedelta(minutes=15),
    )
    if not (
        lone_etr.status == "not_computable"
        and lone_etr.missing_inputs == ["cause_class"]
        and lone_etr.estimated_restored_at is None
        and lone_etr.total_minutes is None
    ):
        nc_errors += 1

    def _synthetic(anchor_device, feeder, substation):
        incident = Incident(
            incident_id="INC-SYNTH", feeder_id=feeder,
            substation_id=substation, topology_version="v1",
            correlation_pack_version="2026.10.1",
            anchor_alarm_id="ALM-SYNTH",
            created_at=T0,
        )
        anchor = Alarm(
            alarm_id="ALM-SYNTH", stream_id="S", device_id=anchor_device,
            feeder_id=feeder, kind="breaker_trip", severity="critical",
            raised_at=T0, message="synthetic",
        )
        classification = CauseClassification(
            classification_id="CAUSE-SYNTH", incident_id="INC-SYNTH",
            pack_version="2026.10.1", cause_class="equipment_failure",
            rule_id=None, inputs={}, classified_at=T0,
        )
        return incident, anchor, classification

    probe = compute_estimate(
        *_synthetic("SEN-1201", "FDR-12", "SUB-SOUTH"),
        svc.topology, svc.roster, T0 + timedelta(hours=2),
    )
    if probe.missing_inputs != ["base_rate"]:
        nc_errors += 1
    no_travel = dict(ETR_PACK)
    no_travel["travel_minutes"] = {"SUB-ALDER": {"SUB-ALDER": 25}}
    probe = compute_estimate(
        *_synthetic("REG-1202", "FDR-12", "SUB-SOUTH"),
        svc.topology, svc.roster, T0 + timedelta(hours=2),
        pack=no_travel,
    )
    if probe.missing_inputs != ["travel_minutes"]:
        nc_errors += 1
    counters["etr_not_computable_errors"] = nc_errors

    # The brief assembled in the R1 loop carries its ETR with the
    # basis label; a bare-time brief is refused outright.
    flood_brief = svc.store.list_briefs(flood_incident.incident_id)[-1]
    counters["etr_brief_basis_missing"] = (
        0
        if any(
            s.text.startswith("Estimated restoration")
            and "computed_from_rates" in s.text
            for s in flood_brief.statements
        )
        else 1
    )

    class _BareTime:
        profile = "offline"

        def propose_brief(self, facts):
            return [
                BriefStatement(
                    text="Restoration is expected by 2026-10-14T06:23:00+00:00.",
                    citations=[
                        Citation(evidence_item_id="EV-ALM-FLD-001", field="kind")
                    ],
                )
            ]

    try:
        IncidentAssembler(
            svc.store, svc.chain, _BareTime(),
            cause=svc.cause, memory=svc.memory, etr=svc.etr,
        ).assemble(flood_incident.incident_id, T0 + timedelta(minutes=30))
        counters["etr_basis_violations_accepted"] = 1
    except EtrBasisRefusal:
        counters["etr_basis_violations_accepted"] = 0

    # Closure: the flood restored at 06:43 — actual 270 against
    # the estimated 250 of record; the lone trip was never
    # computably estimated, and the ledger must show both facts.
    closure_errors = 0
    _closure, outcome, _record = svc.closure.close_incident(
        flood_incident.incident_id,
        "op.dana",
        "operator",
        T0 + timedelta(minutes=270),
        "Vegetation crew cleared the tree contact at REC-071; Feeder 7 restored.",
        T0 + timedelta(minutes=300),
    )
    if not (
        outcome.status == "compared"
        and outcome.estimate_id == flood_etr.estimate_id
        and outcome.estimated_duration_minutes == 250.0
        and outcome.actual_duration_minutes == 270.0
        and outcome.signed_error_minutes == 20.0
    ):
        closure_errors += 1
    try:
        svc.closure.close_incident(
            flood_incident.incident_id,
            "op.dana",
            "operator",
            T0 + timedelta(minutes=270),
            "Closed twice.",
            T0 + timedelta(minutes=300),
        )
        closure_errors += 1  # a second closure was accepted
    except ClosureRefusal:
        pass
    counters["closure_flow_errors"] = closure_errors

    svc.closure.close_incident(
        lone_incident.incident_id,
        "op.dana",
        "operator",
        lone_alarms()[0].raised_at + timedelta(minutes=60),
        "Breaker reclosed after inspection; no fault found.",
        lone_alarms()[0].raised_at + timedelta(minutes=75),
    )
    ledger = svc.closure.accuracy_ledger()
    counters["etr_accuracy_errors"] = (
        0
        if (
            ledger["by_cause_class"]
            == [
                {
                    "cause_class": "vegetation_contact",
                    "compared_count": 1,
                    "mean_signed_error_minutes": 20.0,
                    "mean_absolute_error_minutes": 20.0,
                    "max_absolute_error_minutes": 20.0,
                }
            ]
            and ledger["overall"]["compared_count"] == 1
            and ledger["overall"]["mean_absolute_error_minutes"] == 20.0
            and ledger["unestimated_closures"] == 1
        )
        else 1
    )

    # ---- Round 5: work orders, graded against the planted fixtures ----
    # The flood was closed above with its T+30 ETR of record; the
    # draft drawn at the same as-of pins exactly that estimate.
    wo = svc.work_orders
    wo_at = T0 + timedelta(minutes=30)
    win_ok = (T0 + timedelta(minutes=90), T0 + timedelta(minutes=270))
    flood_order = wo.draft(flood_incident.incident_id, wo_at)

    fixture_errors = 0
    if flood_order.drafted_by != "system" or flood_order.version != 1:
        fixture_errors += 1
    if flood_order.cause_class != "vegetation_contact":
        fixture_errors += 1
    if flood_order.estimate_id != flood_etr.estimate_id:
        fixture_errors += 1
    if [
        (d["device_id"], d["kind"]) for d in flood_order.affected_devices
    ] != [
        ("BRK-070", "feeder_breaker"),
        ("REC-071", "recloser"),
        ("SEC-072", "sectionalizer"),
        ("SEC-073", "sectionalizer"),
        ("SEN-0722", "line_sensor"),
        ("SEN-0732", "line_sensor"),
        ("XF-0721", "transformer"),
        ("XF-0731", "transformer"),
    ]:
        fixture_errors += 1
    if [s["device_kind"] for s in flood_order.safety_notes] != [
        "feeder_breaker",
        "recloser",
        "sectionalizer",
        "line_sensor",
        "transformer",
    ]:
        fixture_errors += 1
    if flood_order.required_qualification != "vegetation_clearing":
        fixture_errors += 1
    if flood_order.materials != WORK_PACK["classes"]["vegetation_contact"][
        "materials"
    ]:
        fixture_errors += 1
    if flood_order.gaps != [] or wo.state_of(flood_order) != "draft":
        fixture_errors += 1
    lone_order = wo.draft(lone_incident.incident_id, T0 + timedelta(hours=8))
    if not (
        lone_order.cause_class == "unknown"
        and lone_order.required_qualification is None
        and lone_order.gaps == ["required_qualification"]
    ):
        fixture_errors += 1
    counters["work_order_fixture_errors"] = fixture_errors

    assign_errors = 0
    try:
        wo.assign(
            flood_order.order_id, "CREW-NORTH-VEG", "op.dana",
            T0 + timedelta(minutes=30), T0 + timedelta(minutes=250), wo_at,
        )
        assign_errors += 1  # the roster-committed window was accepted
    except WorkOrderRefusal as exc:
        if not any("INC-EXT-2201" in p for p in exc.problems):
            assign_errors += 1
    try:
        wo.assign(
            flood_order.order_id, "CREW-SOUTH-EQUIP", "op.dana",
            win_ok[0], win_ok[1], wo_at,
        )
        assign_errors += 1  # an unqualified crew was accepted
    except WorkOrderRefusal:
        pass
    try:
        wo.assign(
            lone_order.order_id, "CREW-ALDER-LINE", "op.dana",
            T0 + timedelta(hours=9), T0 + timedelta(hours=11), wo_at,
        )
        assign_errors += 1  # the unclassified draft was assigned
    except WorkOrderRefusal:
        pass
    flood_assignment = wo.assign(
        flood_order.order_id, "CREW-NORTH-VEG", "op.dana",
        win_ok[0], win_ok[1], wo_at,
    )
    # A second vegetation incident the same evening: the crew's
    # flood window is a live double-booking constraint.
    veg2_base = datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)
    svc.intake.ingest(
        [
            Alarm(
                alarm_id="ALM-VEG2-001", stream_id="STREAM-SCADA-NORTH",
                device_id="BRK-070", feeder_id="FDR-07", kind="breaker_trip",
                severity="critical", raised_at=veg2_base,
                message="Feeder 7 breaker tripped again",
            ),
            Alarm(
                alarm_id="ALM-VEG2-002", stream_id="STREAM-SCADA-NORTH",
                device_id="REC-071", feeder_id="FDR-07",
                kind="recloser_lockout", severity="critical",
                raised_at=veg2_base + timedelta(seconds=30),
                message="Recloser R1 locked out",
            ),
            Alarm(
                alarm_id="ALM-VEG2-003", stream_id="STREAM-SCADA-NORTH",
                device_id="SEC-072", feeder_id="FDR-07", kind="phase_loss",
                severity="major",
                raised_at=veg2_base + timedelta(seconds=60),
                message="Phase loss reported by SEC-072",
            ),
        ]
    )
    veg2 = svc.correlator.correlate_new(svc.topology)
    if len(veg2) != 1:
        assign_errors += 1
    else:
        veg2_order = wo.draft(veg2[0].incident_id, T0 + timedelta(hours=18))
        try:
            wo.assign(
                veg2_order.order_id, "CREW-NORTH-VEG", "op.dana",
                T0 + timedelta(minutes=240), T0 + timedelta(minutes=540),
                wo_at,
            )
            assign_errors += 1  # a double-booking was accepted
        except WorkOrderRefusal as exc:
            if not any(
                flood_assignment.assignment_id in p for p in exc.problems
            ):
                assign_errors += 1
    counters["work_order_assignment_errors"] = assign_errors

    approval_errors = 0
    lone_v2 = wo.revise(
        lone_order.order_id, "op.rivera", wo_at, operator_note="For review."
    )
    try:
        wo.decide(lone_v2.order_id, "approved", "op.dana", "operator", wo_at)
        approval_errors += 1  # approval with no crew assignment landed
    except WorkOrderRefusal:
        pass
    revised = wo.revise(
        flood_order.order_id, "op.rivera", wo_at,
        operator_note="Chipper to be collected from the Alder depot gate.",
    )
    wo.assign(
        revised.order_id, "CREW-NORTH-VEG", "op.dana",
        win_ok[0], win_ok[1], wo_at,
    )
    try:
        wo.decide(revised.order_id, "approved", "op.rivera", "operator", wo_at)
        approval_errors += 1  # self-approval of a human revision landed
    except WorkOrderRefusal:
        pass
    try:
        wo.decide(revised.order_id, "approved", "op.dana", "field_crew", wo_at)
        approval_errors += 1  # a wrong-role approval landed
    except WorkOrderRefusal:
        pass
    act, notifications = wo.decide(
        revised.order_id, "approved", "op.dana", "operator", wo_at
    )
    if act.order_id != revised.order_id:
        approval_errors += 1
    if wo.state_of(revised) != "approved":
        approval_errors += 1
    if len(notifications) != 2 or act.notification_ids != [
        n.notification_id for n in notifications
    ]:
        approval_errors += 1
    try:
        wo.decide(
            revised.order_id, "rejected", "op.dana", "operator", wo_at,
            reason="too late",
        )
        approval_errors += 1  # a decided version was decided again
    except WorkOrderRefusal:
        pass
    counters["work_order_approval_errors"] = approval_errors

    rejection_errors = 0
    try:
        wo.decide(lone_v2.order_id, "rejected", "op.dana", "operator", wo_at)
        rejection_errors += 1  # a reasonless rejection landed
    except WorkOrderRefusal:
        pass
    wo.decide(
        lone_v2.order_id, "rejected", "op.dana", "operator", wo_at,
        reason="Cause unclassified — classify before drafting field work.",
    )
    if wo.state_of(lone_v2) != "rejected":
        rejection_errors += 1
    try:
        wo.decide(lone_v2.order_id, "approved", "op.dana", "operator", wo_at)
        rejection_errors += 1  # a rejected version was approved
    except WorkOrderRefusal:
        pass
    try:
        wo.decide(flood_order.order_id, "approved", "op.dana", "operator", wo_at)
        rejection_errors += 1  # a superseded version was approved
    except WorkOrderRefusal:
        pass
    post = wo.revise(
        revised.order_id, "op.dana", wo_at,
        operator_note="Second tree found on the section.",
    )
    if not (
        post.version == 3
        and post.supersedes == revised.order_id
        and wo.state_of(post) == "draft"
        and wo.state_of(revised) == "approved"
    ):
        rejection_errors += 1
    counters["work_order_rejection_errors"] = rejection_errors

    # Notifications are drafts, and nothing in the package can
    # send one: scan the registry, the routes, the Runtime actions
    # and every function name for a send-shaped surface.
    import re as _re
    from pathlib import Path as _Path

    send_surfaces = 0
    for incident in incidents:
        for note in svc.store.list_notifications(incident.incident_id):
            if not note.body.startswith("DRAFT notification — not sent"):
                send_surfaces += 1
    src_root = _Path(__file__).resolve().parents[1] / "src" / "grid_triage"
    for route in _re.findall(
        r'@app\.(?:get|post)\("([^"]+)"', (src_root / "api.py").read_text()
    ):
        if any(v in route for v in ("send", "deliver", "transmit", "dispatch")):
            send_surfaces += 1
    for action in _re.findall(
        r'action == "([^"]+)"', (src_root / "runtime_app.py").read_text()
    ):
        if any(v in action for v in ("send", "deliver", "transmit")):
            send_surfaces += 1
    for path in src_root.glob("*.py"):
        for name in _re.findall(r"def (\w+)", path.read_text()):
            if name.startswith(("send_", "deliver_", "transmit_")):
                send_surfaces += 1
    counters["notification_send_surfaces"] = send_surfaces

    # ---- Round 6: field updates, v2 declaration, re-correlation ----
    # A fresh Feeder 7 incident the next day, with two alarms on
    # the section v2 splits away and one update alarm from the
    # sensor only v2 knows.
    r6_base = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
    svc.intake.ingest(
        [
            Alarm(alarm_id="ALM-R6-001", stream_id="STREAM-SCADA-NORTH",
                  device_id="BRK-070", feeder_id="FDR-07", kind="breaker_trip",
                  severity="critical", raised_at=r6_base,
                  message="Feeder 7 breaker tripped on protection"),
            Alarm(alarm_id="ALM-R6-002", stream_id="STREAM-SCADA-NORTH",
                  device_id="BRK-070", feeder_id="FDR-07",
                  kind="relay_protection", severity="critical",
                  raised_at=r6_base + timedelta(seconds=30),
                  message="Distance relay operated, zone 1"),
            Alarm(alarm_id="ALM-R6-003", stream_id="STREAM-SCADA-NORTH",
                  device_id="SEC-072", feeder_id="FDR-07", kind="phase_loss",
                  severity="major", raised_at=r6_base + timedelta(seconds=60),
                  message="Phase loss reported by SEC-072"),
            Alarm(alarm_id="ALM-R6-004", stream_id="STREAM-SCADA-NORTH",
                  device_id="SEC-073", feeder_id="FDR-07", kind="voltage_sag",
                  severity="major", raised_at=r6_base + timedelta(seconds=90),
                  message="Voltage sag reported by SEC-073"),
            Alarm(alarm_id="ALM-R6-005", stream_id="STREAM-SCADA-NORTH",
                  device_id="XF-0731", feeder_id="FDR-07",
                  kind="transformer_temp_high", severity="major",
                  raised_at=r6_base + timedelta(seconds=120),
                  message="Transformer T31 temperature high"),
        ]
    )
    r6_created = svc.correlator.correlate_new(svc.topology)
    r6_errors = 0
    if len(r6_created) != 1 or r6_created[0].incident_id != "INC-ALM-R6-001":
        r6_errors += 1
    r6_incident = r6_created[0]
    r6_snapshot = r6_incident.model_dump(mode="json")
    r6_members_before = {
        m.alarm_id for m in svc.store.list_memberships(r6_incident.incident_id)
    }
    if r6_members_before != {
        "ALM-R6-001", "ALM-R6-002", "ALM-R6-003", "ALM-R6-004", "ALM-R6-005",
    }:
        r6_errors += 1

    sensor_update = svc.updates.post_update(
        r6_incident.incident_id, "alarm_observation", "crew.lee",
        "field_crew", r6_base + timedelta(minutes=20),
        alarm=Alarm(
            alarm_id="ALM-R6-101", stream_id="STREAM-SCADA-NORTH",
            device_id="SEN-0723", feeder_id="FDR-07",
            kind="line_sensor_fault", severity="minor",
            raised_at=r6_base + timedelta(minutes=2),
            message="New sensor LS23 reports a fault indication",
        ),
    )
    counters["pinned_topology_update_errors"] = (
        0
        if (
            sensor_update.correlation["status"]
            == "unresolvable_under_pinned_topology"
            and "SEN-0723" in sensor_update.correlation["reason"]
            and svc.store.membership_for_alarm("ALM-R6-101") is None
        )
        else 1
    )

    svc.topology_acts.declare(
        "v2", "op.dana", "operator",
        datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc),
    )
    counters["v1_incident_mutation_errors"] = (
        0
        if (
            svc.store.get_incident(r6_incident.incident_id).model_dump(mode="json")
            == r6_snapshot
            and {
                m.alarm_id
                for m in svc.store.list_memberships(r6_incident.incident_id)
            }
            == r6_members_before
            and svc.store.get_incident(flood_incident.incident_id)
            .model_dump(mode="json")
            == flood_incident.model_dump(mode="json")
        )
        else 1
    )

    closed_accepted = 0
    for closed_id in (flood_incident.incident_id, lone_incident.incident_id):
        try:
            svc.revisions.recorrelate(
                closed_id, "op.dana", "operator",
                datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc),
            )
            closed_accepted += 1  # a closed incident was re-correlated
        except RevisionRefusal as exc:
            if not any("closed" in p for p in exc.problems):
                closed_accepted += 1
    counters["closed_recorrelation_accepted"] = closed_accepted

    revision = svc.revisions.recorrelate(
        r6_incident.incident_id, "op.dana", "operator",
        datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc),
    )
    if revision.diff["alarms_added"] != ["ALM-R6-101"]:
        r6_errors += 1
    if revision.diff["alarms_removed"] != ["ALM-R6-004", "ALM-R6-005"]:
        r6_errors += 1
    if revision.diff["devices_removed"] != ["SEC-073", "XF-0731"]:
        r6_errors += 1
    if revision.diff["devices_added"] != ["SEN-0723"]:
        r6_errors += 1
    change = revision.severity_change
    if not (
        change["changed"]
        and (change["prior_band"], change["prior_score"]) == ("SEV-1", 84)
        and (change["revised_band"], change["revised_score"]) == ("SEV-2", 64)
        and change["moved_inputs"].get("customers_affected")
        == {"prior": 1440, "revised": 760}
    ):
        r6_errors += 1
    if len(svc.store.list_memberships(revision.revised_incident_id)) != 4:
        r6_errors += 1
    counters["revision_diff_errors"] = r6_errors

    lone_members = {
        m.alarm_id: svc.store.get_alarm(m.alarm_id)
        for m in svc.store.list_memberships(lone_incident.incident_id)
    }
    lone_v2 = compute_assessment(
        lone_incident,
        lone_members,
        TopologyView(svc.store.get_topology_payload("v2")),
        lone_alarms()[0].raised_at + timedelta(minutes=15),
    )
    counters["lone_v2_severity_errors"] = (
        0 if (lone_v2.band == "SEV-2" and lone_v2.score == 58) else 1
    )

    print("Evals — grid alarm triage agent (Rounds 1-6)")
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
