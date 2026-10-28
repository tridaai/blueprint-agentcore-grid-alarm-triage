"""The operations API over the planted feed."""

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api


@pytest.fixture()
def client(fed):
    api.override_services(fed)
    return TestClient(api.app)


def test_health_and_readiness(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/readiness").json() == {"status": "ready"}


def test_alarms_and_uncorrelated_view(client):
    assert len(client.get("/alarms").json()) == 49
    uncorrelated = client.get("/alarms", params={"uncorrelated": True}).json()
    assert len(uncorrelated) == 8
    assert all(a["feeder_id"] == "FDR-12" for a in uncorrelated)


def test_alarm_detail_shows_membership_and_rule(client):
    body = client.get("/alarms/ALM-FLD-002").json()
    assert body["incident_id"] == "INC-ALM-FLD-001"
    assert body["correlation_rule"] == "anchor_joins_open_incident"
    nuisance = client.get("/alarms/ALM-NUI-001").json()
    assert nuisance["incident_id"] is None


def test_incident_reads_and_brief(client):
    incidents = client.get("/incidents").json()
    assert len(incidents) == 2
    detail = client.get("/incidents/INC-ALM-FLD-001").json()
    assert len(detail["memberships"]) == 40
    brief = client.post("/incidents/INC-ALM-FLD-001/brief").json()
    assert brief["statements"]
    assert client.get("/incidents/INC-ALM-FLD-001/chain").json()["chain_valid"] is True


def test_topology_endpoints(client):
    assert client.get("/topology/versions").json() == {"versions": ["v1"]}
    body = client.get("/topology/v1").json()
    assert len(body["devices"]) == 13
    assert len(body["critical_loads"]) == 3
    assert client.get("/topology/v99").status_code == 404


def test_missing_incident_is_404(client):
    assert client.get("/incidents/INC-NOPE").status_code == 404
    assert client.post("/incidents/INC-NOPE/brief").status_code == 404
