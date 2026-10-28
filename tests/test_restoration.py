"""Round 7: restoration signals and confirmations, the gated
close, work-order completion, partial-restoration splits and
post-close contradictions — graded against the planted fixtures.

The flood's outage set is the BRK-070 slice of topology v1:
BRK-070 (0 customers), REC-071 (0), SEC-072 (120), SEC-073 (95),
XF-0721 (640), XF-0731 (585), SEN-0722 (0), SEN-0732 (0) — 1440
customers in all. Every count below is derived from that table,
not from the code's own outputs.
"""

from datetime import datetime, timedelta, timezone

import pytest

from grid_triage.models import Alarm
from grid_triage.restoration import RestorationRefusal
from grid_triage.seed import T0
from grid_triage.work_orders import WorkOrderRefusal

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
FLOOD_DEVICES = [
    "BRK-070", "REC-071", "SEC-072", "SEC-073",
    "XF-0721", "XF-0731", "SEN-0722", "SEN-0732",
]
RESTORED_AT = T0 + timedelta(minutes=270)  # 06:43 UTC, as R4


def _ready_flood(fed):
    """The flood assessed, classified, estimated, with its work
    order approved and the crew's note on record."""
    at = T0 + timedelta(minutes=30)
    fed.severity.assess(FLOOD, at)
    fed.cause.classify(FLOOD, at)
    fed.etr.estimate(FLOOD, at)
    order = fed.work_orders.draft(FLOOD, at)
    fed.work_orders.assign(
        order.order_id, "CREW-NORTH-VEG", "op.dana",
        T0 + timedelta(minutes=90), T0 + timedelta(minutes=270), at,
    )
    fed.work_orders.decide(order.order_id, "approved", "op.dana", "operator", at)
    fed.updates.post_update(
        FLOOD, "crew_note", "crew.lee", "field_crew",
        T0 + timedelta(minutes=47),
        note="Tree on the line at REC-071; section restored and inspected.",
    )
    return order


def _signal(fed, incident_id, device_id, state="restored", at=None):
    return fed.restoration.record_signal(
        incident_id, "meter", "disp.ray", "dispatcher",
        at or (RESTORED_AT - timedelta(minutes=5)), state,
        device_id=device_id,
    )


def _full_signals(fed, incident_id=FLOOD, feeder_id="FDR-07"):
    for device_id in FLOOD_DEVICES:
        _signal(fed, incident_id, device_id)
    fed.restoration.record_signal(
        incident_id, "topology", "disp.ray", "dispatcher",
        RESTORED_AT - timedelta(minutes=4), "supplied", feeder_id=feeder_id,
    )


def _close_flood(fed, order):
    _full_signals(fed)
    confirmation = fed.restoration.confirm(FLOOD, RESTORED_AT)
    fed.work_orders.complete(
        order.order_id, "crew.lee", "field_crew",
        RESTORED_AT - timedelta(minutes=2),
    )
    return fed.restoration.close_incident(
        FLOOD, "op.dana", "operator", RESTORED_AT,
        "Vegetation crew cleared the tree contact at REC-071; "
        "Feeder 7 restored.",
        recorded_at=T0 + timedelta(minutes=300),
    ), confirmation


# -- signals ------------------------------------------------------------


def test_signal_pack_refusals(fed):
    with pytest.raises(RestorationRefusal):
        fed.restoration.record_signal(
            FLOOD, "carrier_pigeon", "disp.ray", "dispatcher",
            RESTORED_AT, "restored", device_id="BRK-070",
        )
    with pytest.raises(RestorationRefusal):
        fed.restoration.record_signal(
            FLOOD, "meter", "disp.ray", "dispatcher",
            RESTORED_AT, "supplied", device_id="BRK-070",
        )  # a meter signal does not carry the topology states
    with pytest.raises(RestorationRefusal):
        fed.restoration.record_signal(
            FLOOD, "meter", "crew.lee", "field_crew",
            RESTORED_AT, "restored", device_id="BRK-070",
        )  # crews report through field updates, not signals
    with pytest.raises(RestorationRefusal):
        fed.restoration.record_signal(
            FLOOD, "meter", "disp.ray", "dispatcher", RESTORED_AT, "restored"
        )  # no device named
    with pytest.raises(RestorationRefusal):
        fed.restoration.record_signal(
            FLOOD, "topology", "disp.ray", "dispatcher",
            RESTORED_AT, "supplied",
        )  # no feeder named
    with pytest.raises(KeyError):
        fed.restoration.record_signal(
            "INC-NOPE", "meter", "disp.ray", "dispatcher",
            RESTORED_AT, "restored", device_id="BRK-070",
        )


