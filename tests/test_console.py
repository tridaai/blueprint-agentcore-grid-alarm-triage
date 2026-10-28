"""Round 9: the operator console — the board behind it, and
the page's Law 1 surface.

The board grades here at the service level (the full
figure-by-figure re-performance against a planted record is
scripts/check_console.py); the page itself is graded for what
it must never carry: a form, a POST, a control verb, a send
surface.
"""

import re
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import grid_triage.api as api
from grid_triage.seed import FLOOD_ALARM_IDS, T0

CONSOLE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "grid_triage"
    / "static"
    / "console.html"
)


def test_board_rows_come_from_the_artefacts(fed):
    services = fed
    flood = next(
        i
        for i in services.store.list_incidents()
        if i.anchor_alarm_id == FLOOD_ALARM_IDS[0]
    )
    board = services.board.board(T0 + timedelta(minutes=10))
    assert {r["incident_id"] for r in board["incidents"]} == {
        i.incident_id for i in services.store.list_incidents()
    }
    flood_row = next(
        r for r in board["incidents"] if r["incident_id"] == flood.incident_id
    )
    assert flood_row["band"] == "UNASSESSED"
    assert flood_row["member_alarm_count"] == 40
    assert flood_row["operating_mode"] == "normal"
    assert board["incident_counts_by_band"] == {"UNASSESSED": 2}

    services.severity.assess(flood.incident_id, T0 + timedelta(minutes=30))
    board = services.board.board(T0 + timedelta(minutes=31))
    assert board["incident_counts_by_band"] == {"SEV-1": 1, "UNASSESSED": 1}
    assert board["pending_approval_count"] == 1

    estimate = services.etr.estimate(flood.incident_id, T0 + timedelta(minutes=30))
    board = services.board.board(T0 + timedelta(minutes=32))
    etr_row = next(
        r for r in board["etr"] if r["incident_id"] == flood.incident_id
    )
    assert etr_row["status"] == "computed"
    assert etr_row["basis"] == estimate.basis == "computed_from_rates"
    assert etr_row["estimated_restored_at"] == (
        estimate.estimated_restored_at.isoformat()
    )
    lone_row = next(r for r in board["etr"] if r["incident_id"] != flood.incident_id)
    assert lone_row["status"] == "no_estimate"

    feeders = {r["feeder_id"]: r for r in board["feeders"]}
    payload = services.store.get_topology_payload("v1")
    assert feeders["FDR-07"]["device_count"] == sum(
        1 for d in payload["devices"] if d["feeder_id"] == "FDR-07"
    )
    assert feeders["FDR-07"]["open_incident_count"] == 1
    assert feeders["FDR-07"]["uncorrelated_alarm_count"] == 0
    # The nuisance stream stays visible on its own feeder —
    # uncorrelated, never hidden.
    assert feeders["FDR-12"]["uncorrelated_alarm_count"] == 8
    assert feeders["FDR-03"]["open_incident_count"] == 1
    assert board["storm"]["active"] is False


def test_board_and_handover_sections_agree(fed):
    services = fed
    at = T0 + timedelta(hours=2)
    handover = services.handover.assemble("op.dana", "operator", at)
    board = services.board.board(at)
    for key in (
        "open_incidents",
        "pending_approvals",
        "open_work_orders",
        "unconfirmed_restorations",
        "storm",
        "etr_accuracy",
    ):
        section = {
            "open_incidents": "incidents",
            "pending_approvals": "pending_approvals",
            "open_work_orders": "work_orders",
            "unconfirmed_restorations": "unconfirmed_restorations",
            "storm": "storm",
            "etr_accuracy": "etr_accuracy",
        }[key]
        assert handover.sections[key] == board[section], key


def test_console_page_is_served_and_read_only(services):
    api.override_services(services)
    client = TestClient(api.app)
    page = client.get("/console")
    assert page.status_code == 200
    html = page.text
    assert "Visit Trida AI →" in html
    assert "View all blueprints →" in html
    assert "Grid Operations Console" in html
    data = client.get("/console/data")
    assert data.status_code == 200
    body = data.json()
    assert body["incidents"] == []
    assert body["handover"] is None


def test_console_static_file_law1_surface():
    html = CONSOLE.read_text(encoding="utf-8")
    assert "<form" not in html
    assert "POST" not in html
    assert html.count("fetch(") == 1
    assert 'fetch("/console/data")' in html
    for verb in (
        "reclos",
        "setpoint",
        "energize",
        "de-energize",
        "load shedding",
        "trip_breaker",
        "open_breaker",
        "close_breaker",
    ):
        assert verb not in html.lower(), verb
    # Drafts labelled as drafts, and the footer carrying the
    # portfolio links.
    assert "drafts until a named operator approves them" in html
    assert "never sent" in html
    assert "https://trida.ai/" in html
    assert re.search(r"View all blueprints →", html)
