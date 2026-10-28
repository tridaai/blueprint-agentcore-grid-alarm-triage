"""Round 5: work-order drafts, crew assignment acts and the
operator approval gate (Law 5), graded against the planted
fixtures. The flood draft is hand-derived: vegetation contact on
Feeder 7, eight affected devices, the T+30min ETR of record
(restoration 06:23, basis computed_from_rates)."""

from datetime import datetime, timedelta, timezone

import pytest

from grid_triage.models import Alarm
from grid_triage.seed import T0
from grid_triage.work_orders import WORK_PACK, WorkOrderRefusal

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
AT = T0 + timedelta(minutes=30)
WIN_OK = (T0 + timedelta(minutes=90), T0 + timedelta(minutes=270))
WIN_ROSTER_CLASH = (T0 + timedelta(minutes=30), T0 + timedelta(minutes=250))


def draft_flood(services, at=AT):
    return services.work_orders.draft(FLOOD, at)


def assign_ok(services, order_id, crew="CREW-NORTH-VEG", by="op.dana"):
    return services.work_orders.assign(
        order_id, crew, by, WIN_OK[0], WIN_OK[1], AT
    )


def test_flood_draft_carries_every_pack_section(fed):
    order = draft_flood(fed)
    assert order.version == 1 and order.supersedes is None
    assert order.drafted_by == "system"
    assert order.work_pack_version == WORK_PACK["version"]
    assert order.cause_class == "vegetation_contact"
    classification = fed.cause.current(FLOOD)
    assert order.classification_id == classification.classification_id

    estimate = fed.etr.estimate(FLOOD, AT)
    assert order.estimate_id == estimate.estimate_id
    assert estimate.estimated_restored_at == T0 + timedelta(minutes=250)

    assert [
        (d["device_id"], d["kind"]) for d in order.affected_devices
    ] == [
        ("BRK-070", "feeder_breaker"),
        ("REC-071", "recloser"),
        ("SEC-072", "sectionalizer"),
        ("SEC-073", "sectionalizer"),
        ("SEN-0722", "line_sensor"),
        ("SEN-0732", "line_sensor"),
        ("XF-0721", "transformer"),
        ("XF-0731", "transformer"),
    ]
    assert [s["device_kind"] for s in order.safety_notes] == [
        "feeder_breaker",
        "recloser",
        "sectionalizer",
        "line_sensor",
        "transformer",
    ]
    for entry in order.safety_notes:
        assert entry["notes"] == WORK_PACK["safety_notes"][entry["device_kind"]]
    assert order.required_qualification == "vegetation_clearing"
    assert (
        order.materials
        == WORK_PACK["classes"]["vegetation_contact"]["materials"]
    )
    assert "BRK-070" in order.scope_of_work[0]
    assert "FDR-07" in order.scope_of_work[0]
    assert order.gaps == []
    assert fed.work_orders.state_of(order) == "draft"


def test_redraft_is_a_noop_and_a_changed_draft_refuses(fed):
    order = draft_flood(fed)
    assert draft_flood(fed).order_id == order.order_id
    fed.work_orders.revise(order.order_id, "op.rivera", AT, operator_note="n")
    with pytest.raises(WorkOrderRefusal) as exc:
        draft_flood(fed)
    assert "revise" in exc.value.problems[0]


def test_lone_draft_names_its_gaps(fed):
    at = T0 + timedelta(hours=8)
    order = fed.work_orders.draft(LONE, at)
    assert order.cause_class == "unknown"
    assert order.required_qualification is None
    assert order.gaps == ["required_qualification"]
    estimate = fed.etr.current(LONE)
    assert order.estimate_id == estimate.estimate_id
    assert estimate.status == "not_computable"
    assert [d["device_id"] for d in order.affected_devices] == ["BRK-030"]
    assert [s["device_kind"] for s in order.safety_notes] == ["feeder_breaker"]
    assert order.materials == WORK_PACK["classes"]["unknown"]["materials"]


def test_assignment_roster_commitment_refused(fed):
    order = draft_flood(fed)
    with pytest.raises(WorkOrderRefusal) as exc:
        fed.work_orders.assign(
            order.order_id,
            "CREW-NORTH-VEG",
            "op.dana",
            WIN_ROSTER_CLASH[0],
            WIN_ROSTER_CLASH[1],
            AT,
        )
    assert any("INC-EXT-2201" in p for p in exc.value.problems)


def test_assignment_unqualified_and_unknown_crews_refused(fed):
    order = draft_flood(fed)
    with pytest.raises(WorkOrderRefusal) as exc:
        assign_ok(fed, order.order_id, crew="CREW-SOUTH-EQUIP")
    assert any("vegetation_clearing" in p for p in exc.value.problems)
    with pytest.raises(WorkOrderRefusal) as exc:
        assign_ok(fed, order.order_id, crew="CREW-NOPE")
    assert any("no such crew" in p for p in exc.value.problems)


