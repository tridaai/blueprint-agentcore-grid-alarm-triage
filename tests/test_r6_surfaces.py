"""Round 6 surfaces: field updates, the topology declaration act
and re-correlation at parity across the operations API and the
Runtime invocation handler, plus a CLI pass over the same store."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.cli as cli
import grid_triage.runtime_app as runtime_app
from grid_triage.seed import T0

FLOOD = "INC-ALM-FLD-001"
DECLARE_AT = (T0 - timedelta(days=1)).isoformat()
EFFECTIVE_AT = (T0 - timedelta(days=1)).isoformat()
RECORRELATE_AT = (T0 + timedelta(minutes=50)).isoformat()
SENSOR_ALARM = {
    "alarm_id": "ALM-UPD-001",
    "stream_id": "STREAM-SCADA-NORTH",
    "device_id": "SEN-0723",
    "feeder_id": "FDR-07",
    "kind": "line_sensor_fault",
    "severity": "minor",
    "raised_at": (T0 + timedelta(minutes=3)).isoformat(),
    "message": "New sensor LS23 reports a fault indication",
}


@pytest.fixture()
def client(fed):
    api.override_services(fed)
    return TestClient(api.app)


def test_api_round6_flow(client):
    note = client.post(
        f"/incidents/{FLOOD}/updates",
        json={
            "kind": "crew_note",
            "reported_by": "crew.lee",
            "role": "field_crew",
            "note": "Tree on the line at REC-071.",
        },
    )
    assert note.status_code == 200
    assert note.json()["kind"] == "crew_note"

    bad_kind = client.post(
        f"/incidents/{FLOOD}/updates",
        json={"kind": "close_order", "reported_by": "x", "role": "operator"},
    )
    assert bad_kind.status_code == 422

    sensor = client.post(
        f"/incidents/{FLOOD}/updates",
        json={
            "kind": "alarm_observation",
            "reported_by": "crew.lee",
            "role": "field_crew",
            "alarm": SENSOR_ALARM,
        },
    )
    assert sensor.status_code == 200
    assert (
        sensor.json()["correlation"]["status"]
        == "unresolvable_under_pinned_topology"
    )

    too_early = client.post(
        f"/incidents/{FLOOD}/recorrelate",
        json={"recorrelated_by": "op.dana", "role": "operator",
              "as_of": RECORRELATE_AT},
    )
    assert too_early.status_code == 422
    assert any(
        "not been declared" in p
        for p in too_early.json()["detail"]["problems"]
    )

    declared = client.post(
        "/topology/declare",
        json={
            "version": "v2",
            "declared_by": "op.dana",
            "role": "operator",
            "declared_at": DECLARE_AT,
            "effective_at": EFFECTIVE_AT,
        },
    )
    assert declared.status_code == 200
    assert len(declared.json()["change_set"]) == 4
    duplicate = client.post(
        "/topology/declare",
        json={
            "version": "v2",
            "declared_by": "op.dana",
            "role": "operator",
            "declared_at": DECLARE_AT,
            "effective_at": EFFECTIVE_AT,
        },
    )
    assert duplicate.status_code == 422

    versions = client.get("/topology/versions").json()
    assert versions["versions"] == ["v1", "v2"]
    declarations = client.get("/topology/declarations").json()
    assert len(declarations["declarations"]) == 1

    revision = client.post(
        f"/incidents/{FLOOD}/recorrelate",
        json={"recorrelated_by": "op.dana", "role": "operator",
              "as_of": RECORRELATE_AT},
    )
    assert revision.status_code == 200
    body = revision.json()
    assert body["revised_incident_id"] == f"{FLOOD}-R2"
    assert body["diff"]["alarms_added"] == ["ALM-UPD-001"]
    assert len(body["diff"]["alarms_removed"]) == 13

    fetched = client.get(f"/revisions/{body['revision_id']}")
    assert fetched.status_code == 200
    chain = client.get(f"/incidents/{body['revised_incident_id']}/revisions")
    assert [r["revision_id"] for r in chain.json()["revisions"]] == [
        body["revision_id"]
    ]
    updates = client.get(f"/incidents/{FLOOD}/updates").json()
    assert len(updates["updates"]) == 2
    assert client.get("/incidents/INC-NOPE/updates").status_code == 404
    assert client.get("/revisions/REV-NOPE").status_code == 404


def test_runtime_round6_actions(fed):
    runtime_app._services = fed
    refused = runtime_app.handle_invocation(
        {"action": "recorrelate_incident", "incident_id": FLOOD,
         "recorrelated_by": "op.dana", "role": "operator",
         "as_of": RECORRELATE_AT}
    )
    assert refused["status"] == "refused"
    assert any("not been declared" in p for p in refused["problems"])

    declared = runtime_app.handle_invocation(
        {"action": "declare_topology", "version": "v2",
         "declared_by": "op.dana", "role": "operator",
         "declared_at": DECLARE_AT, "effective_at": EFFECTIVE_AT}
    )
    assert declared["status"] == "declared"

    recorded = runtime_app.handle_invocation(
        {"action": "post_field_update", "incident_id": FLOOD,
         "kind": "alarm_observation", "reported_by": "crew.lee",
         "role": "field_crew", "alarm": SENSOR_ALARM}
    )
    assert recorded["status"] == "recorded"
    assert (
        recorded["update"]["correlation"]["status"]
        == "unresolvable_under_pinned_topology"
    )

    revision = runtime_app.handle_invocation(
        {"action": "recorrelate_incident", "incident_id": FLOOD,
         "recorrelated_by": "op.dana", "role": "operator",
         "as_of": RECORRELATE_AT}
    )
    assert revision["status"] == "recorrelated"
    assert revision["revision"]["diff"]["alarms_added"] == ["ALM-UPD-001"]

    chain = runtime_app.handle_invocation(
        {"action": "get_incident_revisions", "incident_id": FLOOD}
    )
    assert chain["status"] == "ok" and len(chain["revisions"]) == 1
    versions = runtime_app.handle_invocation(
        {"action": "get_topology_versions"}
    )
    assert versions["versions"] == ["v1", "v2"]
    updates = runtime_app.handle_invocation(
        {"action": "get_field_updates", "incident_id": FLOOD}
    )
    assert len(updates["updates"]) == 1
    missing = runtime_app.handle_invocation(
        {"action": "get_field_updates", "incident_id": "INC-NOPE"}
    )
    assert missing["status"] == "no_such_incident"


def test_cli_round6_flow(fed, monkeypatch, capsys):
    monkeypatch.setenv("SQLITE_PATH", fed.settings.sqlite_path)
    monkeypatch.setenv("OBJECT_STORE_DIR", fed.settings.object_store_dir)
    assert (
        cli.main(
            ["declare-topology", "--by", "op.dana", "--role", "operator",
             "--declared-at", DECLARE_AT, "--effective-at", EFFECTIVE_AT]
        )
        == 0
    )
    declaration = json.loads(capsys.readouterr().out)
    assert declaration["version"] == "v2"
    assert (
        cli.main(
            ["field-update", FLOOD, "--kind", "crew_note",
             "--by", "crew.lee", "--role", "field_crew",
             "--note", "Tree on the line at REC-071."]
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["updates", FLOOD]) == 0
    updates = json.loads(capsys.readouterr().out)
    assert len(updates) == 1 and updates[0]["kind"] == "crew_note"
    assert (
        cli.main(
            ["recorrelate", FLOOD, "--by", "op.dana", "--role", "operator",
             "--as-of", RECORRELATE_AT]
        )
        == 0
    )
    revision = json.loads(capsys.readouterr().out)
    assert revision["revised_incident_id"] == f"{FLOOD}-R2"
    assert len(revision["diff"]["alarms_removed"]) == 13
    assert cli.main(["revisions", FLOOD]) == 0
    chain = json.loads(capsys.readouterr().out)
    assert len(chain) == 1
    assert cli.main(["topology-versions"]) == 0
    versions = json.loads(capsys.readouterr().out)
    assert versions["versions"] == ["v1", "v2"]