def test_signal_rerecording_is_a_noop(fed):
    first = _signal(fed, FLOOD, "BRK-070")
    again = _signal(fed, FLOOD, "BRK-070")
    assert again.signal_id == first.signal_id
    assert len(fed.restoration.list_signals(FLOOD)) == 1


# -- confirmations --------------------------------------------------------


def test_confirmation_complete_from_signals(fed):
    _ready_flood(fed)
    _full_signals(fed)
    confirmation = fed.restoration.confirm(FLOOD, RESTORED_AT)
    assert confirmation.status == "complete"
    assert confirmation.missing_classes == []
    assert confirmation.restored_customers == 1440
    assert confirmation.outstanding_customers == 0
    assert confirmation.total_customers == 1440
    assert confirmation.outstanding_devices == []
    assert sorted(confirmation.restored_devices) == sorted(FLOOD_DEVICES)
    meter = confirmation.classes["meter_restoration"]
    assert meter["present"] and len(meter["signal_ids"]) == 8
    assert confirmation.classes["feeder_supply_reading"]["present"]
    crew = confirmation.classes["crew_confirmation"]
    assert crew["present"] and len(crew["update_ids"]) == 1
    assert fed.chain.verify_chain(FLOOD)


def test_confirmation_partial_names_what_is_missing(fed):
    # No crew note, no feeder reading, SEC-073 unreported and
    # XF-0731 still out: the artefact must say all of it.
    for device_id in FLOOD_DEVICES:
        if device_id == "SEC-073":
            continue
        _signal(fed, FLOOD, device_id, "still_out" if device_id == "XF-0731" else "restored")
    confirmation = fed.restoration.confirm(FLOOD, RESTORED_AT)
    assert confirmation.status == "partial"
    assert "crew_confirmation" in confirmation.missing_classes
    assert "feeder_supply_reading" in confirmation.missing_classes
    assert "meter_restoration" in confirmation.missing_classes
    meter = confirmation.classes["meter_restoration"]
    assert meter["devices_without_readings"] == ["SEC-073"]
    assert sorted(confirmation.outstanding_devices) == ["SEC-073", "XF-0731"]
    assert confirmation.restored_customers == 1440 - 95 - 585
    assert confirmation.outstanding_customers == 95 + 585


def test_confirmation_supersedes_by_link_and_noops_when_identical(fed):
    _signal(fed, FLOOD, "BRK-070")
    first = fed.restoration.confirm(FLOOD, RESTORED_AT)
    assert first.status == "partial" and first.supersedes is None
    again = fed.restoration.confirm(FLOOD, RESTORED_AT)
    assert again.confirmation_id == first.confirmation_id
    assert len(fed.store.list_restoration_confirmations(FLOOD)) == 1
    _ready_crew = fed.updates.post_update(
        FLOOD, "crew_note", "crew.lee", "field_crew",
        T0 + timedelta(minutes=47), note="On site.",
    )
    assert _ready_crew is not None
    for device_id in FLOOD_DEVICES[1:]:
        _signal(fed, FLOOD, device_id)
    fed.restoration.record_signal(
        FLOOD, "topology", "disp.ray", "dispatcher",
        RESTORED_AT - timedelta(minutes=4), "supplied", feeder_id="FDR-07",
    )
    second = fed.restoration.confirm(FLOOD, RESTORED_AT + timedelta(minutes=1))
    assert second.status == "complete"
    assert second.supersedes == first.confirmation_id
    assert len(fed.store.list_restoration_confirmations(FLOOD)) == 2


def test_stale_signal_superseded_by_later_reading(fed):
    _ready_flood(fed)
    _full_signals(fed)
    fed.restoration.record_signal(
        FLOOD, "oms", "disp.ray", "dispatcher",
        RESTORED_AT + timedelta(minutes=10), "still_out",
        device_id="XF-0721",
    )
    confirmation = fed.restoration.confirm(FLOOD, RESTORED_AT + timedelta(minutes=11))
    assert confirmation.status == "partial"
    assert confirmation.outstanding_devices == ["XF-0721"]
    assert confirmation.outstanding_customers == 640


# -- the gated close ------------------------------------------------------