def test_assignment_bad_window_and_unqualified_draft_refused(fed):
    order = draft_flood(fed)
    with pytest.raises(WorkOrderRefusal):
        fed.work_orders.assign(
            order.order_id, "CREW-NORTH-VEG", "op.dana", WIN_OK[1], WIN_OK[0], AT
        )
    lone = fed.work_orders.draft(LONE, T0 + timedelta(hours=8))
    with pytest.raises(WorkOrderRefusal) as exc:
        fed.work_orders.assign(
            lone.order_id,
            "CREW-ALDER-LINE",
            "op.dana",
            T0 + timedelta(hours=9),
            T0 + timedelta(hours=11),
            AT,
        )
    assert any("required_qualification" in p for p in exc.value.problems)


def test_assignment_and_decision_leave_the_draft_byte_identical(fed):
    order = draft_flood(fed)
    before = fed.store.get_work_order(order.order_id).model_dump_json()
    assignment = assign_ok(fed, order.order_id)
    fed.work_orders.decide(order.order_id, "approved", "op.dana", "operator", AT)
    after = fed.store.get_work_order(order.order_id).model_dump_json()
    assert before == after
    # the acts are their own records, linked to the draft they name
    assert fed.store.list_work_order_assignments(order.order_id) == [assignment]
    assert fed.chain.verify_chain(FLOOD)


def test_reassignment_supersedes_by_link(fed):
    order = draft_flood(fed)
    first = assign_ok(fed, order.order_id)
    second = fed.work_orders.assign(
        order.order_id,
        "CREW-NORTH-VEG",
        "op.dana",
        T0 + timedelta(minutes=280),
        T0 + timedelta(minutes=400),
        AT,
    )
    assert second.supersedes == first.assignment_id
    assert fed.work_orders.current_assignment(order.order_id) == second


def _second_vegetation_incident(services):
    base = datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)
    services.intake.ingest(
        [
            Alarm(
                alarm_id="ALM-VEG2-001", stream_id="STREAM-SCADA-NORTH",
                device_id="BRK-070", feeder_id="FDR-07", kind="breaker_trip",
                severity="critical", raised_at=base,
                message="Feeder 7 breaker tripped again",
            ),
            Alarm(
                alarm_id="ALM-VEG2-002", stream_id="STREAM-SCADA-NORTH",
                device_id="REC-071", feeder_id="FDR-07",
                kind="recloser_lockout", severity="critical",
                raised_at=base + timedelta(seconds=30),
                message="Recloser R1 locked out",
            ),
            Alarm(
                alarm_id="ALM-VEG2-003", stream_id="STREAM-SCADA-NORTH",
                device_id="SEC-072", feeder_id="FDR-07", kind="phase_loss",
                severity="major", raised_at=base + timedelta(seconds=60),
                message="Phase loss reported by SEC-072",
            ),
        ]
    )
    created = services.correlator.correlate_new(services.topology)
    assert len(created) == 1
    return created[0].incident_id


def test_cross_order_double_booking_refused_and_named(fed):
    flood_order = draft_flood(fed)
    flood_assignment = assign_ok(fed, flood_order.order_id)
    second_id = _second_vegetation_incident(fed)
    second = fed.work_orders.draft(second_id, T0 + timedelta(hours=18))
    assert second.cause_class == "vegetation_contact"
    with pytest.raises(WorkOrderRefusal) as exc:
        fed.work_orders.assign(
            second.order_id,
            "CREW-NORTH-VEG",
            "op.dana",
            T0 + timedelta(minutes=240),  # 06:13 — inside the flood window
            T0 + timedelta(minutes=540),
            AT,
        )
    assert any(
        flood_assignment.assignment_id in p for p in exc.value.problems
    )
    later = fed.work_orders.assign(
        second.order_id,
        "CREW-NORTH-VEG",
        "op.dana",
        T0 + timedelta(minutes=420),  # 09:13 — after the flood window
        T0 + timedelta(minutes=600),
        AT,
    )
    assert later.crew_id == "CREW-NORTH-VEG"


def test_approval_requires_assignment(fed):
    order = draft_flood(fed)
    with pytest.raises(WorkOrderRefusal) as exc:
        fed.work_orders.decide(
            order.order_id, "approved", "op.dana", "operator", AT
        )
    assert any("crew assignment" in p for p in exc.value.problems)


def test_self_approval_refused_other_operator_approves(fed):
    order = draft_flood(fed)
    revised = fed.work_orders.revise(
        order.order_id, "op.rivera", AT, operator_note="Chipper at the gate."
    )
    assign_ok(fed, revised.order_id)
    with pytest.raises(WorkOrderRefusal) as exc:
        fed.work_orders.decide(
            revised.order_id, "approved", "op.rivera", "operator", AT
        )
    assert any("different operator" in p for p in exc.value.problems)

    act, notifications = fed.work_orders.decide(
        revised.order_id, "approved", "op.dana", "operator", AT
    )
    assert act.order_id == revised.order_id
    assert fed.work_orders.state_of(revised) == "approved"
    assert {n.channel for n in notifications} == {
        "field_crew",
        "customer_outage",
    }
    stored = fed.store.list_notifications(FLOOD)
    assert sorted(n.notification_id for n in stored) == sorted(
        act.notification_ids
    )
    for notification in stored:
        assert notification.body.startswith("DRAFT notification — not sent")
    customer = next(n for n in stored if n.channel == "customer_outage")
    assert "computed_from_rates" in customer.body
    assert "2026-10-14T06:23:00+00:00" in customer.body
    crew_note = next(n for n in stored if n.channel == "field_crew")
    assert "CREW-NORTH-VEG" in crew_note.body
    assert revised.order_id in crew_note.body


