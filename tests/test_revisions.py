"""Round 6: field updates, the topology declaration act, and
re-correlation — graded against the planted v2 change set.

The v2 changes are fixed data: Feeder 7 splits (the SEC-073
section — devices SEC-073, SEN-0732, XF-0731 — moves to feeder
FDR-07B), a new sensor SEN-0723 appears on the retained section,
REG-1202 is replaced, and a hospital annex load lands on the
Feeder 3 transformer. Every expectation below is derived from
that change set and the planted flood, not from the code's own
outputs.
"""

from datetime import datetime, timedelta, timezone

import pytest

from grid_triage.models import Alarm
from grid_triage.revisions import RevisionRefusal, TopologyRefusal, UpdateRefusal
from grid_triage.seed import FLOOD_ALARM_IDS, T0, flood_alarms

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
LONE_RAISED = datetime(2026, 10, 14, 9, 41, tzinfo=timezone.utc)
SPLIT_DEVICES = {"SEC-073", "SEN-0732", "XF-0731"}
BRANCH_ALARM_IDS = sorted(
    a.alarm_id for a in flood_alarms() if a.device_id in SPLIT_DEVICES
)


def _sensor_alarm(alarm_id="ALM-UPD-001") -> Alarm:
    return Alarm(
        alarm_id=alarm_id,
        stream_id="STREAM-SCADA-NORTH",
        device_id="SEN-0723",
        feeder_id="FDR-07",
        kind="line_sensor_fault",
        severity="minor",
        raised_at=T0 + timedelta(minutes=3),
        message="New sensor LS23 reports a fault indication",
    )


def _declare(svc, effective=None):
    return svc.topology_acts.declare(
        "v2",
        "op.dana",
        "operator",
        T0 - timedelta(days=1),
        effective or (T0 - timedelta(days=1)),
    )


# -- field updates ----------------------------------------------------


def test_field_update_kinds_recorded(fed):
    note = fed.updates.post_update(
        FLOOD, "crew_note", "crew.lee", "field_crew",
        T0 + timedelta(minutes=20), note="Tree on the line at REC-071.",
    )
    assert note.kind == "crew_note" and note.correlation == {}
    finding = fed.updates.post_update(
        FLOOD, "damage_finding", "crew.lee", "field_crew",
        T0 + timedelta(minutes=22),
        device_id="XF-0721", finding="Scorch marks on the T21 bushings.",
    )
    assert finding.device_id == "XF-0721"
    assert [u.update_id for u in fed.updates.list_updates(FLOOD)] == [
        note.update_id,
        finding.update_id,
    ]
    assert fed.chain.verify_chain(FLOOD)


def test_field_update_pack_refusals(fed):
    with pytest.raises(UpdateRefusal):
        fed.updates.post_update(
            FLOOD, "close_order", "crew.lee", "field_crew",
            T0, note="not a pack kind",
        )
    with pytest.raises(UpdateRefusal):
        fed.updates.post_update(
            FLOOD, "crew_note", "crew.lee", "auditor", T0, note="wrong role"
        )
    with pytest.raises(UpdateRefusal):
        fed.updates.post_update(
            FLOOD, "crew_note", "crew.lee", "field_crew", T0
        )  # a crew note with no note
    with pytest.raises(UpdateRefusal):
        fed.updates.post_update(
            FLOOD, "alarm_observation", "crew.lee", "field_crew", T0
        )  # an observation with no alarm
    with pytest.raises(UpdateRefusal):
        fed.updates.post_update(
            FLOOD, "damage_finding", "crew.lee", "field_crew", T0,
            device_id="XF-0721",
        )  # a finding with no finding
    with pytest.raises(KeyError):
        fed.updates.post_update(
            "INC-NOPE", "crew_note", "crew.lee", "field_crew", T0, note="x"
        )