def test_gated_close_binds_confirmation_closure_and_outcome(fed):
    order = _ready_flood(fed)
    before = fed.store.get_incident(FLOOD).model_dump(mode="json")
    (act, closure, outcome, record), confirmation = _close_flood(fed, order)
    assert act.confirmation_id == confirmation.confirmation_id
    assert act.closure_id == closure.closure_id
    assert act.outcome_id == outcome.outcome_id
    assert act.memory_record_id == record.record_id
    assert act.completed_orders == [order.order_id]
    assert act.waived_orders == []
    # The accuracy record is the R4 one: actual 270 against the
    # estimated 250, signed +20 — linked, not duplicated.
    assert outcome.status == "compared"
    assert outcome.actual_duration_minutes == 270.0
    assert outcome.estimated_duration_minutes == 250.0
    assert outcome.signed_error_minutes == 20.0
    assert len(fed.store.list_etr_outcomes(FLOOD)) == 1
    ledger = fed.closure.accuracy_ledger()
    assert ledger["overall"]["compared_count"] == 1
    # The incident row itself is never mutated by a close.
    assert fed.store.get_incident(FLOOD).model_dump(mode="json") == before
    assert fed.chain.verify_chain(FLOOD)


def test_close_refused_without_any_confirmation(fed):
    order = _ready_flood(fed)
    fed.work_orders.complete(
        order.order_id, "crew.lee", "field_crew", RESTORED_AT
    )
    with pytest.raises(RestorationRefusal) as exc:
        fed.restoration.close_incident(
            FLOOD, "op.dana", "operator", RESTORED_AT, "Done.",
            recorded_at=RESTORED_AT,
        )
    assert any("no restoration confirmation" in p for p in exc.value.problems)
    assert fed.store.get_incident_closure(FLOOD) is None


def test_close_refused_on_partial_confirmation(fed):
    _ready_flood(fed)
    _signal(fed, FLOOD, "BRK-070")  # one device of eight
    fed.restoration.confirm(FLOOD, RESTORED_AT)
    with pytest.raises(RestorationRefusal) as exc:
        fed.restoration.close_incident(
            FLOOD, "op.dana", "operator", RESTORED_AT, "Done.",
            recorded_at=RESTORED_AT,
        )
    problems = "; ".join(exc.value.problems)
    assert "partial" in problems
    assert "crew_confirmation" in problems
    assert "still out" in problems
    assert fed.store.get_incident_closure(FLOOD) is None


def test_close_refused_with_open_work_order_then_waived(fed):
    order = _ready_flood(fed)
    _full_signals(fed)
    fed.restoration.confirm(FLOOD, RESTORED_AT)
    with pytest.raises(RestorationRefusal) as exc:
        fed.restoration.close_incident(
            FLOOD, "op.dana", "operator", RESTORED_AT, "Done.",
            recorded_at=RESTORED_AT,
        )
    assert any(order.order_id in p for p in exc.value.problems)
    # A waiver without a reason is not a waiver.
    with pytest.raises(RestorationRefusal):
        fed.restoration.close_incident(
            FLOOD, "op.dana", "operator", RESTORED_AT, "Done.",
            waivers={order.order_id: "  "}, recorded_at=RESTORED_AT,
        )
    # A waiver naming some other order is refused too.
    with pytest.raises(RestorationRefusal):
        fed.restoration.close_incident(
            FLOOD, "op.dana", "operator", RESTORED_AT, "Done.",
            waivers={"WO-NOPE": "not needed"}, recorded_at=RESTORED_AT,
        )
    act, _closure, _outcome, _record = fed.restoration.close_incident(
        FLOOD, "op.dana", "operator", RESTORED_AT,
        "Crew stood down; the remaining patrol is folded into the "
        "next morning's rounds.",
        waivers={order.order_id: "Patrol folded into morning rounds."},
        recorded_at=RESTORED_AT,
    )
    assert act.waived_orders == [
        {"order_id": order.order_id, "reason": "Patrol folded into morning rounds."}
    ]
    assert act.completed_orders == []


def test_close_role_and_second_close_refused(fed):
    order = _ready_flood(fed)
    _full_signals(fed)
    fed.restoration.confirm(FLOOD, RESTORED_AT)
    fed.work_orders.complete(order.order_id, "crew.lee", "field_crew", RESTORED_AT)
    with pytest.raises(RestorationRefusal):
        fed.restoration.close_incident(
            FLOOD, "crew.lee", "field_crew", RESTORED_AT, "Done.",
            recorded_at=RESTORED_AT,
        )
    fed.restoration.close_incident(
        FLOOD, "op.dana", "operator", RESTORED_AT, "Done.",
        recorded_at=RESTORED_AT,
    )
    with pytest.raises(RestorationRefusal) as exc:
        fed.restoration.close_incident(
            FLOOD, "op.dana", "operator", RESTORED_AT, "Again.",
            recorded_at=RESTORED_AT,
        )
    assert any("already closed" in p for p in exc.value.problems)


