"""Round 10 — the red team. Every test is numbered to match
docs/red-team.md: test_rt01…test_rt06 are the defects found and
fixed in this round (they fail on the R9 code), test_rt07… are
the attacks that held, re-graded so they stay held.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from grid_triage.closure import ClosureRefusal
from grid_triage.correlation import correlate
from grid_triage.etr import EtrRefusal
from grid_triage.handover import HandoverRefusal
from grid_triage.models import Alarm
from grid_triage.restoration import RestorationRefusal
from grid_triage.revisions import RevisionRefusal, TopologyRefusal
from grid_triage.seed import T0
from grid_triage.severity import SeverityRefusal
from grid_triage.storm import StormRefusal
from grid_triage.topology import TopologyView, load_topology_file

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
LONE_T0 = datetime(2026, 10, 14, 9, 41, 0, tzinfo=timezone.utc)
SRC = Path(__file__).resolve().parents[1] / "src" / "grid_triage"


def _alarm(aid, device, feeder, kind, at, stream="STREAM-SCADA-NORTH"):
    return Alarm(
        alarm_id=aid, stream_id=stream, device_id=device, feeder_id=feeder,
        kind=kind, severity="major", raised_at=at,
        message=f"{kind} on {device}",
    )


# -- the defects ------------------------------------------------------


def test_rt01_severity_recomputation_cannot_rewind_the_head(fed):
    a30 = fed.severity.assess(FLOOD, T0 + timedelta(minutes=30))
    a300 = fed.severity.assess(FLOOD, T0 + timedelta(minutes=300))
    assert fed.severity.current(FLOOD).assessment_id == a300.assessment_id
    assert a300.score > a30.score  # duration accrued in between
    # The attack: re-run the clock backwards and the stale band
    # becomes the current one on every head-reading surface.
    with pytest.raises(SeverityRefusal):
        fed.severity.assess(FLOOD, T0 + timedelta(minutes=5))
    assert fed.severity.current(FLOOD).assessment_id == a300.assessment_id
    # Re-running an old instant is refused too: its supersedes
    # link would point backwards at the head it claims to
    # supersede, which is the same rewind wearing a link.
    with pytest.raises(SeverityRefusal):
        fed.severity.assess(FLOOD, T0 + timedelta(minutes=30))
    assert fed.severity.current(FLOOD).assessment_id == a300.assessment_id
    # The head's own identical recomputation is still a no-op.
    assert fed.severity.assess(FLOOD, T0 + timedelta(minutes=300)) == a300
    # A recomputation at the head's own instant over changed
    # inputs is a supersession, not a rewind, and still lands.
    fed.updates.post_update(
        FLOOD, "alarm_observation", "crew.lee", "field_crew",
        T0 + timedelta(minutes=16),
        alarm=_alarm("ALM-RT01", "SEC-072", "FDR-07", "voltage_sag",
                     T0 + timedelta(minutes=15)),
    )
    assert fed.store.membership_for_alarm("ALM-RT01") is not None
    moved = fed.severity.assess(FLOOD, T0 + timedelta(minutes=300))
    assert moved.assessment_id != a300.assessment_id
    assert moved.inputs["member_alarm_count"] == 41
    assert fed.severity.current(FLOOD).assessment_id == moved.assessment_id


def test_rt02_backdated_estimate_cannot_poison_the_closure(fed):
    estimate = fed.etr.estimate(FLOOD, T0 + timedelta(minutes=30))
    with pytest.raises(EtrRefusal):
        fed.etr.estimate(FLOOD, T0 + timedelta(minutes=10))
    assert fed.etr.current(FLOOD).estimate_id == estimate.estimate_id
    _closure, outcome, _record = fed.closure.close_incident(
        FLOOD, "op.dana", "operator", T0 + timedelta(minutes=270),
        "Restored.", T0 + timedelta(minutes=300),
    )
    # The outcome grades against the estimate of record — the
    # one standing when the incident was run — never a backdate.
    assert outcome.estimate_id == estimate.estimate_id
    assert outcome.signed_error_minutes == 20.0


def test_rt03_closure_role_comes_from_a_pack(fed):
    for role in ("banana", "field_crew", "system", ""):
        with pytest.raises(ClosureRefusal):
            fed.closure.close_incident(
                LONE, "mallory", role, LONE_T0 + timedelta(minutes=60),
                "Closed.", LONE_T0 + timedelta(minutes=75),
            )
    assert fed.store.get_incident_closure(LONE) is None
    # The pack's second role stands too (the first is exercised
    # all over Rounds 4–9).
    closure, _outcome, _record = fed.closure.close_incident(
        LONE, "op.dana", "duty_manager", LONE_T0 + timedelta(minutes=60),
        "Closed.", LONE_T0 + timedelta(minutes=75),
    )
    assert closure.role == "duty_manager"


def test_rt04_a_restoration_cannot_be_dated_after_its_recording(fed):
    with pytest.raises(ClosureRefusal):
        fed.closure.close_incident(
            LONE, "op.dana", "operator", LONE_T0 + timedelta(days=7),
            "Restored next week.", LONE_T0 + timedelta(minutes=75),
        )
    assert fed.store.list_etr_outcomes(LONE) == []
    # The boundary itself stands: restored at the recording
    # instant is a fact recorded as it happens.
    closure, outcome, _record = fed.closure.close_incident(
        LONE, "op.dana", "operator", LONE_T0 + timedelta(minutes=75),
        "Restored.", LONE_T0 + timedelta(minutes=75),
    )
    assert outcome.actual_duration_minutes == 75.0


def test_rt05_an_extension_cannot_widen_onto_another_window(fed):
    w1 = fed.storm.declare("op.dana", "operator", "Cell one", T0,
                           feeders=["FDR-07"])
    w2 = fed.storm.declare("op.rivera", "operator", "Cell two",
                           T0 + timedelta(minutes=10), feeders=["FDR-12"])
    with pytest.raises(StormRefusal) as exc:
        fed.storm.extend(
            w1.declaration_id, "op.dana", "operator",
            "Widening onto Feeder 12", T0 + timedelta(minutes=20),
            feeders=["FDR-07", "FDR-12"],
        )
    assert w2.declaration_id in str(exc.value)
    # Feeder 12's state still names the second window alone.
    state = fed.storm.state_at("FDR-12", T0 + timedelta(minutes=30))
    assert state["declaration_id"] == w2.declaration_id
    # Widening onto an uncovered feeder is still the act's job.
    fed.storm.extend(
        w1.declaration_id, "op.dana", "operator",
        "Widening onto Feeder 3", T0 + timedelta(minutes=20),
        feeders=["FDR-07", "FDR-03"],
    )
    assert fed.storm.state_at("FDR-03", T0 + timedelta(minutes=30))["mode"] == "storm"


def test_rt06_a_backdated_declaration_cannot_overlap_a_window(fed):
    w1 = fed.storm.declare("op.dana", "operator", "Cell one", T0,
                           feeders=["FDR-07"])
    with pytest.raises(StormRefusal):
        fed.storm.declare("op.rivera", "operator", "Backdated cell",
                          T0 - timedelta(hours=1), feeders=["FDR-07"])
    fed.storm.stand_down(w1.declaration_id, "op.dana", "operator",
                         "Cell passed", T0 + timedelta(hours=2))
    # Nor can a declaration be backdated into the closed
    # window's recorded span.
    with pytest.raises(StormRefusal):
        fed.storm.declare("op.rivera", "operator", "Into the closed span",
                          T0 + timedelta(hours=1), feeders=["FDR-07"])
    # After the span, the feeder is free to declare over.
    fed.storm.declare("op.rivera", "operator", "Evening cell",
                      T0 + timedelta(hours=3), feeders=["FDR-07"])


# -- the attacks that held ---------------------------------------------


def test_rt07_law_1_surface_scan(fed):
    verbs = [
        "switch", "reclose", "shed", "energize", "deenergize",
        "open_breaker", "close_breaker", "trip_breaker", "setpoint",
        "execute", "send", "transmit",
    ]

    def hit(name: str) -> bool:
        return any(v in name.lower() for v in verbs)

    from grid_triage.api import app

    routes = [
        f"{sorted(r.methods)[0]} {r.path}" for r in app.routes if hasattr(r, "methods") and r.methods
    ]
    assert routes and not [r for r in routes if hit(r)], routes
    runtime_src = (SRC / "runtime_app.py").read_text()
    actions = set(re.findall(r'action == "([a-z_]+)"', runtime_src))
    actions |= set(re.findall(r'action in \(([^)]*)\)', runtime_src))
    assert actions and not [a for a in actions if hit(a)], actions
    cli_src = (SRC / "cli.py").read_text()
    commands = set(re.findall(r'add_parser\("([a-z-]+)"', cli_src))
    assert commands and not [c for c in commands if hit(c)], commands
    # No module ships a send mechanism at all.
    for module in SRC.glob("*.py"):
        text = module.read_text()
        for token in ("smtplib", "sendmail", "SMTP", ".send("):
            assert token not in text, f"{module.name} carries {token}"
    # And the notifications an approval produces are drafts in
    # their own text — the notify shape records, never sends.
    order = fed.work_orders.draft(FLOOD, T0 + timedelta(minutes=35))
    fed.work_orders.assign(
        order.order_id, "CREW-NORTH-VEG", "op.dana",
        T0 + timedelta(minutes=90), T0 + timedelta(minutes=270),
        T0 + timedelta(minutes=35),
    )
    fed.work_orders.decide(order.order_id, "approved", "op.dana",
                           "operator", T0 + timedelta(minutes=36))
    notifications = fed.store.list_notifications(FLOOD)
    assert notifications
    assert all(n.body.startswith("DRAFT notification") for n in notifications)


def test_rt08_the_correlation_window_edge_is_the_pack_edge():
    view = TopologyView(load_topology_file("v1"))
    for delta, expected in ((600, True), (601, False)):
        pair = [
            _alarm("X1", "BRK-070", "FDR-07", "breaker_trip", T0),
            _alarm("X2", "SEC-072", "FDR-07", "voltage_sag",
                   T0 + timedelta(seconds=delta)),
        ]
        result = correlate(pair, view, incident_id_for=lambda a: f"INC-{a.alarm_id}")
        joined = any(m.alarm_id == "X2" for m in result.memberships)
        assert joined is expected


def test_rt09_storm_joinable_kinds_are_exactly_the_packs(fed):
    fed.storm.declare("op.dana", "operator", "Cell over Feeder 12", T0,
                      feeders=["FDR-12"])
    fed.intake.ingest([
        _alarm("S-ANC", "REG-1202", "FDR-12", "breaker_trip",
               T0 + timedelta(minutes=30), "STREAM-SCADA-SOUTH"),
        _alarm("S-PING", "SEN-1201", "FDR-12", "meter_ping_failure",
               T0 + timedelta(minutes=32), "STREAM-COMMS-SOUTH"),
        _alarm("S-COMMS", "RTU-120", "FDR-12", "comms_poll_failure",
               T0 + timedelta(minutes=33), "STREAM-COMMS-SOUTH"),
        _alarm("S-TAP", "REG-1202", "FDR-12", "tap_step_counter",
               T0 + timedelta(minutes=34), "STREAM-COMMS-SOUTH"),
    ])
    fed.correlator.correlate_new(fed.topology)
    members = {m.alarm_id for m in fed.store.list_memberships("INC-S-ANC")}
    assert members == {"S-ANC", "S-PING"}
    assert fed.store.get_incident("INC-S-ANC").operating_mode == "storm"


def test_rt10_the_gated_close_and_the_revision_cycle_hold(fed):
    with pytest.raises(RestorationRefusal):
        fed.restoration.close_incident(
            FLOOD, "op.dana", "operator", T0 + timedelta(minutes=270),
            "Done.", recorded_at=T0 + timedelta(minutes=300),
        )
    # Round 17: the declaration is struck through the governed
    # route — the direct act is closed.
    _proposal10 = fed.topology_acts.propose(
        "v2", "op.dana", "operator",
        T0 + timedelta(hours=1), T0 + timedelta(hours=2))
    fed.topology_acts.decide(_proposal10.proposal_id, "dm.reyes",
                             "duty_manager", T0 + timedelta(hours=1),
                             "confirmed")
    revision = fed.revisions.recorrelate(FLOOD, "op.dana", "operator",
                                         T0 + timedelta(hours=3))
    with pytest.raises(RevisionRefusal):
        fed.revisions.recorrelate(FLOOD, "op.dana", "operator",
                                  T0 + timedelta(hours=4))
    with pytest.raises(RevisionRefusal):
        fed.revisions.recorrelate(revision.revised_incident_id, "op.dana",
                                  "operator", T0 + timedelta(hours=4))


def test_rt11_post_close_contradiction_on_a_split_part(fed):
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
        T0 + timedelta(minutes=47), note="North section back on.",
    )
    restored_devices = ["BRK-070", "REC-071", "SEC-072", "SEN-0722", "XF-0721"]
    for device_id in restored_devices:
        fed.restoration.record_signal(
            FLOOD, "meter", "disp.ray", "dispatcher",
            T0 + timedelta(minutes=265), "restored", device_id=device_id,
        )
    fed.restoration.record_signal(
        FLOOD, "topology", "disp.ray", "dispatcher",
        T0 + timedelta(minutes=266), "supplied", feeder_id="FDR-07",
    )
    confirmation = fed.restoration.confirm(FLOOD, T0 + timedelta(minutes=267))
    assert confirmation.status == "partial"
    split = fed.restoration.split_incident(
        FLOOD, "op.dana", "operator", T0 + timedelta(minutes=268)
    )
    part = split.restored_incident_id
    part_confirmation = fed.restoration.confirm(part, T0 + timedelta(minutes=269))
    assert part_confirmation.status == "complete"
    fed.restoration.close_incident(
        part, "op.dana", "operator", T0 + timedelta(minutes=270),
        "North section restored and closed.", recorded_at=T0 + timedelta(minutes=300),
    )
    before_row = fed.store.get_incident(part).model_dump(mode="json")
    before_members = {m.alarm_id for m in fed.store.list_memberships(part)}
    update = fed.updates.post_update(
        part, "alarm_observation", "disp.ray", "dispatcher",
        T0 + timedelta(hours=6),
        alarm=_alarm("ALM-POST-SPLIT-001", "SEC-072", "FDR-07",
                     "breaker_trip", T0 + timedelta(hours=6)),
    )
    assert update.correlation["status"] == "post_close_contradiction"
    assert fed.store.get_incident(part).model_dump(mode="json") == before_row
    assert {m.alarm_id for m in fed.store.list_memberships(part)} == before_members
    assert len(fed.restoration.contradictions_for(part)) == 1


def test_rt12_handover_discipline_and_the_staleness_rule(fed):
    fed.severity.assess(FLOOD, T0 + timedelta(minutes=30))
    handover = fed.handover.assemble("op.dana", "operator", T0 + timedelta(hours=6))
    row = next(
        r for r in handover.sections["open_incidents"]
        if r["incident_id"] == FLOOD
    )
    assert row["band"] == "SEV-1"  # an open SEV-1 is in the record
    with pytest.raises(HandoverRefusal):
        fed.handover.accept(handover.handover_id, "op.dana", "operator",
                            T0 + timedelta(hours=6, minutes=1))
    # The stated rule: staleness is judged over the sections the
    # handover carries. A crew note moves no section, so the
    # acceptance still binds; an artefact that moves a section
    # (below, a fresh severity head) stales the next handover.
    fed.updates.post_update(
        FLOOD, "crew_note", "crew.lee", "field_crew",
        T0 + timedelta(hours=6, minutes=2), note="On site, assessing.",
    )
    assert fed.handover.verify(handover.handover_id) == []
    fed.handover.accept(handover.handover_id, "op.reyes", "operator",
                        T0 + timedelta(hours=6, minutes=3))
    second = fed.handover.assemble("op.reyes", "operator", T0 + timedelta(hours=7))
    # A fresh assessment moves the open-incidents section (new
    # head, new score) — the drift is named and acceptance of
    # the stale artefact is refused.
    fed.severity.assess(FLOOD, T0 + timedelta(hours=7, minutes=30))
    assert fed.handover.verify(second.handover_id) != []
    with pytest.raises(HandoverRefusal):
        fed.handover.accept(second.handover_id, "op.okafor", "operator",
                            T0 + timedelta(hours=7, minutes=2))


def test_rt13_topology_attacks_hold(fed):
    update = fed.updates.post_update(
        FLOOD, "alarm_observation", "crew.lee", "field_crew",
        T0 + timedelta(minutes=20),
        alarm=_alarm("ALM-UPD-V2", "SEN-0723", "FDR-07", "line_sensor_fault",
                     T0 + timedelta(minutes=19)),
    )
    assert update.correlation["status"] == "unresolvable_under_pinned_topology"
    assert fed.store.membership_for_alarm("ALM-UPD-V2") is None
    with pytest.raises(TopologyRefusal):
        fed.topology_acts.declare("v2", "op.dana", "field_crew",
                                  T0 + timedelta(hours=1), T0 + timedelta(hours=2))
    with pytest.raises(TopologyRefusal):
        fed.topology_acts.declare("v2", "  ", "operator",
                                  T0 + timedelta(hours=1), T0 + timedelta(hours=2))
    with pytest.raises(TopologyRefusal):
        fed.topology_acts.declare("v2", "op.dana", "operator",
                                  T0 + timedelta(hours=2), T0 + timedelta(hours=1))
    import copy

    payload = copy.deepcopy(load_topology_file("v1"))
    for device in payload["devices"]:
        if device["device_id"] == "BRK-070":
            device["customers"] = 99999
    fed.store.load_topology(payload, T0 + timedelta(days=1))
    stored = fed.store.get_topology_payload("v1")
    brk = next(d for d in stored["devices"] if d["device_id"] == "BRK-070")
    assert brk["customers"] == 0  # a loaded version is immutable


def test_rt14_the_mode_comes_from_the_stamp_not_the_caller(fed):
    estimate = fed.etr.estimate(FLOOD, T0 + timedelta(minutes=30))
    assert estimate.operating_mode == "normal"
    assert "storm_conditions" not in {c["name"] for c in estimate.components}
    fed.storm.declare("op.dana", "operator", "Cell over Feeder 12", T0,
                      feeders=["FDR-12"])
    # The declaration changes nothing for an incident outside
    # its scope, and nothing retroactively for this one.
    again = fed.etr.current(FLOOD)
    assert again.operating_mode == "normal"


# -- the open findings, characterised so a change is deliberate --------


def test_rt15_characterises_the_handover_storm_moment(fed):
    """Open finding RT-15: the handover's storm section is the
    fold at the handover's own moment (the other five sections
    re-perform against the current record), so a storm declared
    after assembly does not stale the artefact. This test pins
    the current behaviour; changing the finding's status means
    changing this test on purpose."""
    handover = fed.handover.assemble("op.dana", "operator", T0 + timedelta(hours=6))
    fed.storm.declare("op.dana", "operator", "New cell",
                      T0 + timedelta(hours=7), feeders=["FDR-07"])
    assert fed.handover.verify(handover.handover_id) == []
    fed.handover.accept(handover.handover_id, "op.reyes", "operator",
                        T0 + timedelta(hours=8))


def test_rt16_characterises_correlation_span_chaining():
    """Finding RT-16, as Round 12 settled it. The original
    characterisation: the pack's window is applied to the
    nearest member, so consequence alarms chained an incident's
    membership span without bound — while anchors are placed
    before consequences, so two faults never merged into one
    incident. Round 12 adopted the span cap as correlation pack
    2026.10.2, so this test now pins the deliberate change, in
    both directions: under the current pack the same ladder is
    capped at four hours from the anchor (the sags beyond it
    uncorrelated with the named reason, the second trip still
    its own incident), and under the kept 2026.10.1 pack the
    ladder re-performs uncapped, exactly as it was computed
    when that pack was current. The full grading lives in
    tests/test_r12.py; this test stays the finding's pin."""
    from grid_triage.correlation import (
        CORRELATION_PACK_V1,
        REASON_BEYOND_SPAN,
    )

    view = TopologyView(load_topology_file("v1"))
    alarms = [_alarm("A1", "BRK-070", "FDR-07", "breaker_trip", T0)]
    t = T0
    for i in range(33):
        t += timedelta(minutes=9)
        alarms.append(_alarm(f"B{i}", "SEC-072", "FDR-07", "voltage_sag", t))
    alarms.append(_alarm("A2", "BRK-070", "FDR-07", "breaker_trip",
                         t + timedelta(minutes=9)))

    def span_of(result, incident_id):
        return max(
            a.raised_at for a in alarms
            if any(m.alarm_id == a.alarm_id and m.incident_id == incident_id
                   for m in result.memberships)
        )

    result = correlate(alarms, view, incident_id_for=lambda a: f"INC-{a.alarm_id}")
    assert {i.incident_id for i in result.incidents} == {"INC-A1", "INC-A2"}
    a2_members = {m.alarm_id for m in result.memberships if m.incident_id == "INC-A2"}
    # Under the cap the chain is cut, and the ladder's last sag
    # — nine minutes before the second trip, inside its window
    # and its span — joins the second incident, not the first.
    # (Uncapped, it chained onto the first incident instead;
    # that is exactly the behaviour the cap removes.)
    assert a2_members == {"A2", "B32"}
    # The cap: no member lies more than four hours from A1.
    assert span_of(result, "INC-A1") <= T0 + timedelta(hours=4)
    # And the first refused sag — the one the window still
    # reaches (nine minutes past the last member) and the cap
    # alone refuses — is visible with the cap's named reason.
    # Past it the chain is broken, so the window rule speaks
    # for the later sags in its own words.
    assert result.uncorrelated_reasons.get("B26") == REASON_BEYOND_SPAN

    # The historical pack re-performs unchanged: uncapped.
    legacy = correlate(
        alarms, view, CORRELATION_PACK_V1,
        incident_id_for=lambda a: f"INC-{a.alarm_id}",
    )
    assert span_of(legacy, "INC-A1") > T0 + timedelta(hours=4)
