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
first entry. The demo closes with a declared storm: storm mode
entered by a named operator's act, a burst correlated under
the widened rules, and the post-storm review assembled after
stand-down.
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

    # Round 7: the close is earned from signals, not asserted.
    restored_at = T0 + timedelta(hours=4, minutes=30)  # 06:43 UTC
    for device_id in (
        "BRK-070", "REC-071", "SEC-072", "SEC-073",
        "XF-0721", "XF-0731", "SEN-0722", "SEN-0732",
    ):
        svc.restoration.record_signal(
            flood.incident_id, "meter", "disp.ray", "dispatcher",
            restored_at - timedelta(minutes=5), "restored",
            device_id=device_id,
        )
    svc.restoration.record_signal(
        flood.incident_id, "topology", "disp.ray", "dispatcher",
        restored_at - timedelta(minutes=4), "supplied", feeder_id="FDR-07",
    )
    confirmation = svc.restoration.confirm(flood.incident_id, restored_at)
    print(
        f"\nRestoration confirmation {confirmation.confirmation_id}: "
        f"{confirmation.status} — {confirmation.restored_customers} of "
        f"{confirmation.total_customers} customers restored, classes "
        f"{ {k: v['present'] for k, v in confirmation.classes.items()} }."
    )
    completion = svc.work_orders.complete(
        revised.order_id, "crew.lee", "field_crew",
        restored_at - timedelta(minutes=2),
        note="Vegetation cleared and the section patrolled.",
    )
    print(
        f"Work order {revised.order_id} completion recorded by "
        f"{completion.completed_by} ({completion.completion_id})."
    )
    act, closure, outcome, _record = svc.restoration.close_incident(
        flood.incident_id,
        "op.dana",
        "operator",
        restored_at,
        "Vegetation crew cleared the tree contact at REC-071; "
        "Feeder 7 restored after inspection.",
        recorded_at=now,
    )
    print(
        f"Gated close {act.act_id}: binds confirmation "
        f"{act.confirmation_id}, closure {closure.closure_id} and "
        f"outcome {outcome.outcome_id} — restored {restored_at}, actual "
        f"duration {outcome.actual_duration_minutes} min against the "
        f"estimated {outcome.estimated_duration_minutes} min (signed "
        f"error {outcome.signed_error_minutes:+} min)."
    )
    ledger = svc.closure.accuracy_ledger()
    print(f"ETR accuracy ledger: {ledger['by_cause_class']}")
    log = svc.event_log.build_log(flood.incident_id)
    problems = svc.event_log.verify_log(flood.incident_id, log)
    print(
        f"Event log for the flood: {log['line_count']} lines, every one "
        f"carrying its artefact id; verifier problems: {problems}."
    )

    # Round 7: a partial restoration the next morning is split,
    # never closed whole.
    day2 = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    svc.intake.ingest(
        [
            _Alarm(alarm_id="ALM-SPL-001", stream_id="STREAM-SCADA-NORTH",
                   device_id="BRK-070", feeder_id="FDR-07",
                   kind="breaker_trip", severity="critical",
                   raised_at=day2,
                   message="Feeder 7 breaker tripped on protection"),
            _Alarm(alarm_id="ALM-SPL-002", stream_id="STREAM-SCADA-NORTH",
                   device_id="SEC-072", feeder_id="FDR-07",
                   kind="phase_loss", severity="major",
                   raised_at=day2 + timedelta(seconds=40),
                   message="Phase loss reported by SEC-072"),
            _Alarm(alarm_id="ALM-SPL-003", stream_id="STREAM-SCADA-NORTH",
                   device_id="SEC-073", feeder_id="FDR-07",
                   kind="voltage_sag", severity="major",
                   raised_at=day2 + timedelta(seconds=70),
                   message="Voltage sag reported by SEC-073"),
            _Alarm(alarm_id="ALM-SPL-004", stream_id="STREAM-SCADA-NORTH",
                   device_id="XF-0731", feeder_id="FDR-07",
                   kind="transformer_temp_high", severity="major",
                   raised_at=day2 + timedelta(seconds=100),
                   message="Transformer T31 temperature high"),
        ]
    )
    split_source = svc.correlator.correlate_new(svc.topology)[0]
    svc.updates.post_update(
        split_source.incident_id, "crew_note", "crew.lee", "field_crew",
        day2 + timedelta(minutes=25),
        note="North section back on; the SEC-073 branch is still out.",
    )
    for device_id in ("BRK-070", "REC-071", "SEC-072", "SEN-0722", "XF-0721"):
        svc.restoration.record_signal(
            split_source.incident_id, "meter", "disp.ray", "dispatcher",
            day2 + timedelta(minutes=26), "restored", device_id=device_id,
        )
    partial = svc.restoration.confirm(
        split_source.incident_id, day2 + timedelta(minutes=27)
    )
    print(
        f"\nNext-morning incident {split_source.incident_id}: "
        f"confirmation {partial.status} — {partial.restored_customers} "
        f"customers restored, {partial.outstanding_customers} still out "
        f"on {partial.outstanding_devices}. It cannot close whole."
    )
    split = svc.restoration.split_incident(
        split_source.incident_id, "op.dana", "operator",
        day2 + timedelta(minutes=30),
    )
    print(
        f"Split {split.split_id}: restored part "
        f"{split.restored_incident_id} ({split.restored_customers} "
        f"customers), continuing incident "
        f"{split.continuing_incident_id} ({split.continuing_customers} "
        "customers still out)."
    )
    svc.restoration.record_signal(
        split.restored_incident_id, "topology", "disp.ray", "dispatcher",
        day2 + timedelta(minutes=35), "supplied", feeder_id="FDR-07",
    )
    part_confirmation = svc.restoration.confirm(
        split.restored_incident_id, day2 + timedelta(minutes=36)
    )
    part_act, _c, part_outcome, _r = svc.restoration.close_incident(
        split.restored_incident_id,
        "op.dana",
        "operator",
        day2 + timedelta(minutes=40),
        "North section of Feeder 7 restored and inspected; the "
        "remainder continues as its own incident.",
        recorded_at=day2 + timedelta(minutes=41),
    )
    print(
        f"Restored part confirmed {part_confirmation.status} and closed "
        f"({part_act.act_id}); its ETR outcome is "
        f"{part_outcome.status} — no estimate was ever computed for the "
        "part, and the ledger shows that gap rather than hiding it. "
        f"The continuing incident stays open: "
        f"{svc.store.get_incident_closure(split.continuing_incident_id) is None}."
    )

    # Round 7: an alarm on the closed flood's device set does not
    # reopen it — it contradicts the restoration and opens fresh.
    contra_update = svc.updates.post_update(
        flood.incident_id, "alarm_observation", "disp.ray", "dispatcher",
        restored_at + timedelta(hours=2),
        alarm=_Alarm(
            alarm_id="ALM-POST-001", stream_id="STREAM-SCADA-NORTH",
            device_id="REC-071", feeder_id="FDR-07",
            kind="recloser_lockout", severity="critical",
            raised_at=restored_at + timedelta(hours=2),
            message="Recloser R1 locked out again after restoration",
        ),
    )
    print(
        f"\nPost-close alarm on REC-071: {contra_update.correlation['status']} "
        f"— {contra_update.correlation['reason']}"
    )
    print(f"Evidence chain still valid: {svc.chain.verify_chain(flood.incident_id)}")

    # Round 8: two days later a storm cell crosses the territory.
    # Storm mode is declared by a named operator — the burst
    # alone would never declare it.
    storm_day = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)
    declaration = svc.storm.declare(
        "op.dana",
        "operator",
        "Storm cell crossing the Alder service area; damage "
        "expected across the north feeders",
        storm_day,
        feeders=["FDR-07"],
    )
    print(
        f"\nStorm mode declared by {declaration.declared_by} "
        f"({declaration.declaration_id}): scope "
        f"{declaration.feeders}, reason on the record. Before the "
        f"act, the state read for FDR-07 was "
        f"{svc.storm.state_at('FDR-07', storm_day - timedelta(minutes=1))['mode']}."
    )
    burst_t0 = storm_day + timedelta(hours=1)  # 02:00 UTC
    svc.intake.ingest(
        [
            _Alarm(alarm_id="ALM-STM-001", stream_id="STREAM-SCADA-NORTH",
                   device_id="BRK-070", feeder_id="FDR-07",
                   kind="breaker_trip", severity="critical",
                   raised_at=burst_t0,
                   message="Feeder 7 breaker tripped on protection"),
            _Alarm(alarm_id="ALM-STM-002", stream_id="STREAM-SCADA-NORTH",
                   device_id="BRK-070", feeder_id="FDR-07",
                   kind="relay_protection", severity="critical",
                   raised_at=burst_t0 + timedelta(seconds=20),
                   message="Distance relay operated, zone 1"),
            _Alarm(alarm_id="ALM-STM-003", stream_id="STREAM-SCADA-NORTH",
                   device_id="REC-071", feeder_id="FDR-07",
                   kind="recloser_lockout", severity="critical",
                   raised_at=burst_t0 + timedelta(seconds=40),
                   message="Recloser R1 locked out after unsuccessful reclose attempts"),
            _Alarm(alarm_id="ALM-STM-004", stream_id="STREAM-SCADA-NORTH",
                   device_id="SEN-0722", feeder_id="FDR-07",
                   kind="meter_ping_failure", severity="info",
                   raised_at=burst_t0 + timedelta(minutes=5),
                   message="Meter ping failure on SEN-0722 — AMI silent"),
            _Alarm(alarm_id="ALM-STM-005", stream_id="STREAM-SCADA-NORTH",
                   device_id="SEC-072", feeder_id="FDR-07",
                   kind="voltage_sag", severity="major",
                   raised_at=burst_t0 + timedelta(minutes=12),
                   message="Voltage sag reported by SEC-072"),
            _Alarm(alarm_id="ALM-STM-006", stream_id="STREAM-SCADA-NORTH",
                   device_id="SEC-073", feeder_id="FDR-07",
                   kind="phase_loss", severity="major",
                   raised_at=burst_t0 + timedelta(minutes=20),
                   message="Phase loss reported by SEC-073"),
        ]
    )
    storm_created = svc.correlator.correlate_new(svc.topology)
    storm_incident = next(
        i for i in storm_created if i.anchor_alarm_id == "ALM-STM-001"
    )
    storm_members = svc.store.list_memberships(storm_incident.incident_id)
    print(
        f"Storm burst correlated as {storm_incident.incident_id}: "
        f"{len(storm_members)} alarms — the sag at +12 min joins "
        "only through the declared window (1800s, not 600s), and "
        "the meter ping joins only because the storm pack names "
        f"it joinable. Stamp on its face: mode "
        f"{storm_incident.operating_mode}, storm pack "
        f"{storm_incident.storm_pack_version}, declaration "
        f"{storm_incident.storm_declaration_id}."
    )
    storm_estimate = svc.etr.estimate(
        storm_incident.incident_id, burst_t0 + timedelta(minutes=30)
    )
    storm_components = {
        c["name"]: c["minutes"] for c in storm_estimate.components
    }
    print(
        f"Storm ETR (mode {storm_estimate.operating_mode}): "
        f"restoration {storm_estimate.estimated_restored_at} (basis: "
        f"{storm_estimate.basis}) — base repair "
        f"{storm_components['base_repair']} min plus "
        f"{storm_components['storm_conditions']} min of stated "
        "storm_conditions (the pack's 6/5 factor, its own line)."
    )
    extension = svc.storm.extend(
        declaration.declaration_id,
        "op.dana",
        "operator",
        "Cell widening — Feeder 3 added to the storm scope",
        storm_day + timedelta(hours=2),
        feeders=["FDR-07", "FDR-03"],
    )
    print(
        f"Declaration extended ({extension.declaration_id}): scope "
        f"now {extension.feeders} — from the extension's hour on; "
        "the fold for 02:30 still shows the original scope."
    )
    storm_assessment = svc.severity.assess(
        storm_incident.incident_id, burst_t0 + timedelta(minutes=90)
    )
    adjustments = storm_assessment.inputs["storm_adjustments"]
    print(
        f"Storm severity: {storm_assessment.band}, score "
        f"{storm_assessment.score} — points "
        f"{storm_assessment.inputs['points']}, under the pack's "
        "stated adjustments "
        + ", ".join(
            f"{key} {pair['normal']}→{pair['storm']}"
            for key, pair in adjustments.items()
        )
        + "."
    )
    _, storm_outcome, _ = svc.closure.close_incident(
        storm_incident.incident_id,
        "op.dana",
        "operator",
        burst_t0 + timedelta(minutes=400),
        "Storm damage on Feeder 7 cleared; feeder restored after "
        "the cell passed.",
        recorded_at=burst_t0 + timedelta(minutes=401),
    )
    print(
        f"Storm incident closed: actual "
        f"{storm_outcome.actual_duration_minutes} min against the "
        f"estimated {storm_outcome.estimated_duration_minutes} "
        f"(signed error {storm_outcome.signed_error_minutes:+}) — "
        f"the outcome records mode {storm_outcome.operating_mode}."
    )
    from grid_triage.storm import StormRefusal

    try:
        svc.storm.compute_review(
            declaration.declaration_id, "op.dana", storm_day + timedelta(hours=6)
        )
        print("ERROR: a review was computed over an open window")
    except StormRefusal as exc:
        print(f"Post-storm review refused while the window is open: {exc.problems[0]}")
    stand_down = svc.storm.stand_down(
        declaration.declaration_id,
        "op.dana",
        "operator",
        "Cell has passed; damage assessment complete",
        storm_day + timedelta(hours=11),
    )
    print(
        f"Storm mode stood down by {stand_down.declared_by} at "
        f"{stand_down.declared_at} — the storm incident keeps its "
        f"stamp: {svc.store.get_incident(storm_incident.incident_id).operating_mode}."
    )
    review = svc.storm.compute_review(
        declaration.declaration_id, "op.dana", storm_day + timedelta(hours=11, minutes=5)
    )
    review_problems = svc.storm.verify_review(review.review_id)
    print(
        f"Post-storm review {review.review_id}: figures "
        f"{review.figures}; verifier problems: {review_problems}."
    )
    ledger = svc.closure.accuracy_ledger()
    print(f"ETR accuracy by operating mode: {ledger['by_operating_mode']}")
    return svc


def main() -> int:
    run_demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
