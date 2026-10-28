"""Round 2: the severity engine — computed bands with recorded
inputs, content-addressed superseding assessments, the brief's
band discipline, and the acknowledgement queue and act."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.runtime_app as runtime_app
from grid_triage.agent import BandMismatchRefusal, IncidentAssembler
from grid_triage.models import Alarm, BriefStatement, Citation, Incident
from grid_triage.seed import T0
from grid_triage.severity import (
    SEVERITY_PACK,
    SeverityRefusal,
    band_for_score,
    compute_assessment,
)
from grid_triage.topology import TopologyView

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
LONE_T0 = datetime(2026, 10, 14, 9, 41, 0, tzinfo=timezone.utc)
ACK_AT = datetime(2026, 10, 14, 4, 0, 0, tzinfo=timezone.utc)


# -- the planted fixtures, hand-computed --------------------------------
# Flood (Feeder 7, hospital feeder), assessed at T0+30min:
#   customers 1440 -> min(50, 1440//25) = 50; hospital weight 30;
#   duration 30min -> 1; breadth 40 alarms -> 4. Score 85 -> SEV-1.
# Lone trip (Feeder 3), assessed at anchor+15min:
#   customers 210 -> 8; emergency_911 weight 20; duration 15 -> 0;
#   breadth 1 -> 0. Score 28 -> SEV-3.


def test_flood_severity_exact(fed):
    a = fed.severity.assess(FLOOD, T0 + timedelta(minutes=30))
    assert a.band == "SEV-1"
    assert a.score == 85
    assert a.topology_version == "v1"
    assert a.pack_version == SEVERITY_PACK["version"]
    assert a.supersedes is None
    assert a.missing_inputs == []
    assert a.inputs["customers_affected"] == 1440
    assert a.inputs["member_alarm_count"] == 40
    assert a.inputs["duration_minutes"] == 30.0
    assert a.inputs["slice_device_ids"] == sorted(
        ["BRK-070", "REC-071", "SEC-072", "SEC-073", "XF-0721", "XF-0731",
         "SEN-0722", "SEN-0732"]
    )
    assert a.inputs["critical_loads"] == [
        {
            "load_id": "CL-HOSPITAL",
            "kind": "hospital",
            "name": "Kestrel General Hospital",
            "device_id": "XF-0721",
            "weight": 30,
        }
    ]
    assert a.inputs["points"] == {
        "customers": 50,
        "critical_loads": 30,
        "duration": 1,
        "breadth": 4,
    }
    assert a.escalation_required is True
    assert "operator" in a.ack_roles


def test_lone_severity_exact(fed):
    a = fed.severity.assess(LONE, LONE_T0 + timedelta(minutes=15))
    assert a.band == "SEV-3"
    assert a.score == 28
    assert a.inputs["customers_affected"] == 210
    assert a.inputs["points"] == {
        "customers": 8,
        "critical_loads": 20,
        "duration": 0,
        "breadth": 0,
    }
    assert a.escalation_required is False


def test_assessment_supersedes_by_link_and_recompute_is_noop(fed):
    svc = fed.severity
    a1 = svc.assess(FLOOD, T0 + timedelta(minutes=30))
    again = svc.assess(FLOOD, T0 + timedelta(minutes=30))
    assert again.assessment_id == a1.assessment_id
    assert len(svc.history(FLOOD)) == 1

    a2 = svc.assess(FLOOD, T0 + timedelta(minutes=90))
    assert a2.assessment_id != a1.assessment_id
    assert a2.supersedes == a1.assessment_id
    assert a2.score == 87  # duration term grows 1 -> 3, nothing else moves
    assert a2.band == "SEV-1"
    assert svc.current(FLOOD).assessment_id == a2.assessment_id
    # The superseded assessment is unchanged, byte for byte.
    assert svc.store.get_severity_assessment(a1.assessment_id) == a1
    assert [a.assessment_id for a in svc.history(FLOOD)] == [
        a1.assessment_id,
        a2.assessment_id,
    ]


def test_assessment_lands_on_the_evidence_chain(fed):
    a = fed.severity.assess(FLOOD, T0 + timedelta(minutes=30))
    kinds = [i.kind for i in fed.store.list_evidence(FLOOD)]
    assert "severity_assessment" in kinds
    item = next(
        i for i in fed.store.list_evidence(FLOOD) if i.item_id == f"EV-{a.assessment_id}"
    )
    assert fed.chain.payload_of(item)["band"] == "SEV-1"
    assert fed.chain.verify_chain(FLOOD) is True


# -- missing inputs: UNRATED, named, never guessed -----------------------


def _synthetic_view(devices, loads=None):
    return TopologyView(
        {
            "version": "vX",
            "substations": [{"substation_id": "S1", "name": "S"}],
            "feeders": [{"feeder_id": "F1", "substation_id": "S1", "name": "F"}],
            "devices": devices,
            "critical_loads": loads or [],
        }
    )


def _synthetic_incident():
    incident = Incident(
        incident_id="INC-SYN",
        feeder_id="F1",
        substation_id="S1",
        topology_version="vX",
        correlation_pack_version="2026.10.1",
        anchor_alarm_id="A1",
        created_at=T0,
    )
    alarm = Alarm(
        alarm_id="A1",
        stream_id="S",
        device_id="B1",
        feeder_id="F1",
        kind="breaker_trip",
        severity="critical",
        raised_at=T0,
    )
    return incident, {"A1": alarm}


def test_unrated_when_customer_counts_are_absent():
    devices = [
        {"device_id": "B1", "feeder_id": "F1", "kind": "feeder_breaker",
         "name": "B", "parent_device_id": None},
        {"device_id": "T1", "feeder_id": "F1", "kind": "transformer",
         "name": "T", "parent_device_id": "B1"},
    ]
    incident, alarms = _synthetic_incident()
    a = compute_assessment(incident, alarms, _synthetic_view(devices), T0)
    assert a.band == "UNRATED"
    assert a.score is None
    assert "customers_affected" in a.missing_inputs
    assert a.escalation_required is True  # an unknown severity escalates


def test_unrated_when_topology_version_not_loaded(services):
    alarm = Alarm(
        alarm_id="ALM-X-001",
        stream_id="S",
        device_id="BRK-070",
        feeder_id="FDR-07",
        kind="breaker_trip",
        severity="critical",
        raised_at=T0,
    )
    services.store.add_alarm(alarm)
    services.store.add_incident(
        Incident(
            incident_id="INC-X",
            feeder_id="FDR-07",
            substation_id="SUB-ALDER",
            topology_version="v9",
            correlation_pack_version="2026.10.1",
            anchor_alarm_id="ALM-X-001",
            created_at=T0,
        )
    )
    from grid_triage.models import IncidentMembership

    services.store.add_membership(
        IncidentMembership(incident_id="INC-X", alarm_id="ALM-X-001", rule_id="anchor")
    )
    a = services.severity.assess("INC-X", T0 + timedelta(minutes=5))
    assert a.band == "UNRATED"
    assert a.score is None
    assert "topology_version" in a.missing_inputs
    assert "customers_affected" in a.missing_inputs


# -- band edges, graded on both sides ------------------------------------


def test_band_thresholds_both_sides():
    assert band_for_score(0) == "SEV-4"
    assert band_for_score(19) == "SEV-4"
    assert band_for_score(20) == "SEV-3"
    assert band_for_score(44) == "SEV-3"
    assert band_for_score(45) == "SEV-2"
    assert band_for_score(74) == "SEV-2"
    assert band_for_score(75) == "SEV-1"
    assert band_for_score(200) == "SEV-1"


def test_one_customer_either_side_of_the_cap_threshold():
    def view(customers):
        return _synthetic_view(
            [
                {"device_id": "B1", "feeder_id": "F1", "kind": "feeder_breaker",
                 "name": "B", "parent_device_id": None, "customers": 0},
                {"device_id": "T1", "feeder_id": "F1", "kind": "transformer",
                 "name": "T", "parent_device_id": "B1", "customers": customers},
            ]
        )

    incident, alarms = _synthetic_incident()
    below = compute_assessment(incident, alarms, view(1249), T0)
    above = compute_assessment(incident, alarms, view(1250), T0)
    assert below.inputs["points"]["customers"] == 49
    assert above.inputs["points"]["customers"] == 50
    assert above.score - below.score == 1


def test_duration_edge_both_sides(fed):
    a29 = fed.severity.assess(FLOOD, T0 + timedelta(minutes=29))
    assert a29.inputs["points"]["duration"] == 0
    a30 = fed.severity.assess(FLOOD, T0 + timedelta(minutes=30))
    assert a30.inputs["points"]["duration"] == 1


# -- the brief carries the band, and only the band ------------------------


def test_brief_carries_the_assessed_severity(fed):
    brief = fed.assembler.assemble(FLOOD, datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc))
    severity_statements = [s for s in brief.statements if "Severity" in s.text]
    assert len(severity_statements) == 1
    stmt = severity_statements[0]
    assert "SEV-1" in stmt.text and "(score 85)" in stmt.text
    cited_items = {c.evidence_item_id for c in stmt.citations}
    severity_items = {
        i.item_id for i in fed.store.list_evidence(FLOOD) if i.kind == "severity_assessment"
    }
    assert cited_items <= severity_items and cited_items
    assert fed.assembler.resolver.problems(FLOOD, brief.statements) == []
    assert fed.chain.verify_chain(FLOOD) is True


class _WrongBandModel:
    profile = "offline"

    def propose_brief(self, facts):
        return [
            BriefStatement(
                text="Severity: SEV-4 — a minor event, stood down.",
                citations=[Citation(evidence_item_id="EV-ALM-FLD-001", field="kind")],
            )
        ]


def test_brief_stating_a_different_band_is_refused(fed):
    assembler = IncidentAssembler(fed.store, fed.chain, _WrongBandModel())
    with pytest.raises(BandMismatchRefusal):
        assembler.assemble(FLOOD, datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc))
    assert fed.store.list_briefs(FLOOD) == []


# -- acknowledgement: requirement, queue, act -----------------------------


def test_acknowledgement_flow(fed):
    svc = fed.severity
    a = svc.assess(FLOOD, T0 + timedelta(minutes=30))
    assert [p.assessment_id for p in svc.pending_acknowledgements()] == [a.assessment_id]

    with pytest.raises(SeverityRefusal):
        svc.acknowledge(a.assessment_id, "crew.lee", "field_crew", ACK_AT)
    with pytest.raises(SeverityRefusal):
        svc.acknowledge(a.assessment_id, "", "operator", ACK_AT)
    with pytest.raises(KeyError):
        svc.acknowledge("SEVA-nope", "op.dana", "operator", ACK_AT)

    ack = svc.acknowledge(a.assessment_id, "op.dana", "operator", ACK_AT)
    assert ack.assessment_id == a.assessment_id
    assert ack.acknowledged_by == "op.dana" and ack.role == "operator"
    assert svc.pending_acknowledgements() == []

    with pytest.raises(SeverityRefusal):
        svc.acknowledge(a.assessment_id, "op.erin", "duty_manager", ACK_AT)


def test_band_without_requirement_refuses_acknowledgement(fed):
    a = fed.severity.assess(LONE, LONE_T0 + timedelta(minutes=15))
    assert a.escalation_required is False
    with pytest.raises(SeverityRefusal):
        fed.severity.acknowledge(a.assessment_id, "op.dana", "operator", ACK_AT)


# -- API parity ------------------------------------------------------------


@pytest.fixture()
def client(fed):
    api.override_services(fed)
    return TestClient(api.app)


def test_severity_api_read_assess_acknowledge(client):
    body = client.get(f"/incidents/{FLOOD}/severity").json()
    assert body["current"] is None and body["history"] == []

    assessed = client.post(
        f"/incidents/{FLOOD}/severity/assess",
        json={"as_of": (T0 + timedelta(minutes=30)).isoformat()},
    ).json()
    assert assessed["band"] == "SEV-1" and assessed["score"] == 85

    body = client.get(f"/incidents/{FLOOD}/severity").json()
    assert body["current"]["assessment_id"] == assessed["assessment_id"]
    assert len(body["history"]) == 1
    assert body["pending_acknowledgement_ids"] == [assessed["assessment_id"]]

    queue = client.get("/severity/acknowledgement-queue").json()
    assert [p["assessment_id"] for p in queue["pending"]] == [assessed["assessment_id"]]

    refused = client.post(
        "/severity/acknowledgements",
        json={
            "assessment_id": assessed["assessment_id"],
            "acknowledged_by": "crew.lee",
            "role": "field_crew",
        },
    )
    assert refused.status_code == 422

    ack = client.post(
        "/severity/acknowledgements",
        json={
            "assessment_id": assessed["assessment_id"],
            "acknowledged_by": "op.dana",
            "role": "operator",
        },
    )
    assert ack.status_code == 200
    assert ack.json()["role"] == "operator"
    assert client.get("/severity/acknowledgement-queue").json() == {"pending": []}

    assert client.get("/incidents/INC-NOPE/severity").status_code == 404
    assert client.post("/incidents/INC-NOPE/severity/assess", json={}).status_code == 404


def test_runtime_severity_actions(fed):
    runtime_app._services = fed
    body = runtime_app.handle_invocation(
        {
            "action": "assess_severity",
            "incident_id": FLOOD,
            "as_of": (T0 + timedelta(minutes=30)).isoformat(),
        }
    )
    assert body["status"] == "assessed"
    assessment_id = body["assessment"]["assessment_id"]
    assert body["assessment"]["band"] == "SEV-1"

    body = runtime_app.handle_invocation({"action": "get_severity", "incident_id": FLOOD})
    assert body["current"]["assessment_id"] == assessment_id
    assert len(body["history"]) == 1

    body = runtime_app.handle_invocation({"action": "acknowledgement_queue"})
    assert [p["assessment_id"] for p in body["pending"]] == [assessment_id]

    body = runtime_app.handle_invocation(
        {
            "action": "acknowledge_severity",
            "assessment_id": assessment_id,
            "acknowledged_by": "crew.lee",
            "role": "field_crew",
        }
    )
    assert body["status"] == "refused"

    body = runtime_app.handle_invocation(
        {
            "action": "acknowledge_severity",
            "assessment_id": assessment_id,
            "acknowledged_by": "op.dana",
            "role": "operator",
        }
    )
    assert body["status"] == "acknowledged"
    body = runtime_app.handle_invocation({"action": "acknowledgement_queue"})
    assert body["pending"] == []

    body = runtime_app.handle_invocation(
        {"action": "assess_severity", "incident_id": "INC-NOPE"}
    )
    assert body["status"] == "no_such_incident"