def test_update_alarm_joins_under_pinned_topology(fed):
    alarm = Alarm(
        alarm_id="ALM-UPD-101", stream_id="STREAM-SCADA-NORTH",
        device_id="SEC-072", feeder_id="FDR-07",
        kind="current_fault_indication", severity="major",
        raised_at=T0 + timedelta(minutes=4),
        message="SEC-072 still shows a fault indication",
    )
    update = fed.updates.post_update(
        FLOOD, "alarm_observation", "disp.ray", "dispatcher",
        T0 + timedelta(minutes=21), alarm=alarm,
    )
    assert update.correlation["status"] == "joined"
    assert update.correlation["topology_version"] == "v1"
    assert update.correlation["rule_id"] == "hierarchy_window"
    membership = fed.store.membership_for_alarm("ALM-UPD-101")
    assert membership is not None and membership.incident_id == FLOOD
    assert fed.chain.verify_chain(FLOOD)
    # The same alarm can never be posted twice — history is not
    # overwritten.
    with pytest.raises(UpdateRefusal):
        fed.updates.post_update(
            FLOOD, "alarm_observation", "disp.ray", "dispatcher",
            T0 + timedelta(minutes=22), alarm=alarm,
        )


def test_update_alarm_v2_only_device_is_unresolvable_under_pinned(fed):
    update = fed.updates.post_update(
        FLOOD, "alarm_observation", "crew.lee", "field_crew",
        T0 + timedelta(minutes=21), alarm=_sensor_alarm(),
    )
    assert update.correlation["status"] == "unresolvable_under_pinned_topology"
    assert "SEN-0723" in update.correlation["reason"]
    assert "v1" in update.correlation["reason"]
    # The alarm is in the ingest log (evidence), but it is not a
    # member of the v1-pinned incident.
    assert fed.store.get_alarm("ALM-UPD-001") is not None
    assert fed.store.membership_for_alarm("ALM-UPD-001") is None
    assert fed.chain.verify_chain(FLOOD)


def test_update_alarm_excluded_kind_does_not_join(fed):
    alarm = Alarm(
        alarm_id="ALM-UPD-102", stream_id="STREAM-COMMS-SOUTH",
        device_id="SEC-072", feeder_id="FDR-07",
        kind="comms_poll_failure", severity="info",
        raised_at=T0 + timedelta(minutes=4),
        message="Poll failure on SEC-072",
    )
    update = fed.updates.post_update(
        FLOOD, "alarm_observation", "disp.ray", "dispatcher",
        T0 + timedelta(minutes=21), alarm=alarm,
    )
    assert update.correlation["status"] == "no_join"
    assert "excludes" in update.correlation["reason"]
    assert fed.store.membership_for_alarm("ALM-UPD-102") is None


# -- the declaration act ------------------------------------------------


def test_declaration_act(fed):
    declaration = _declare(fed)
    assert declaration.version == "v2"
    assert len(declaration.change_set) == 4
    assert fed.store.list_topology_versions() == ["v1", "v2"]
    with pytest.raises(TopologyRefusal):
        _declare(fed)  # already declared
    with pytest.raises(TopologyRefusal):
        fed.topology_acts.declare(
            "v3", "op.dana", "operator", T0, T0
        )  # not a declarable version


def test_declaration_role_and_effective_order(services):
    with pytest.raises(TopologyRefusal):
        services.topology_acts.declare(
            "v2", "crew.lee", "field_crew", T0, T0
        )
    with pytest.raises(TopologyRefusal):
        services.topology_acts.declare(
            "v2", "op.dana", "operator",
            T0, T0 - timedelta(hours=1),
        )  # effective before it is declared


# -- re-correlation -----------------------------------------------------


def test_recorrelate_requires_declaration_and_effectiveness(fed):
    with pytest.raises(RevisionRefusal) as exc:
        fed.revisions.recorrelate(FLOOD, "op.dana", "operator", T0)
    assert any("not been declared" in p for p in exc.value.problems)
    _declare(fed, effective=T0 + timedelta(hours=2))
    with pytest.raises(RevisionRefusal) as exc:
        fed.revisions.recorrelate(
            FLOOD, "op.dana", "operator", T0 + timedelta(hours=1)
        )
    assert any("not effective until" in p for p in exc.value.problems)