# -- work-order completion ---------------------------------------------------


def test_completion_rules(fed):
    at = T0 + timedelta(minutes=30)
    fed.cause.classify(FLOOD, at)
    draft = fed.work_orders.draft(FLOOD, at)
    with pytest.raises(WorkOrderRefusal):
        fed.work_orders.complete(draft.order_id, "crew.lee", "field_crew", at)
    revised = fed.work_orders.revise(draft.order_id, "op.rivera", at)
    fed.work_orders.assign(
        revised.order_id, "CREW-NORTH-VEG", "op.dana",
        T0 + timedelta(minutes=90), T0 + timedelta(minutes=270), at,
    )
    fed.work_orders.decide(revised.order_id, "approved", "op.dana", "operator", at)
    with pytest.raises(WorkOrderRefusal):
        fed.work_orders.complete(draft.order_id, "crew.lee", "field_crew", at)
    with pytest.raises(WorkOrderRefusal):
        fed.work_orders.complete(revised.order_id, "crew.lee", "auditor", at)
    completion = fed.work_orders.complete(
        revised.order_id, "crew.lee", "field_crew", at, note="Done."
    )
    assert fed.work_orders.completion_for(revised.order_id) == completion
    # The decision-derived state is untouched by the completion.
    assert fed.work_orders.state_of(revised) == "approved"
    view = fed.work_orders.view(revised)
    assert view["completion"]["completion_id"] == completion.completion_id
    with pytest.raises(WorkOrderRefusal):
        fed.work_orders.complete(revised.order_id, "crew.lee", "field_crew", at)


# -- splits ---------------------------------------------------------------


def _section_incident(fed):
    base = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    fed.intake.ingest(
        [
            Alarm(alarm_id="ALM-SPL-001", stream_id="STREAM-SCADA-NORTH",
                  device_id="BRK-070", feeder_id="FDR-07",
                  kind="breaker_trip", severity="critical", raised_at=base,
                  message="Feeder 7 breaker tripped on protection"),
            Alarm(alarm_id="ALM-SPL-002", stream_id="STREAM-SCADA-NORTH",
                  device_id="SEC-072", feeder_id="FDR-07",
                  kind="phase_loss", severity="major",
                  raised_at=base + timedelta(seconds=40),
                  message="Phase loss reported by SEC-072"),
            Alarm(alarm_id="ALM-SPL-003", stream_id="STREAM-SCADA-NORTH",
                  device_id="SEC-073", feeder_id="FDR-07",
                  kind="voltage_sag", severity="major",
                  raised_at=base + timedelta(seconds=70),
                  message="Voltage sag reported by SEC-073"),
            Alarm(alarm_id="ALM-SPL-004", stream_id="STREAM-SCADA-NORTH",
                  device_id="XF-0731", feeder_id="FDR-07",
                  kind="transformer_temp_high", severity="major",
                  raised_at=base + timedelta(seconds=100),
                  message="Transformer T31 temperature high"),
        ]
    )
    created = fed.correlator.correlate_new(fed.topology)
    incident = next(i for i in created if i.anchor_alarm_id == "ALM-SPL-001")
    return incident, base


def _partial_section(fed):
    incident, base = _section_incident(fed)
    fed.updates.post_update(
        incident.incident_id, "crew_note", "crew.lee", "field_crew",
        base + timedelta(minutes=25),
        note="North section back on; the SEC-073 branch is still out.",
    )
    for device_id in ("BRK-070", "REC-071", "SEC-072", "SEN-0722", "XF-0721"):
        fed.restoration.record_signal(
            incident.incident_id, "meter", "disp.ray", "dispatcher",
            base + timedelta(minutes=26), "restored", device_id=device_id,
        )
    confirmation = fed.restoration.confirm(
        incident.incident_id, base + timedelta(minutes=27)
    )
    return incident, base, confirmation


