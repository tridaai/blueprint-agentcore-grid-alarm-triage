"""Round 4 surfaces: ETR estimates, the roster, the closure act
and the accuracy ledger at parity across the operations API and
the Runtime invocation handler."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.runtime_app as runtime_app
from grid_triage.seed import T0, lone_alarms

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
AS_OF = (T0 + timedelta(minutes=30)).isoformat()
LONE_AS_OF = (lone_alarms()[0].raised_at + timedelta(minutes=15)).isoformat()
RESTORED = (T0 + timedelta(minutes=270)).isoformat()
SUMMARY = "Vegetation crew cleared the tree contact at REC-071; Feeder 7 restored."


@pytest.fixture()
def client(fed):
    api.override_services(fed)
    return TestClient(api.app)


def test_api_etr_lifecycle(client):
    empty = client.get(f"/incidents/{FLOOD}/etr").json()
    assert empty["current"] is None and empty["history"] == []

    estimate = client.post(
        f"/incidents/{FLOOD}/etr/estimate", json={"as_of": AS_OF}
    ).json()
    assert estimate["status"] == "computed"
    assert estimate["basis"] == "computed_from_rates"
    assert estimate["total_minutes"] == 220
    assert estimate["estimated_duration_minutes"] == 250.0

    read = client.get(f"/incidents/{FLOOD}/etr").json()
    assert read["current"]["estimate_id"] == estimate["estimate_id"]
    assert len(read["history"]) == 1

    lone = client.post(
        f"/incidents/{LONE}/etr/estimate", json={"as_of": LONE_AS_OF}
    ).json()
    assert lone["status"] == "not_computable"
    assert lone["missing_inputs"] == ["cause_class"]
    assert lone["estimated_restored_at"] is None

    assert client.get("/incidents/INC-NOPE/etr").status_code == 404
    assert (
        client.post("/incidents/INC-NOPE/etr/estimate", json={}).status_code == 404
    )


def test_api_roster(client):
    roster = client.get("/roster").json()
    assert roster["version"] == "v1"
    assert {c["crew_id"] for c in roster["crews"]} == {
        "CREW-NORTH-VEG",
        "CREW-ALDER-LINE",
        "CREW-SOUTH-EQUIP",
    }


def test_api_closure_and_accuracy(client):
    client.post(f"/incidents/{FLOOD}/etr/estimate", json={"as_of": AS_OF})
    assert client.get(f"/incidents/{FLOOD}/closure").json()["closure"] is None

    closed = client.post(
        f"/incidents/{FLOOD}/close",
        json={
            "closed_by": "op.dana",
            "role": "operator",
            "restored_at": RESTORED,
            "resolution_summary": SUMMARY,
        },
    )
    assert closed.status_code == 200
    body = closed.json()
    assert body["closure"]["closed_by"] == "op.dana"
    assert body["outcome"]["status"] == "compared"
    assert body["outcome"]["signed_error_minutes"] == 20.0
    assert body["memory_record"]["incident_id"] == FLOOD

    read = client.get(f"/incidents/{FLOOD}/closure").json()
    assert read["closure"]["closure_id"] == body["closure"]["closure_id"]
    assert read["outcome"]["outcome_id"] == body["outcome"]["outcome_id"]

    again = client.post(
        f"/incidents/{FLOOD}/close",
        json={
            "closed_by": "op.dana",
            "role": "operator",
            "restored_at": RESTORED,
            "resolution_summary": SUMMARY,
        },
    )
    assert again.status_code == 422
    assert any(
        "already closed" in p for p in again.json()["detail"]["problems"]
    )
    assert client.get("/incidents/INC-NOPE/closure").status_code == 404

    ledger = client.get("/etr/accuracy").json()
    assert ledger["by_cause_class"] == [
        {
            "cause_class": "vegetation_contact",
            "compared_count": 1,
            "mean_signed_error_minutes": 20.0,
            "mean_absolute_error_minutes": 20.0,
            "max_absolute_error_minutes": 20.0,
        }
    ]
    assert ledger["unestimated_closures"] == 0


def test_runtime_actions(fed):
    runtime_app._services = fed
    estimated = runtime_app.handle_invocation(
        {"action": "estimate_etr", "incident_id": FLOOD, "as_of": AS_OF}
    )
    assert estimated["status"] == "estimated"
    assert estimated["estimate"]["total_minutes"] == 220

    read = runtime_app.handle_invocation({"action": "get_etr", "incident_id": FLOOD})
    assert read["status"] == "ok"
    assert read["current"]["estimate_id"] == estimated["estimate"]["estimate_id"]
    assert len(read["history"]) == 1

    roster = runtime_app.handle_invocation({"action": "get_roster"})
    assert roster["status"] == "ok"
    assert roster["roster"]["version"] == "v1"

    closed = runtime_app.handle_invocation(
        {
            "action": "close_incident",
            "incident_id": FLOOD,
            "closed_by": "op.dana",
            "role": "operator",
            "restored_at": RESTORED,
            "resolution_summary": SUMMARY,
        }
    )
    assert closed["status"] == "closed"
    assert closed["outcome"]["signed_error_minutes"] == 20.0

    again = runtime_app.handle_invocation(
        {
            "action": "close_incident",
            "incident_id": FLOOD,
            "closed_by": "op.dana",
            "role": "operator",
            "restored_at": RESTORED,
            "resolution_summary": SUMMARY,
        }
    )
    assert again["status"] == "refused"
    assert any("already closed" in p for p in again["problems"])

    closure = runtime_app.handle_invocation(
        {"action": "get_closure", "incident_id": FLOOD}
    )
    assert closure["status"] == "ok"
    assert closure["closure"]["closure_id"] == closed["closure"]["closure_id"]

    ledger = runtime_app.handle_invocation({"action": "etr_accuracy"})
    assert ledger["status"] == "ok"
    assert ledger["ledger"]["overall"]["compared_count"] == 1

    missing = runtime_app.handle_invocation(
        {"action": "estimate_etr", "incident_id": "INC-NOPE", "as_of": AS_OF}
    )
    assert missing["status"] == "no_such_incident"
