"""Demo: the planted streams end to end, offline.

Flood in → one incident out. Nuisance stream → no incident, every
alarm still visible. Lone trip → its own incident. Then the agent
assembles the flood incident's brief, every statement cited —
including the prior incidents retrieved from memory — the
evidence pack is sealed and verified, the ETR is computed with
its basis on the record, a work order is drafted, revised,
assigned and approved through the operator gate (with the
refusals the gate exists for). Then the field reports back,
topology v2 is declared effective, and the flood is
re-correlated under it — the revision's diff told plainly —
before the flood is closed so the accuracy ledger gains its
first entry.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from grid_triage.config import Settings
from grid_triage.seed import FLOOD_ALARM_IDS, NUISANCE_ALARM_IDS
from grid_triage.wiring import Services


def run_demo(settings: Settings | None = None) -> Services:
    if settings is None:
        tmp = Path(tempfile.mkdtemp(prefix="grid-demo-"))
        settings = Settings(
            sqlite_path=str(tmp / "grid.db"), object_store_dir=str(tmp / "objects")
        )
    svc = Services(settings)
    count = svc.intake.ingest_feed(svc.feed.fetch_alarms())
    created = svc.correlator.correlate_new(svc.topology)

    print(f"Ingested {count} alarms (append-only).")
    print(f"Incidents created: {len(created)}")
    for incident in created:
        members = svc.store.list_memberships(incident.incident_id)
        print(
            f"  {incident.incident_id} on {incident.feeder_id}: "
            f"{len(members)} alarms, anchor {incident.anchor_alarm_id}, "
            f"topology {incident.topology_version}"
        )
    uncorrelated = svc.store.list_uncorrelated_alarms()
    print(
        f"Uncorrelated alarms (visible, never dropped): {len(uncorrelated)} "
        f"— the {len(NUISANCE_ALARM_IDS)}-alarm nuisance stream among them."
    )
    flood = next(i for i in created if i.anchor_alarm_id == FLOOD_ALARM_IDS[0])
    members = {m.alarm_id for m in svc.store.list_memberships(flood.incident_id)}
    assert members == set(FLOOD_ALARM_IDS), "flood membership must be exact"
    print(f"Flood membership exact: {len(members)}/{len(FLOOD_ALARM_IDS)} alarms.")

    from datetime import timedelta

    from grid_triage.seed import T0

    assessment = svc.severity.assess(flood.incident_id, T0 + timedelta(minutes=30))
    print(
        f"Severity at T+30min (formula pack {assessment.pack_version}): "
        f"{assessment.band}, score {assessment.score} — "
        f"{assessment.inputs['customers_affected']} customers affected, "
        f"critical loads: "
        f"{[c['name'] for c in assessment.inputs['critical_loads']]}; "
        f"operator acknowledgement required: {assessment.escalation_required}."
    )

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    loaded = svc.memory.load_history()
    print(
        f"\nMemory: {len(loaded)} closed historical incidents loaded "
        "into the memory port (local double)."
    )
    classification = svc.cause.classify(flood.incident_id, now)
    print(
        f"Root-cause classification: {classification.cause_class} "
        f"(rule: {classification.rule_id}, pack {classification.pack_version})."
    )
    estimate = svc.etr.estimate(flood.incident_id, T0 + timedelta(minutes=30))
    components = {c["name"]: c["minutes"] for c in estimate.components}
    print(
        f"ETR at T+30min (rate pack {estimate.rate_pack_version}, roster "
        f"{estimate.roster_version}): restoration {estimate.estimated_restored_at} "
        f"(basis: {estimate.basis}) — crew wait {components['crew_wait']} min "
        f"({estimate.inputs['crew_id']}), travel {components['travel_access']} min, "
        f"repair {components['base_repair']} min."
    )
    from grid_triage.work_orders import WorkOrderRefusal

    orders = svc.work_orders
    drafted = orders.draft(flood.incident_id, T0 + timedelta(minutes=35))
    print(
        f"\nWork order {drafted.order_id} drafted by {drafted.drafted_by} "
        f"(work pack {drafted.work_pack_version}): "
        f"cause {drafted.cause_class}, qualification "
        f"{drafted.required_qualification}, ETR pinned "
        f"{drafted.estimate_id}, {len(drafted.affected_devices)} devices, "
        f"safety notes for "
        f"{[s['device_kind'] for s in drafted.safety_notes]}, gaps "
        f"{drafted.gaps}. A draft authorises nothing."
    )
    try:
        orders.assign(
            drafted.order_id,
            "CREW-NORTH-VEG",
            "op.dana",
            T0 + timedelta(minutes=30),
            T0 + timedelta(minutes=250),
            T0 + timedelta(minutes=36),
        )
        print("ERROR: an overlapping roster commitment was accepted")
    except WorkOrderRefusal as exc:
        print(f"Assignment over the crew's existing commitment refused: "
              f"{exc.problems[0]}")
    revised = orders.revise(
        drafted.order_id,
        "op.rivera",
        T0 + timedelta(minutes=40),
        operator_note="Chipper to be collected from the Alder depot gate.",
    )
    assignment = orders.assign(
        revised.order_id,
        "CREW-NORTH-VEG",
        "op.dana",
        T0 + timedelta(minutes=90),
        T0 + timedelta(minutes=270),
        T0 + timedelta(minutes=41),
    )
    print(
        f"Version {revised.version} (revised by {revised.drafted_by}) "
        f"assigned: {assignment.crew_id} "
        f"{assignment.window_start.time()}–{assignment.window_end.time()} UTC."
    )
    try:
        orders.decide(
            revised.order_id, "approved", "op.rivera", "operator",
            T0 + timedelta(minutes=42),
        )
        print("ERROR: self-approval was accepted")
    except WorkOrderRefusal as exc:
        print(f"Self-approval refused: {exc.problems[0]}")
    decision, notifications = orders.decide(
        revised.order_id, "approved", "op.dana", "operator",
        T0 + timedelta(minutes=43),
    )
    print(
        f"Work order approved by {decision.decided_by} "
        f"({decision.role}) — the act pins version {revised.version} "
        f"({revised.order_id}); {len(notifications)} notifications drafted, "
        "none sent:"
    )
    for notification in notifications:
        print(f"  [{notification.channel}] {notification.body}")

    hits = svc.memory.retrieve_for_incident(flood.incident_id, now)
    for hit in hits:
        print(
            f"  prior {hit.record.incident_id} "
            f"({hit.record.cause_class}, closed "
            f"{hit.record.closed_at.date()}): score {hit.score}"
        )

    brief = svc.assembler.assemble(flood.incident_id, now)
    print(f"\nBrief {brief.brief_id} (profile: {brief.model_profile}):")
    for statement in brief.statements:
        cites = ", ".join(
            f"{c.evidence_item_id}:{c.field}" for c in statement.citations
        )
        print(f"  - {statement.text}  [{cites}]")

    pack = svc.packs.seal(flood.incident_id, "op.dana", now)
    problems = svc.packs.verify_pack(pack.pack_id)
    print(
        f"\nEvidence pack {pack.pack_id} sealed by op.dana: "
        f"{len(pack.members)} members, manifest {pack.manifest_hash[:16]}…, "
        f"verify problems: {problems}."
    )
    print(f"Evidence chain valid: {svc.chain.verify_chain(flood.incident_id)}")

    # Round 6: the territory changes and the field reports back.
    from grid_triage.models import Alarm as _Alarm

    note_update = svc.updates.post_update(
        flood.incident_id, "crew_note", "crew.lee", "field_crew",
        T0 + timedelta(minutes=47),
        note="Tree on the line at REC-071; the section is earthed and tagged.",
    )
    print(
        f"\nField update {note_update.update_id} recorded "
        f"({note_update.kind} by {note_update.reported_by})."
    )
    sensor_update = svc.updates.post_update(
        flood.incident_id, "alarm_observation", "crew.lee", "field_crew",
        T0 + timedelta(minutes=47),
        alarm=_Alarm(
            alarm_id="ALM-UPD-001", stream_id="STREAM-SCADA-NORTH",
            device_id="SEN-0723", feeder_id="FDR-07",
            kind="line_sensor_fault", severity="minor",
            raised_at=T0 + timedelta(minutes=3),
            message="New sensor LS23 reports a fault indication",
        ),
    )
    print(
        "Alarm from the new sensor SEN-0723 under the incident's pinned "
        f"topology v1: {sensor_update.correlation['status']} — "
        f"{sensor_update.correlation['reason']}"
    )
    declaration = svc.topology_acts.declare(
        "v2", "op.dana", "operator",
        T0 + timedelta(minutes=45), T0 + timedelta(minutes=46),
    )
    print(
        f"Topology v2 declared effective by {declaration.declared_by} "
        f"from {declaration.effective_at} — {len(declaration.change_set)} "
        "changes on the record:"
    )
    for change in declaration.change_set:
        print(f"  - {change['change']}: {change['detail']}")
    revision = svc.revisions.recorrelate(
        flood.incident_id, "op.dana", "operator", T0 + timedelta(minutes=50)
    )
    print(
        f"Re-correlated as {revision.revised_incident_id} under v2: "
        f"{len(revision.diff['alarms_removed'])} alarms leave with the "
        f"split section {revision.diff['devices_removed']}, "
        f"{revision.diff['alarms_added']} join, severity "
        f"{revision.severity_change['prior_band']} "
        f"({revision.severity_change['prior_score']}) → "
        f"{revision.severity_change['revised_band']} "
        f"({revision.severity_change['revised_score']}). The v1 incident "
        "stands untouched."
    )

    restored_at = T0 + timedelta(hours=4, minutes=30)  # 06:43 UTC
    closure, outcome, _record = svc.closure.close_incident(
        flood.incident_id,
        "op.dana",
        "operator",
        restored_at,
        "Vegetation crew cleared the tree contact at REC-071; "
        "Feeder 7 restored after inspection.",
        now,
    )
    print(
        f"\nClosure {closure.closure_id} recorded by {closure.closed_by}: "
        f"restored {restored_at} — actual duration "
        f"{outcome.actual_duration_minutes} min against the estimated "
        f"{outcome.estimated_duration_minutes} min (signed error "
        f"{outcome.signed_error_minutes:+} min)."
    )
    ledger = svc.closure.accuracy_ledger()
    print(f"ETR accuracy ledger: {ledger['by_cause_class']}")
    print(f"Evidence chain still valid: {svc.chain.verify_chain(flood.incident_id)}")
    return svc


def main() -> int:
    run_demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