def test_split_partitions_the_partial_incident(fed):
    incident, base, confirmation = _partial_section(fed)
    assert confirmation.status == "partial"
    assert confirmation.restored_customers == 120 + 640
    assert confirmation.outstanding_customers == 95 + 585
    before = fed.store.get_incident(incident.incident_id).model_dump(mode="json")
    split = fed.restoration.split_incident(
        incident.incident_id, "op.dana", "operator", base + timedelta(minutes=30)
    )
    assert split.restored_incident_id == f"{incident.incident_id}-RESTORED"
    assert split.continuing_incident_id == f"{incident.incident_id}-CONT"
    assert split.restored_customers == 760
    assert split.continuing_customers == 680
    assert split.confirmation_id == confirmation.confirmation_id
    restored_members = {
        m.alarm_id
        for m in fed.store.list_memberships(split.restored_incident_id)
    }
    continuing_members = {
        m.alarm_id
        for m in fed.store.list_memberships(split.continuing_incident_id)
    }
    assert restored_members == {"ALM-SPL-001", "ALM-SPL-002"}
    assert continuing_members == {"ALM-SPL-003", "ALM-SPL-004"}
    # The source incident stands untouched.
    assert fed.store.get_incident(incident.incident_id).model_dump(mode="json") == before
    assert len(fed.store.list_memberships(incident.incident_id)) == 4
    assert split.continuing_estimate_id is not None
    assert fed.chain.verify_chain(split.restored_incident_id)
    assert fed.chain.verify_chain(split.continuing_incident_id)


def test_split_refusals(fed):
    incident, base = _section_incident(fed)
    with pytest.raises(RestorationRefusal):
        fed.restoration.split_incident(
            incident.incident_id, "op.dana", "operator", base
        )  # no confirmation computed yet
    fed.updates.post_update(
        incident.incident_id, "crew_note", "crew.lee", "field_crew",
        base + timedelta(minutes=25), note="North section back on.",
    )
    for device_id in ("BRK-070", "REC-071", "SEC-072", "SEN-0722", "XF-0721"):
        fed.restoration.record_signal(
            incident.incident_id, "meter", "disp.ray", "dispatcher",
            base + timedelta(minutes=26), "restored", device_id=device_id,
        )
    fed.restoration.confirm(incident.incident_id, base + timedelta(minutes=27))
    split = fed.restoration.split_incident(
        incident.incident_id, "op.dana", "operator", base + timedelta(minutes=30)
    )
    with pytest.raises(RestorationRefusal):
        fed.restoration.split_incident(
            incident.incident_id, "op.dana", "operator",
            base + timedelta(minutes=31),
        )  # already split — the continuing incident is the live one
    with pytest.raises(RestorationRefusal):
        fed.restoration.split_incident(
            split.restored_incident_id, "op.dana", "operator",
            base + timedelta(minutes=31),
        )  # the restored part has nothing to split off


def test_restored_part_closes_while_the_rest_continues(fed):
    incident, base, _confirmation = _partial_section(fed)
    split = fed.restoration.split_incident(
        incident.incident_id, "op.dana", "operator", base + timedelta(minutes=30)
    )
    # The source can never be closed whole on its partial evidence.
    with pytest.raises(RestorationRefusal):
        fed.restoration.close_incident(
            incident.incident_id, "op.dana", "operator",
            base + timedelta(minutes=40), "All done.",
            recorded_at=base + timedelta(minutes=40),
        )
    fed.restoration.record_signal(
        split.restored_incident_id, "topology", "disp.ray", "dispatcher",
        base + timedelta(minutes=35), "supplied", feeder_id="FDR-07",
    )
    part_confirmation = fed.restoration.confirm(
        split.restored_incident_id, base + timedelta(minutes=36)
    )
    # The partition is the accounting universe: 760 customers, all
    # restored, with the meter and crew evidence inherited from
    # the source through the split link.
    assert part_confirmation.status == "complete"
    assert part_confirmation.total_customers == 760
    act, _closure, outcome, _record = fed.restoration.close_incident(
        split.restored_incident_id, "op.dana", "operator",
        base + timedelta(minutes=40),
        "North section restored and inspected.",
        recorded_at=base + timedelta(minutes=41),
    )
    assert act.confirmation_id == part_confirmation.confirmation_id
    assert outcome.status == "no_computed_estimate"
    assert fed.store.get_incident_closure(split.continuing_incident_id) is None
    assert fed.store.get_incident_closure(incident.incident_id) is None


# -- post-close contradictions -----------------------------------------------


