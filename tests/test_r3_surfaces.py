"""Round 3 surfaces: classification, memory, priors and evidence
packs at parity across the operations API and the Runtime
invocation handler."""

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.runtime_app as runtime_app

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"


@pytest.fixture()
def client(fed):
    api.override_services(fed)
    return TestClient(api.app)


def test_api_memory_and_priors(client):
    assert client.post("/memory/seed-history").json()["count"] == 3
    records = client.get("/memory/records").json()
    assert len(records) == 3
    one = client.get(f"/memory/records/{records[0]['record_id']}").json()
    assert one["record_id"] == records[0]["record_id"]
    assert client.get("/memory/records/MEM-nope").status_code == 404

    priors = client.get(f"/incidents/{FLOOD}/priors").json()["priors"]
    assert [p["record"]["incident_id"] for p in priors] == [
        "INC-HIST-0042",
        "INC-HIST-0057",
    ]
    assert [p["score"] for p in priors] == [10, 7]
    assert client.get(f"/incidents/{LONE}/priors").json()["priors"] == []
    assert client.get("/incidents/INC-NOPE/priors").status_code == 404


def test_api_classification(client):
    body = client.post(f"/incidents/{FLOOD}/classify").json()
    assert body["cause_class"] == "vegetation_contact"
    assert body["rule_id"] == "lockout_with_downstream_fault"
    read = client.get(f"/incidents/{FLOOD}/classification").json()
    assert read["classification"]["cause_class"] == "vegetation_contact"
    detail = client.get(f"/incidents/{FLOOD}").json()
    assert detail["cause_classification"]["cause_class"] == "vegetation_contact"
    lone = client.post(f"/incidents/{LONE}/classify").json()
    assert lone["cause_class"] == "unknown"
    assert client.post("/incidents/INC-NOPE/classify").status_code == 404


def test_api_pack_lifecycle(client):
    client.post("/memory/seed-history")
    # Sealing before any brief exists is refused, naming the gap.
    refused = client.post(
        f"/incidents/{FLOOD}/pack/seal", json={"sealed_by": "op.dana"}
    )
    assert refused.status_code == 422
    assert any("brief" in p for p in refused.json()["detail"]["problems"])

    client.post(f"/incidents/{FLOOD}/brief")
    pack = client.post(
        f"/incidents/{FLOOD}/pack/seal", json={"sealed_by": "op.dana"}
    ).json()
    assert pack["version"] == 1
    assert pack["sealed_by"] == "op.dana"
    verify = client.get(f"/packs/{pack['pack_id']}/verify").json()
    assert verify == {"pack_id": pack["pack_id"], "valid": True, "problems": []}
    current = client.get(f"/incidents/{FLOOD}/pack").json()["pack"]
    assert current["pack_id"] == pack["pack_id"]
    history = client.get(f"/incidents/{FLOOD}/packs").json()["packs"]
    assert [p["pack_id"] for p in history] == [pack["pack_id"]]
    assert client.get("/packs/PACK-nope").status_code == 404


def test_runtime_actions(fed):
    runtime_app._services = fed
    out = runtime_app.handle_invocation({"action": "seed_history"})
    assert out["count"] == 3
    out = runtime_app.handle_invocation(
        {"action": "classify_cause", "incident_id": FLOOD}
    )
    assert out["classification"]["cause_class"] == "vegetation_contact"
    out = runtime_app.handle_invocation(
        {"action": "get_priors", "incident_id": FLOOD}
    )
    assert [p["record"]["incident_id"] for p in out["priors"]] == [
        "INC-HIST-0042",
        "INC-HIST-0057",
    ]
    record_id = out["priors"][0]["record"]["record_id"]
    out = runtime_app.handle_invocation(
        {"action": "get_memory_record", "record_id": record_id}
    )
    assert out["record"]["incident_id"] == "INC-HIST-0042"
    out = runtime_app.handle_invocation(
        {"action": "list_memory_records"}
    )
    assert len(out["records"]) == 3

    runtime_app.handle_invocation(
        {"action": "assemble_brief", "incident_id": FLOOD}
    )
    out = runtime_app.handle_invocation(
        {"action": "seal_pack", "incident_id": FLOOD, "sealed_by": "op.dana"}
    )
    assert out["status"] == "sealed"
    pack_id = out["pack"]["pack_id"]
    out = runtime_app.handle_invocation(
        {"action": "verify_pack", "pack_id": pack_id}
    )
    assert out["valid"] is True and out["problems"] == []
    out = runtime_app.handle_invocation(
        {"action": "list_packs", "incident_id": FLOOD}
    )
    assert [p["pack_id"] for p in out["packs"]] == [pack_id]
    out = runtime_app.handle_invocation(
        {"action": "get_pack", "pack_id": pack_id}
    )
    assert out["pack"]["pack_id"] == pack_id
    out = runtime_app.handle_invocation(
        {"action": "seal_pack", "incident_id": FLOOD, "sealed_by": ""}
    )
    assert out["status"] == "refused"
