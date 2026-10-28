"""Round 5 surfaces: work-order drafts, assignment acts and the
approval gate at parity across the operations API and the Runtime
invocation handler, a CLI pass over the same store, and the
mechanical scan that no surface can send a notification — the
notify shape records drafts, there is no send verb anywhere."""

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.cli as cli
import grid_triage.runtime_app as runtime_app
from grid_triage.seed import T0

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
AS_OF = (T0 + timedelta(minutes=30)).isoformat()
WIN_START = (T0 + timedelta(minutes=90)).isoformat()
WIN_END = (T0 + timedelta(minutes=270)).isoformat()
CLASH_START = (T0 + timedelta(minutes=30)).isoformat()
CLASH_END = (T0 + timedelta(minutes=250)).isoformat()


@pytest.fixture()
def client(fed):
    api.override_services(fed)
    return TestClient(api.app)


def _draft(client):
    response = client.post(
        f"/incidents/{FLOOD}/work-orders", json={"as_of": AS_OF}
    )
    assert response.status_code == 200
    return response.json()


def test_api_work_order_lifecycle(client):
    order = _draft(client)
    assert order["state"] == "draft"
    assert order["cause_class"] == "vegetation_contact"
    assert order["assignment"] is None and order["decision"] is None
    order_id = order["order_id"]

    clash = client.post(
        f"/work-orders/{order_id}/assign",
        json={
            "crew_id": "CREW-NORTH-VEG",
            "assigned_by": "op.dana",
            "window_start": CLASH_START,
            "window_end": CLASH_END,
        },
    )
    assert clash.status_code == 422
    assert any(
        "INC-EXT-2201" in p for p in clash.json()["detail"]["problems"]
    )

    assigned = client.post(
        f"/work-orders/{order_id}/assign",
        json={
            "crew_id": "CREW-NORTH-VEG",
            "assigned_by": "op.dana",
            "window_start": WIN_START,
            "window_end": WIN_END,
        },
    )
    assert assigned.status_code == 200

    revised = client.post(
        f"/work-orders/{order_id}/revise",
        json={"revised_by": "op.rivera", "operator_note": "Chipper at the gate."},
    )
    assert revised.status_code == 200
    revised_id = revised.json()["order_id"]
    assert revised.json()["version"] == 2

    client.post(
        f"/work-orders/{revised_id}/assign",
        json={
            "crew_id": "CREW-NORTH-VEG",
            "assigned_by": "op.dana",
            "window_start": WIN_START,
            "window_end": WIN_END,
        },
    )
    selfish = client.post(
        f"/work-orders/{revised_id}/approve",
        json={"decided_by": "op.rivera", "role": "operator"},
    )
    assert selfish.status_code == 422
    assert any(
        "different operator" in p for p in selfish.json()["detail"]["problems"]
    )

    approved = client.post(
        f"/work-orders/{revised_id}/approve",
        json={"decided_by": "op.dana", "role": "operator"},
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["decision"]["order_id"] == revised_id
    assert {n["channel"] for n in body["notifications"]} == {
        "field_crew",
        "customer_outage",
    }
    assert body["order"]["state"] == "approved"

    current = client.get(f"/incidents/{FLOOD}/work-orders").json()
    assert current["current"]["order_id"] == revised_id
    assert len(current["orders"]) == 2
    approved_list = client.get("/work-orders", params={"state": "approved"})
    assert [o["order_id"] for o in approved_list.json()] == [revised_id]
    assignments = client.get(f"/work-orders/{revised_id}/assignments").json()
    assert len(assignments["assignments"]) == 1
    decisions = client.get(f"/work-orders/{revised_id}/decisions").json()
    assert len(decisions["decisions"]) == 1

    assert (
        client.post("/incidents/INC-NOPE/work-orders", json={}).status_code
        == 404
    )
    assert client.get("/work-orders/WO-NOPE").status_code == 404


def test_api_rejection_flow(client):
    drafted = client.post(
        f"/incidents/{LONE}/work-orders", json={"as_of": AS_OF}
    ).json()
    order_id = drafted["order_id"]
    no_reason = client.post(
        f"/work-orders/{order_id}/reject",
        json={"decided_by": "op.dana", "role": "operator"},
    )
    assert no_reason.status_code == 422
    rejected = client.post(
        f"/work-orders/{order_id}/reject",
        json={
            "decided_by": "op.dana",
            "role": "operator",
            "reason": "Cause unclassified.",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["order"]["state"] == "rejected"
    assert rejected.json()["notifications"] == []
    approve = client.post(
        f"/work-orders/{order_id}/approve",
        json={"decided_by": "op.dana", "role": "operator"},
    )
    assert approve.status_code == 422


def test_runtime_actions(fed):
    runtime_app._services = fed
    drafted = runtime_app.handle_invocation(
        {"action": "draft_work_order", "incident_id": FLOOD, "as_of": AS_OF}
    )
    assert drafted["status"] == "drafted"
    order_id = drafted["order"]["order_id"]
    assert drafted["order"]["state"] == "draft"

    clash = runtime_app.handle_invocation(
        {
            "action": "assign_work_order",
            "order_id": order_id,
            "crew_id": "CREW-NORTH-VEG",
            "assigned_by": "op.dana",
            "window_start": CLASH_START,
            "window_end": CLASH_END,
        }
    )
    assert clash["status"] == "refused"
    assert any("INC-EXT-2201" in p for p in clash["problems"])

    assigned = runtime_app.handle_invocation(
        {
            "action": "assign_work_order",
            "order_id": order_id,
            "crew_id": "CREW-NORTH-VEG",
            "assigned_by": "op.dana",
            "window_start": WIN_START,
            "window_end": WIN_END,
        }
    )
    assert assigned["status"] == "assigned"

    revised = runtime_app.handle_invocation(
        {
            "action": "revise_work_order",
            "order_id": order_id,
            "revised_by": "op.rivera",
            "operator_note": "Chipper at the gate.",
        }
    )
    assert revised["status"] == "revised"
    revised_id = revised["order"]["order_id"]
    runtime_app.handle_invocation(
        {
            "action": "assign_work_order",
            "order_id": revised_id,
            "crew_id": "CREW-NORTH-VEG",
            "assigned_by": "op.dana",
            "window_start": WIN_START,
            "window_end": WIN_END,
        }
    )
    selfish = runtime_app.handle_invocation(
        {
            "action": "approve_work_order",
            "order_id": revised_id,
            "decided_by": "op.rivera",
            "role": "operator",
        }
    )
    assert selfish["status"] == "refused"
    approved = runtime_app.handle_invocation(
        {
            "action": "approve_work_order",
            "order_id": revised_id,
            "decided_by": "op.dana",
            "role": "operator",
        }
    )
    assert approved["status"] == "approved"
    assert len(approved["notifications"]) == 2

    current = runtime_app.handle_invocation(
        {"action": "get_work_order", "incident_id": FLOOD}
    )
    assert current["current"]["state"] == "approved"
    listed = runtime_app.handle_invocation(
        {"action": "list_work_orders", "state": "approved"}
    )
    assert [o["order_id"] for o in listed["orders"]] == [revised_id]
    missing = runtime_app.handle_invocation(
        {"action": "draft_work_order", "incident_id": "INC-NOPE"}
    )
    assert missing["status"] == "no_such_incident"


def test_cli_work_order_flow(fed, monkeypatch, capsys):
    monkeypatch.setenv("SQLITE_PATH", fed.settings.sqlite_path)
    monkeypatch.setenv("OBJECT_STORE_DIR", fed.settings.object_store_dir)
    assert (
        cli.main(["work-order", FLOOD, "--as-of", AS_OF]) == 0
    )
    order = json.loads(capsys.readouterr().out)
    order_id = order["order_id"]
    assert order["state"] == "draft"
    assert (
        cli.main(
            [
                "assign-work-order", order_id,
                "--crew", "CREW-NORTH-VEG", "--by", "op.dana",
                "--from", CLASH_START, "--to", CLASH_END,
            ]
        )
        == 1
    )
    assert "refused" in capsys.readouterr().out
    assert (
        cli.main(
            [
                "assign-work-order", order_id,
                "--crew", "CREW-NORTH-VEG", "--by", "op.dana",
                "--from", WIN_START, "--to", WIN_END,
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        cli.main(
            ["approve-work-order", order_id, "--by", "op.dana", "--role", "operator"]
        )
        == 0
    )
    decided = json.loads(capsys.readouterr().out)
    assert decided["decision"]["decision"] == "approved"
    assert len(decided["notifications"]) == 2
    assert cli.main(["work-orders", "--state", "approved"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [o["order_id"] for o in listed] == [order_id]


def test_no_send_surface_exists():
    """Notifications are drafts: no route, Runtime action or
    function in the package offers to send one."""
    src = Path(__file__).resolve().parents[1] / "src" / "grid_triage"
    api_src = (src / "api.py").read_text()
    routes = re.findall(r'@app\.(?:get|post)\("([^"]+)"', api_src)
    assert routes, "the scan must see the routes"
    for route in routes:
        for verb in ("send", "deliver", "transmit", "dispatch"):
            assert verb not in route, f"route {route} is a send surface"
    runtime_src = (src / "runtime_app.py").read_text()
    actions = re.findall(r'action == "([^"]+)"', runtime_src)
    assert actions, "the scan must see the actions"
    for action in actions:
        for verb in ("send", "deliver", "transmit"):
            assert verb not in action, f"action {action} is a send surface"
    for path in src.glob("*.py"):
        for name in re.findall(r"def (\w+)", path.read_text()):
            assert not name.startswith(("send_", "deliver_", "transmit_")), (
                f"{path.name} defines {name} — a send surface"
            )