def _post_close_alarm(alarm_id="ALM-POST-001", device_id="REC-071",
                      kind="recloser_lockout"):
    return Alarm(
        alarm_id=alarm_id, stream_id="STREAM-SCADA-NORTH",
        device_id=device_id, feeder_id="FDR-07", kind=kind,
        severity="critical", raised_at=RESTORED_AT + timedelta(hours=2),
        message="Recloser R1 locked out again after restoration",
    )


def test_post_close_contradiction_opens_a_fresh_incident(fed):
    order = _ready_flood(fed)
    _close_flood(fed, order)
    before_members = {
        m.alarm_id for m in fed.store.list_memberships(FLOOD)
    }
    before_row = fed.store.get_incident(FLOOD).model_dump(mode="json")
    update = fed.updates.post_update(
        FLOOD, "alarm_observation", "disp.ray", "dispatcher",
        RESTORED_AT + timedelta(hours=2), alarm=_post_close_alarm(),
    )
    assert update.correlation["status"] == "post_close_contradiction"
    fresh_id = update.correlation["fresh_incident_id"]
    assert fresh_id == "INC-ALM-POST-001"
    # The closed incident is byte-identical and gains no member.
    assert fed.store.get_incident(FLOOD).model_dump(mode="json") == before_row
    assert {m.alarm_id for m in fed.store.list_memberships(FLOOD)} == before_members
    # The fresh incident holds the alarm and links back.
    fresh_members = {m.alarm_id for m in fed.store.list_memberships(fresh_id)}
    assert fresh_members == {"ALM-POST-001"}
    on_closed = fed.restoration.contradictions_for(FLOOD)
    on_fresh = fed.restoration.contradictions_for(fresh_id)
    assert len(on_closed) == 1 and on_fresh == on_closed
    assert on_closed[0].alarm_id == "ALM-POST-001"
    assert fed.chain.verify_chain(FLOOD)
    assert fed.chain.verify_chain(fresh_id)


def test_post_close_update_off_the_device_set_is_evidence_only(fed):
    order = _ready_flood(fed)
    _close_flood(fed, order)
    update = fed.updates.post_update(
        FLOOD, "alarm_observation", "disp.ray", "dispatcher",
        RESTORED_AT + timedelta(hours=2),
        alarm=Alarm(
            alarm_id="ALM-POST-002", stream_id="STREAM-COMMS-SOUTH",
            device_id="RTU-120", feeder_id="FDR-12",
            kind="comms_poll_failure", severity="info",
            raised_at=RESTORED_AT + timedelta(hours=2),
            message="Poll failure on RTU-120",
        ),
    )
    assert update.correlation["status"] == "no_join"
    assert "closed" in update.correlation["reason"]
    assert fed.restoration.contradictions_for(FLOOD) == []
    assert fed.store.membership_for_alarm("ALM-POST-002") is None


# -- the event log ------------------------------------------------------------


def test_event_log_reperforms_and_names_artefacts(fed):
    order = _ready_flood(fed)
    (act, _closure, _outcome, _record), _conf = _close_flood(fed, order)
    fed.updates.post_update(
        FLOOD, "alarm_observation", "disp.ray", "dispatcher",
        RESTORED_AT + timedelta(hours=2), alarm=_post_close_alarm(),
    )
    log = fed.event_log.build_log(FLOOD)
    kinds = [line["kind"] for line in log["lines"]]
    for expected in (
        "incident", "alarm", "severity_assessment",
        "cause_classification", "etr_estimate", "work_order",
        "work_order_assignment", "work_order_decision",
        "work_order_completion", "field_update", "restoration_signal",
        "restoration_confirmation", "closure", "etr_outcome",
        "incident_close_act", "post_close_contradiction",
    ):
        assert expected in kinds, expected
    ats = [line["at"] for line in log["lines"]]
    assert ats == sorted(ats)
    assert all(line["artefact_id"] for line in log["lines"])
    assert log["chain_valid"] is True
    assert fed.event_log.verify_log(FLOOD, log) == []

    tampered = [dict(line) for line in log["lines"]]
    tampered[3]["at"] = "1999-01-01T00:00:00+00:00"
    problems = fed.event_log.verify_log(FLOOD, {"lines": tampered})
    assert any("does not re-perform" in p for p in problems)

    dropped = [dict(line) for line in log["lines"]][:-1]
    problems = fed.event_log.verify_log(FLOOD, {"lines": dropped})
    assert any("missing line" in p for p in problems)

    text = fed.event_log.render_text(log)
    assert FLOOD in text and act.act_id in text
    with pytest.raises(KeyError):
        fed.event_log.build_log("INC-NOPE")