def test_flood_recorrelation_diff_is_exact(fed):
    before_incident = fed.store.get_incident(FLOOD).model_dump(mode="json")
    before_members = {
        m.alarm_id for m in fed.store.list_memberships(FLOOD)
    }
    assert before_members == set(FLOOD_ALARM_IDS)
    fed.severity.assess(FLOOD, T0 + timedelta(minutes=30))
    before_assessments = [
        a.model_dump(mode="json")
        for a in fed.store.list_severity_assessments(FLOOD)
    ]
    _declare(fed)
    # Declaring v2 alone changes nothing about the v1 incident.
    assert fed.store.get_incident(FLOOD).model_dump(mode="json") == before_incident

    fed.updates.post_update(
        FLOOD, "alarm_observation", "crew.lee", "field_crew",
        T0 + timedelta(minutes=21), alarm=_sensor_alarm(),
    )
    revision = fed.revisions.recorrelate(
        FLOOD, "op.dana", "operator", T0 + timedelta(minutes=50)
    )
    assert revision.revised_incident_id == f"{FLOOD}-R2"
    assert revision.base_topology_version == "v1"
    assert revision.target_topology_version == "v2"
    assert revision.diff["alarms_removed"] == BRANCH_ALARM_IDS
    assert len(revision.diff["alarms_removed"]) == 13
    assert revision.diff["alarms_added"] == ["ALM-UPD-001"]
    assert revision.diff["devices_removed"] == sorted(SPLIT_DEVICES)
    assert revision.diff["devices_added"] == ["SEN-0723"]
    revised = fed.store.get_incident(revision.revised_incident_id)
    assert revised.topology_version == "v2"
    assert len(fed.store.list_memberships(revised.incident_id)) == 28

    change = revision.severity_change
    assert change["changed"] is True
    assert (change["prior_band"], change["prior_score"]) == ("SEV-1", 85)
    assert (change["revised_band"], change["revised_score"]) == ("SEV-2", 63)
    assert change["moved_inputs"]["customers_affected"] == {
        "prior": 1440,
        "revised": 760,
    }
    assert change["moved_inputs"]["points.customers"] == {
        "prior": 50,
        "revised": 30,
    }
    assert revision.revised_estimate_id is not None
    assert (
        fed.etr.current(revised.incident_id).estimate_id
        == revision.revised_estimate_id
    )

    # The prior revision stands untouched, byte for byte.
    assert fed.store.get_incident(FLOOD).model_dump(mode="json") == before_incident
    assert {m.alarm_id for m in fed.store.list_memberships(FLOOD)} == before_members
    assert [
        a.model_dump(mode="json")
        for a in fed.store.list_severity_assessments(FLOOD)
    ] == before_assessments
    assert fed.chain.verify_chain(FLOOD)
    assert fed.chain.verify_chain(revised.incident_id)

    chain = fed.revisions.revision_chain(FLOOD)
    assert [r.revision_id for r in chain] == [revision.revision_id]
    assert fed.revisions.revision_chain(revised.incident_id)[0].revision_id == (
        revision.revision_id
    )
    with pytest.raises(RevisionRefusal) as exc:
        fed.revisions.recorrelate(
            FLOOD, "op.dana", "operator", T0 + timedelta(hours=1)
        )
    assert any("already re-correlated" in p for p in exc.value.problems)


def test_lone_severity_change_names_the_moved_inputs(fed):
    _declare(fed)
    revision = fed.revisions.recorrelate(
        LONE, "op.dana", "operator", LONE_RAISED + timedelta(minutes=15)
    )
    change = revision.severity_change
    assert change["changed"] is True
    assert (change["prior_band"], change["prior_score"]) == ("SEV-3", 28)
    assert (change["revised_band"], change["revised_score"]) == ("SEV-2", 58)
    assert change["moved_inputs"]["points.critical_loads"] == {
        "prior": 20,
        "revised": 50,
    }
    revised_loads = change["moved_inputs"]["critical_loads"]["revised"]
    assert {load["load_id"] for load in revised_loads} == {"CL-911", "CL-ANNEX"}
    assert revision.diff["alarms_added"] == []
    assert revision.diff["alarms_removed"] == []
    assert len(fed.store.list_memberships(revision.revised_incident_id)) == 1


def test_closed_incident_is_never_recorrelated(fed):
    closure, _outcome, _record = fed.closure.close_incident(
        LONE, "op.dana", "operator",
        LONE_RAISED + timedelta(minutes=60),
        "Breaker reclosed after inspection; no fault found.",
        LONE_RAISED + timedelta(minutes=75),
    )
    _declare(fed)
    with pytest.raises(RevisionRefusal) as exc:
        fed.revisions.recorrelate(
            LONE, "op.dana", "operator", LONE_RAISED + timedelta(minutes=90)
        )
    assert any(
        "closed" in p and closure.closure_id in p for p in exc.value.problems
    )
