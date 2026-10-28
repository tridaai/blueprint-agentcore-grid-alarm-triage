"""Round 7 surfaces: restoration signals, confirmations, the
gated close, work-order completion, splits and the event log at
parity across the operations API and the Runtime invocation
handler, plus a CLI pass over the same store."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.cli as cli
import grid_triage.runtime_app as runtime_app
from grid_triage.seed import T0

FLOOD = "INC-ALM-FLD-001"
AT = (T0 + timedelta(minutes=30)).isoformat()
RESTORED_AT = (T0 + timedelta(minutes=270)).isoformat()
SIGNAL_AT = (T0 + timedelta(minutes=265)).isoformat()
DEVICES = [
    "BRK-070", "REC-071", "SEC-072", "SEC-073",
    "XF-0721", "XF-0731", "SEN-0722", "SEN-0732",
]


@pytest.fixture()
def client(fed):
    api.override_services(fed)
    return TestClient(api.app)


def _prepare(fed):
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
        T0 + timedelta(minutes=47), note="Section restored and inspected.",
    )
    return order


def _post_all_signals(client):
    for device_id in DEVICES:
        resp = client.post(
            f"/incidents/{FLOOD}/restoration/signals",
            json={
                "source": "meter", "recorded_by": "disp.ray",
                "role": "dispatcher", "state": "restored",
                "observed_at": SIGNAL_AT, "device_id": device_id,
            },
        )
        assert resp.status_code == 200
    resp = client.post(
        f"/incidents/{FLOOD}/restoration/signals",
        json={
            "source": "topology", "recorded_by": "disp.ray",
            "role": "dispatcher", "state": "supplied",
            "observed_at": SIGNAL_AT, "feeder_id": "FDR-07",
        },
    )
    assert resp.status_code == 200


def test_api_round7_flow(client, fed):
    order = _prepare(fed)
    bad = client.post(
        f"/incidents/{FLOOD}/restoration/signals",
        json={
            "source": "meter", "recorded_by": "disp.ray",
            "role": "dispatcher", "state": "supplied",
            "observed_at": SIGNAL_AT, "device_id": "BRK-070",
        },
    )
    assert bad.status_code == 422
    assert client.get("/incidents/INC-NOPE/restoration/signals").status_code == 404

    _post_all_signals(client)
    signals = client.get(f"/incidents/{FLOOD}/restoration/signals").json()
    assert len(signals["signals"]) == 9

    # The gate refuses while the approved order is unfinished.
    confirmation = client.post(
        f"/incidents/{FLOOD}/restoration/confirm", json={"as_of": RESTORED_AT}
    )
    assert confirmation.status_code == 200
    assert confirmation.json()["status"] == "complete"
    refused = client.post(
        f"/incidents/{FLOOD}/close-gated",
        json={
            "closed_by": "op.dana", "role": "operator",
            "restored_at": RESTORED_AT,
            "resolution_summary": "Feeder 7 restored.",
        },
    )
    assert refused.status_code == 422
    assert any(
        order.order_id in p
        for p in refused.json()["detail"]["problems"]
    )

    completed = client.post(
        f"/work-orders/{order.order_id}/complete",
        json={"completed_by": "crew.lee", "role": "field_crew"},
    )
    assert completed.status_code == 200
    assert completed.json()["order_id"] == order.order_id

    closed = client.post(
        f"/incidents/{FLOOD}/close-gated",
        json={
            "closed_by": "op.dana", "role": "operator",
            "restored_at": RESTORED_AT,
            "resolution_summary": "Feeder 7 restored.",
        },
    )
    assert closed.status_code == 200
    body = closed.json()
    assert body["close_act"]["confirmation_id"] == confirmation.json()["confirmation_id"]
    assert body["outcome"]["signed_error_minutes"] == 20.0

    fetched = client.get(f"/incidents/{FLOOD}/close-act").json()
    assert fetched["close_act"]["act_id"] == body["close_act"]["act_id"]
    conf = client.get(f"/incidents/{FLOOD}/restoration/confirmation").json()
    assert conf["confirmation"]["status"] == "complete"

    log = client.get(f"/incidents/{FLOOD}/event-log").json()
    assert log["verification_problems"] == []
    assert log["chain_valid"] is True
    kinds = [line["kind"] for line in log["lines"]]
    assert "incident_close_act" in kinds and "work_order_completion" in kinds
    text = client.get(f"/incidents/{FLOOD}/event-log", params={"format": "text"}).json()
    assert FLOOD in text["text"]
    assert client.get("/incidents/INC-NOPE/event-log").status_code == 404
    contradictions = client.get(f"/incidents/{FLOOD}/contradictions").json()
    assert contradictions["contradictions"] == []


def test_runtime_round7_actions(fed):
    runtime_app._services = fed
    order = _prepare(fed)
    recorded = runtime_app.handle_invocation(
        {"action": "record_restoration_signal", "incident_id": FLOOD,
         "source": "meter", "recorded_by": "disp.ray", "role": "dispatcher",
         "state": "restored", "observed_at": SIGNAL_AT, "device_id": "BRK-070"}
    )
    assert recorded["status"] == "recorded"
    refused = runtime_app.handle_invocation(
        {"action": "record_restoration_signal", "incident_id": FLOOD,
         "source": "meter", "recorded_by": "disp.ray", "role": "dispatcher",
         "state": "supplied", "observed_at": SIGNAL_AT, "device_id": "BRK-070"}
    )
    assert refused["status"] == "refused"
    missing = runtime_app.handle_invocation(
        {"action": "record_restoration_signal", "incident_id": "INC-NOPE",
         "source": "meter", "recorded_by": "disp.ray", "role": "dispatcher",
         "state": "restored", "device_id": "BRK-070"}
    )
    assert missing["status"] == "no_such_incident"

    # Confirm on one device of eight: partial, and the gated close
    # is refused naming it.
    confirmed = runtime_app.handle_invocation(
        {"action": "confirm_restoration", "incident_id": FLOOD,
         "as_of": RESTORED_AT}
    )
    assert confirmed["confirmation"]["status"] == "partial"
    refused_close = runtime_app.handle_invocation(
        {"action": "close_incident_gated", "incident_id": FLOOD,
         "closed_by": "op.dana", "role": "operator",
         "restored_at": RESTORED_AT, "resolution_summary": "Done."}
    )
    assert refused_close["status"] == "refused"
    assert any("partial" in p for p in refused_close["problems"])

    for device_id in DEVICES[1:]:
        runtime_app.handle_invocation(
            {"action": "record_restoration_signal", "incident_id": FLOOD,
             "source": "meter", "recorded_by": "disp.ray",
             "role": "dispatcher", "state": "restored",
             "observed_at": SIGNAL_AT, "device_id": device_id}
        )
    runtime_app.handle_invocation(
        {"action": "record_restoration_signal", "incident_id": FLOOD,
         "source": "topology", "recorded_by": "disp.ray",
         "role": "dispatcher", "state": "supplied",
         "observed_at": SIGNAL_AT, "feeder_id": "FDR-07"}
    )
    completed = runtime_app.handle_invocation(
        {"action": "complete_work_order", "order_id": order.order_id,
         "completed_by": "crew.lee", "role": "field_crew"}
    )
    assert completed["status"] == "completed"
    runtime_app.handle_invocation(
        {"action": "confirm_restoration", "incident_id": FLOOD,
         "as_of": RESTORED_AT}
    )
    closed = runtime_app.handle_invocation(
        {"action": "close_incident_gated", "incident_id": FLOOD,
         "closed_by": "op.dana", "role": "operator",
         "restored_at": RESTORED_AT, "resolution_summary": "Done."}
    )
    assert closed["status"] == "closed"
    assert closed["outcome"]["signed_error_minutes"] == 20.0

    fetched = runtime_app.handle_invocation(
        {"action": "get_restoration_confirmation", "incident_id": FLOOD}
    )
    assert fetched["confirmation"]["status"] == "complete"
    assert len(fetched["signals"]) == 9
    log = runtime_app.handle_invocation(
        {"action": "get_incident_event_log", "incident_id": FLOOD,
         "format": "text"}
    )
    assert log["status"] == "ok" and log["verification_problems"] == []
    assert FLOOD in log["text"]
    contra = runtime_app.handle_invocation(
        {"action": "get_incident_contradictions", "incident_id": FLOOD}
    )
    assert contra["contradictions"] == []


def test_cli_round7_flow(fed, monkeypatch, capsys):
    monkeypatch.setenv("SQLITE_PATH", fed.settings.sqlite_path)
    monkeypatch.setenv("OBJECT_STORE_DIR", fed.settings.object_store_dir)
    _prepare(fed)
    for device_id in DEVICES:
        assert cli.main(
            ["restoration-signal", FLOOD, "--source", "meter",
             "--state", "restored", "--by", "disp.ray", "--role",
             "dispatcher", "--device", device_id, "--observed-at", SIGNAL_AT]
        ) == 0
    capsys.readouterr()
    assert cli.main(
        ["restoration-signal", FLOOD, "--source", "topology",
         "--state", "supplied", "--by", "disp.ray", "--role",
         "dispatcher", "--feeder", "FDR-07", "--observed-at", SIGNAL_AT]
    ) == 0
    capsys.readouterr()
    assert cli.main(["confirm-restoration", FLOOD, "--as-of", RESTORED_AT]) == 0
    confirmation = json.loads(capsys.readouterr().out)
    assert confirmation["status"] == "complete"

    order = fed.work_orders.head(FLOOD)
    # Refused first: the approved order has no completion yet.
    assert cli.main(
        ["close-gated", FLOOD, "--by", "op.dana", "--role", "operator",
         "--restored-at", RESTORED_AT, "--summary", "Feeder 7 restored."]
    ) == 1
    assert "refused" in capsys.readouterr().out
    assert cli.main(
        ["complete-work-order", order.order_id, "--by", "crew.lee",
         "--role", "field_crew"]
    ) == 0
    capsys.readouterr()
    assert cli.main(
        ["close-gated", FLOOD, "--by", "op.dana", "--role", "operator",
         "--restored-at", RESTORED_AT, "--summary", "Feeder 7 restored."]
    ) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["outcome"]["signed_error_minutes"] == 20.0
    assert body["close_act"]["confirmation_id"] == confirmation["confirmation_id"]
    assert cli.main(["event-log", FLOOD, "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert "incident_close_act" in text and FLOOD in text
    assert cli.main(["close-act", FLOOD]) == 0
    act = json.loads(capsys.readouterr().out)
    assert act["act_id"] == body["close_act"]["act_id"]
