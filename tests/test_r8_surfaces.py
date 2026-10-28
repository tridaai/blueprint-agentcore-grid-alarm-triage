"""Round 8 surfaces: storm declarations, the state fold and the
post-storm review at parity across the operations API and the
Runtime invocation handler, plus a CLI pass over the same store.
"""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.cli as cli
import grid_triage.runtime_app as runtime_app

DECLARE_AT = "2026-10-16T01:00:00+00:00"
DURING = "2026-10-16T06:00:00+00:00"
STAND_DOWN_AT = "2026-10-16T12:00:00+00:00"
REVIEW_AT = "2026-10-16T12:05:00+00:00"


@pytest.fixture()
def client(services):
    api.override_services(services)
    return TestClient(api.app)


def _declare(client, **overrides):
    body = {
        "declared_by": "op.dana",
        "role": "operator",
        "reason": "Storm cell crossing the Alder service area",
        "declared_at": DECLARE_AT,
        "feeders": ["FDR-07"],
    }
    body.update(overrides)
    return client.post("/storm/declare", json=body)


def test_api_storm_flow(client):
    refused = _declare(client, role="field_crew")
    assert refused.status_code == 422
    assert "problems" in refused.json()["detail"]

    created = _declare(client)
    assert created.status_code == 200
    declaration = created.json()
    assert declaration["kind"] == "declare"
    declaration_id = declaration["declaration_id"]

    state = client.get("/storm/state", params={"feeder_id": "FDR-07", "at": DURING})
    assert state.status_code == 200
    assert state.json()["mode"] == "storm"
    assert state.json()["declaration_id"] == declaration_id
    other = client.get("/storm/state", params={"feeder_id": "FDR-03", "at": DURING})
    assert other.json()["mode"] == "normal"

    # The review refuses while the window is open.
    early = client.post(
        "/storm/reviews",
        json={
            "declaration_id": declaration_id,
            "computed_by": "op.dana",
            "computed_at": DURING,
        },
    )
    assert early.status_code == 422

    extension = client.post(
        "/storm/extend",
        json={
            "declaration_id": declaration_id,
            "declared_by": "op.dana",
            "role": "operator",
            "reason": "Cell widening — Feeder 3 added",
            "declared_at": "2026-10-16T03:00:00+00:00",
            "feeders": ["FDR-07", "FDR-03"],
        },
    )
    assert extension.status_code == 200
    widened = client.get(
        "/storm/state", params={"feeder_id": "FDR-03", "at": DURING}
    )
    assert widened.json()["mode"] == "storm"

    stand_down = client.post(
        "/storm/stand-down",
        json={
            "declaration_id": declaration_id,
            "declared_by": "op.dana",
            "role": "operator",
            "reason": "Cell has passed",
            "declared_at": STAND_DOWN_AT,
        },
    )
    assert stand_down.status_code == 200
    after = client.get(
        "/storm/state",
        params={"feeder_id": "FDR-07", "at": "2026-10-16T13:00:00+00:00"},
    )
    assert after.json()["mode"] == "normal"

    review_resp = client.post(
        "/storm/reviews",
        json={
            "declaration_id": declaration_id,
            "computed_by": "op.dana",
            "computed_at": REVIEW_AT,
        },
    )
    assert review_resp.status_code == 200
    review = review_resp.json()
    assert review["figures"]["incident_count"] == 0

    fetched = client.get(f"/storm/reviews/{review['review_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["review_id"] == review["review_id"]
    verified = client.get(f"/storm/reviews/{review['review_id']}/verify")
    assert verified.json() == {"review_id": review["review_id"], "problems": []}
    by_declaration = client.get(f"/storm/declarations/{declaration_id}/review")
    assert by_declaration.json()["review"]["review_id"] == review["review_id"]
    missing = client.get("/storm/reviews/STORMREV-missing")
    assert missing.status_code == 404
    listed = client.get("/storm/declarations")
    assert [d["kind"] for d in listed.json()["declarations"]] == [
        "declare",
        "extend",
        "stand_down",
    ]


def test_runtime_storm_actions(services):
    runtime_app._services = services
    declared = runtime_app.handle_invocation(
        {
            "action": "declare_storm",
            "declared_by": "op.dana",
            "role": "operator",
            "reason": "Storm cell crossing the Alder service area",
            "declared_at": DECLARE_AT,
            "feeders": ["FDR-07"],
        }
    )
    assert declared["status"] == "declared"
    declaration_id = declared["declaration"]["declaration_id"]

    refused = runtime_app.handle_invocation(
        {
            "action": "declare_storm",
            "declared_by": "op.dana",
            "role": "field_crew",
            "reason": "trying anyway",
            "declared_at": DECLARE_AT,
            "feeders": ["FDR-12"],
        }
    )
    assert refused["status"] == "refused"

    state = runtime_app.handle_invocation(
        {"action": "get_storm_state", "feeder_id": "FDR-07", "at": DURING}
    )
    assert state["status"] == "ok"
    assert state["mode"] == "storm"
    assert state["declaration_id"] == declaration_id

    early = runtime_app.handle_invocation(
        {
            "action": "compute_storm_review",
            "declaration_id": declaration_id,
            "computed_by": "op.dana",
            "computed_at": DURING,
        }
    )
    assert early["status"] == "refused"

    stood_down = runtime_app.handle_invocation(
        {
            "action": "stand_down_storm",
            "declaration_id": declaration_id,
            "declared_by": "op.dana",
            "role": "operator",
            "reason": "Cell has passed",
            "declared_at": STAND_DOWN_AT,
        }
    )
    assert stood_down["status"] == "stood_down"

    computed = runtime_app.handle_invocation(
        {
            "action": "compute_storm_review",
            "declaration_id": declaration_id,
            "computed_by": "op.dana",
            "computed_at": REVIEW_AT,
        }
    )
    assert computed["status"] == "computed"
    review_id = computed["review"]["review_id"]
    fetched = runtime_app.handle_invocation(
        {"action": "get_storm_review", "review_id": review_id}
    )
    assert fetched["status"] == "ok"
    verified = runtime_app.handle_invocation(
        {"action": "verify_storm_review", "review_id": review_id}
    )
    assert verified == {
        "status": "ok",
        "review_id": review_id,
        "problems": [],
    }
    missing = runtime_app.handle_invocation(
        {"action": "get_storm_review", "review_id": "STORMREV-missing"}
    )
    assert missing["status"] == "no_such_review"
    listed = runtime_app.handle_invocation({"action": "list_storm_declarations"})
    assert [d["kind"] for d in listed["declarations"]] == ["declare", "stand_down"]


def test_cli_storm_flow(services, monkeypatch, capsys):
    monkeypatch.setenv("SQLITE_PATH", services.settings.sqlite_path)
    monkeypatch.setenv("OBJECT_STORE_DIR", services.settings.object_store_dir)
    # A wrong role is refused on the CLI too.
    assert cli.main(
        [
            "storm-declare", "--by", "op.dana", "--role", "field_crew",
            "--reason", "Storm cell crossing", "--feeder", "FDR-07",
            "--declared-at", DECLARE_AT,
        ]
    ) == 1
    assert "refused" in capsys.readouterr().out
    assert cli.main(
        [
            "storm-declare", "--by", "op.dana", "--role", "operator",
            "--reason", "Storm cell crossing the Alder service area",
            "--feeder", "FDR-07", "--declared-at", DECLARE_AT,
        ]
    ) == 0
    declaration = json.loads(capsys.readouterr().out)
    declaration_id = declaration["declaration_id"]
    assert cli.main(["storm-state", "FDR-07", "--at", DURING]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["mode"] == "storm"
    assert state["declaration_id"] == declaration_id
    # The review refuses before stand-down.
    assert cli.main(
        ["storm-review", declaration_id, "--by", "op.dana", "--at", DURING]
    ) == 1
    assert "refused" in capsys.readouterr().out
    assert cli.main(
        [
            "storm-stand-down", declaration_id, "--by", "op.dana",
            "--role", "operator", "--reason", "Cell has passed",
            "--declared-at", STAND_DOWN_AT,
        ]
    ) == 0
    capsys.readouterr()
    assert cli.main(
        ["storm-review", declaration_id, "--by", "op.dana", "--at", REVIEW_AT]
    ) == 0
    review = json.loads(capsys.readouterr().out)
    assert review["declaration_id"] == declaration_id
    assert cli.main(["storm-review-verify", review["review_id"]]) == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict == {"review_id": review["review_id"], "problems": []}
    assert cli.main(["storm-declarations"]) == 0
    acts = json.loads(capsys.readouterr().out)
    assert [a["kind"] for a in acts] == ["declare", "stand_down"]
    # The parsed CLI timestamps are real datetimes in the record.
    assert datetime.fromisoformat(
        acts[0]["declared_at"].replace("Z", "+00:00")
    ) == datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)