def test_system_draft_may_be_approved_by_any_named_operator(fed):
    order = draft_flood(fed)
    assign_ok(fed, order.order_id)
    act, _ = fed.work_orders.decide(
        order.order_id, "approved", "op.rivera", "duty_manager", AT
    )
    assert act.decision == "approved"


def test_human_drafter_may_not_self_approve_the_first_version(fed):
    order = fed.work_orders.draft(FLOOD, AT, drafted_by="op.rivera")
    assign_ok(fed, order.order_id)
    with pytest.raises(WorkOrderRefusal):
        fed.work_orders.decide(
            order.order_id, "approved", "op.rivera", "operator", AT
        )


def test_approval_role_is_checked(fed):
    order = draft_flood(fed)
    assign_ok(fed, order.order_id)
    with pytest.raises(WorkOrderRefusal) as exc:
        fed.work_orders.decide(
            order.order_id, "approved", "op.dana", "field_crew", AT
        )
    assert any("may not decide" in p for p in exc.value.problems)


def test_a_decided_version_cannot_be_decided_again(fed):
    order = draft_flood(fed)
    assign_ok(fed, order.order_id)
    fed.work_orders.decide(order.order_id, "approved", "op.dana", "operator", AT)
    with pytest.raises(WorkOrderRefusal) as exc:
        fed.work_orders.decide(
            order.order_id, "rejected", "op.dana", "operator", AT,
            reason="too late",
        )
    assert any("already approved" in p for p in exc.value.problems)


def test_decision_on_a_superseded_version_refused(fed):
    order = draft_flood(fed)
    fed.work_orders.revise(order.order_id, "op.rivera", AT, operator_note="n")
    with pytest.raises(WorkOrderRefusal) as exc:
        fed.work_orders.decide(
            order.order_id, "approved", "op.dana", "operator", AT
        )
    assert any("not the current version" in p for p in exc.value.problems)


def test_rejection_needs_a_reason_and_blocks_approval(fed):
    order = draft_flood(fed)
    with pytest.raises(WorkOrderRefusal) as exc:
        fed.work_orders.decide(
            order.order_id, "rejected", "op.dana", "operator", AT
        )
    assert any("reason" in p for p in exc.value.problems)
    act, notifications = fed.work_orders.decide(
        order.order_id, "rejected", "op.dana", "duty_manager", AT,
        reason="Scope misses the downstream sensor checks.",
    )
    assert notifications == []
    assert fed.work_orders.state_of(order) == "rejected"
    with pytest.raises(WorkOrderRefusal):
        fed.work_orders.decide(
            order.order_id, "approved", "op.dana", "operator", AT
        )
    # the way forward is a new version, which can be approved
    revised = fed.work_orders.revise(
        order.order_id, "op.rivera", AT, operator_note="Sensor checks added."
    )
    assert fed.work_orders.state_of(revised) == "draft"
    assign_ok(fed, revised.order_id)
    fed.work_orders.decide(
        revised.order_id, "approved", "op.dana", "operator", AT
    )
    assert fed.work_orders.state_of(revised) == "approved"


def test_post_approval_revision_returns_to_draft_and_needs_fresh_acts(fed):
    order = draft_flood(fed)
    assign_ok(fed, order.order_id)
    fed.work_orders.decide(order.order_id, "approved", "op.dana", "operator", AT)
    revised = fed.work_orders.revise(
        order.order_id, "op.dana", AT, operator_note="Second tree found."
    )
    assert revised.version == 2 and revised.supersedes == order.order_id
    # the approval stays pinned to version 1 — it does not follow the link
    assert fed.work_orders.state_of(order) == "approved"
    assert fed.work_orders.state_of(revised) == "draft"
    with pytest.raises(WorkOrderRefusal):
        fed.work_orders.decide(
            revised.order_id, "approved", "op.rivera", "operator", AT
        )
    assign_ok(fed, revised.order_id)
    fed.work_orders.decide(
        revised.order_id, "approved", "op.rivera", "operator", AT
    )
    assert fed.work_orders.state_of(revised) == "approved"


def test_list_by_state(fed):
    order = draft_flood(fed)
    lone = fed.work_orders.draft(LONE, T0 + timedelta(hours=8))
    assign_ok(fed, order.order_id)
    fed.work_orders.decide(order.order_id, "approved", "op.dana", "operator", AT)
    fed.work_orders.decide(
        lone.order_id, "rejected", "op.dana", "operator", AT,
        reason="Unclassified.",
    )
    assert [o.order_id for o in fed.work_orders.list_by_state("approved")] == [
        order.order_id
    ]
    assert [o.order_id for o in fed.work_orders.list_by_state("rejected")] == [
        lone.order_id
    ]
    assert fed.work_orders.list_by_state("draft") == []
