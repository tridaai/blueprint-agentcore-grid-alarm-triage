"""Round 9 surface parity: the console board and the handover
acts behave the same over HTTP, through the runtime dispatch
and on the CLI, and the console page serves over HTTP.
"""

import json
from datetime import timedelta

from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.cli as cli
import grid_triage.runtime_app as runtime_app
from grid_triage.seed import T0

AT = (T0 + timedelta(hours=2)).isoformat()
AT_LATER = (T0 + timedelta(hours=2, minutes=5)).isoformat()


def test_api_handover_flow_and_console(fed):
    api.override_services(fed)
    client = TestClient(api.app)
    # The console serves, and its data is the board.
    assert client.get("/console").status_code == 200
    data = client.get("/console/data").json()
    assert len(data["incidents"]) == 2
    assert data["handover"] is None

    created = client.post(
        "/handovers",
        json={
            "handed_over_by": "op.dana",
            "role": "operator",
            "handed_over_at": AT,
        },
    )
    assert created.status_code == 200, created.text
    handover = created.json()
    hid = handover["handover_id"]
    # A bad role is refused with the problems named.
    refused = client.post(
        "/handovers",
        json={"handed_over_by": "op.dana", "role": "field_crew"},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["problems"]
    # The artefact reads back on its own route.
    fetched = client.get(f"/handovers/{hid}")
    assert fetched.status_code == 200
    assert fetched.json()["handover_id"] == hid
    # Verify is clean; self-acceptance is refused; the incoming
    # operator's acceptance binds the record hash.
    assert client.get(f"/handovers/{hid}/verify").json() == {
        "handover_id": hid,
        "problems": [],
    }
    self_accept = client.post(
        f"/handovers/{hid}/accept",
        json={"accepted_by": "op.dana", "role": "operator"},
    )
    assert self_accept.status_code == 422
    accepted = client.post(
        f"/handovers/{hid}/accept",
        json={
            "accepted_by": "op.reyes",
            "role": "operator",
            "accepted_at": AT_LATER,
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["record_hash"] == handover["record_hash"]
    listing = client.get("/handovers").json()["handovers"]
    assert [h["handover_id"] for h in listing] == [hid]
    assert listing[0]["acceptance"]["accepted_by"] == "op.reyes"
    data = client.get("/console/data").json()
    assert data["handover"]["handover_id"] == hid
    assert client.get("/handovers/nope").status_code == 404


def test_runtime_handover_actions(fed, monkeypatch):
    runtime_app._services = fed
    created = runtime_app.handle_invocation(
        {
            "action": "assemble_handover",
            "handed_over_by": "op.dana",
            "role": "operator",
            "handed_over_at": AT,
        }
    )
    assert created["status"] == "assembled"
    hid = created["handover"]["handover_id"]
    assert runtime_app.handle_invocation(
        {"action": "verify_handover", "handover_id": hid}
    ) == {"status": "ok", "handover_id": hid, "problems": []}
    accepted = runtime_app.handle_invocation(
        {
            "action": "accept_handover",
            "handover_id": hid,
            "accepted_by": "op.reyes",
            "role": "operator",
            "accepted_at": AT_LATER,
        }
    )
    assert accepted["status"] == "accepted"
    assert accepted["acceptance"]["accepted_by"] == "op.reyes"
    board = runtime_app.handle_invocation({"action": "get_console_board"})
    assert board["status"] == "ok"
    assert board["board"]["handover"]["handover_id"] == hid
    assert len(board["board"]["incidents"]) == 2
    listing = runtime_app.handle_invocation({"action": "list_handovers"})
    assert [h["handover_id"] for h in listing["handovers"]] == [hid]
    unknown = runtime_app.handle_invocation(
        {"action": "get_handover", "handover_id": "nope"}
    )
    assert unknown == {"status": "no_such_handover", "handover_id": "nope"}


def test_cli_handover_round_trip(tmp_path, fed, monkeypatch, capsys):
    monkeypatch.setenv("SQLITE_PATH", fed.settings.sqlite_path)
    monkeypatch.setenv("OBJECT_STORE_DIR", fed.settings.object_store_dir)
    assert cli.main(["console-board"]) == 0
    board = json.loads(capsys.readouterr().out)
    assert len(board["incidents"]) == 2
    assert (
        cli.main(
            [
                "handover",
                "--by", "op.dana",
                "--role", "operator",
                "--at", AT,
            ]
        )
        == 0
    )
    handover = json.loads(capsys.readouterr().out)
    hid = handover["handover_id"]
    assert cli.main(["handover-verify", hid]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "handover_id": hid,
        "problems": [],
    }
    # Self-acceptance on the CLI is refused with exit code 1
    # (the CLI's refusal convention).
    assert (
        cli.main(
            [
                "handover-accept", hid,
                "--by", "op.dana",
                "--role", "operator",
            ]
        )
        == 1
    )
    capsys.readouterr()
    assert (
        cli.main(
            [
                "handover-accept", hid,
                "--by", "op.reyes",
                "--role", "operator",
                "--at", AT_LATER,
            ]
        )
        == 0
    )
    acceptance = json.loads(capsys.readouterr().out)
    assert acceptance["record_hash"] == handover["record_hash"]
    assert cli.main(["handovers"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert [h["handover_id"] for h in listing] == [hid]
    assert listing[0]["acceptance"]["accepted_by"] == "op.reyes"
