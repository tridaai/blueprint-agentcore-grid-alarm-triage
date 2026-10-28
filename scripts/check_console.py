"""Console correctness check (R9).

Builds a planted record — the reference feed's flood and lone
trip worked through severity, ETR and the work-order gate, a
declared storm with its own burst incident, restoration signals
on the lone incident, and a shift handover assembled and
accepted — then serves the console through the real FastAPI app
and grades the operator-facing claims:

- every figure in /console/data re-performs from the artefacts
  of record, recomputed here from raw store reads (never via
  the board service under test): no figure without an artefact;
- the console's own JavaScript, executed under Node against the
  API's JSON, renders the same figures;
- the static console file carries no control or send surface
  (Law 1): no form, no POST, one read fetch — and its drafts
  are labelled as drafts.

Run: uv run python scripts/check_console.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from grid_triage.config import Settings  # noqa: E402
from grid_triage.models import Alarm  # noqa: E402
from grid_triage.seed import FLOOD_ALARM_IDS, T0  # noqa: E402
from grid_triage.wiring import Services  # noqa: E402


def build_service(tmp: Path) -> tuple[Services, dict]:
    svc = Services(
        Settings(
            sqlite_path=str(tmp / "grid.db"),
            object_store_dir=str(tmp / "objects"),
        )
    )
    svc.intake.ingest_feed(svc.feed.fetch_alarms())
    created = svc.correlator.correlate_new(svc.topology)
    flood = next(i for i in created if i.anchor_alarm_id == FLOOD_ALARM_IDS[0])
    lone = next(i for i in created if i.incident_id != flood.incident_id)

    # The flood worked through the governed path: severity
    # (SEV-1, acknowledgement pending), classification, ETR, and
    # a work order assigned and approved — notifications drafted.
    svc.severity.assess(flood.incident_id, T0 + timedelta(minutes=30))
    svc.cause.classify(flood.incident_id, T0 + timedelta(minutes=31))
    svc.etr.estimate(flood.incident_id, T0 + timedelta(minutes=30))
    order = svc.work_orders.draft(flood.incident_id, T0 + timedelta(minutes=35))
    svc.work_orders.assign(
        order.order_id,
        "CREW-NORTH-VEG",
        "op.dana",
        T0 + timedelta(minutes=90),
        T0 + timedelta(minutes=270),
        T0 + timedelta(minutes=41),
    )
    svc.work_orders.decide(
        order.order_id, "approved", "op.dana", "operator",
        T0 + timedelta(minutes=43),
    )

    # Restoration under way on the lone incident: two meter
    # readings, no confirmation yet.
    anchor = svc.store.get_alarm(lone.anchor_alarm_id)
    for minutes in (50, 55):
        svc.restoration.record_signal(
            lone.incident_id, "meter", "disp.ray", "dispatcher",
            T0 + timedelta(minutes=minutes), "restored",
            device_id=anchor.device_id,
        )

    # A declared storm, live now: the burst correlates under it,
    # is assessed and estimated under it, and its work order
    # stands in draft — a decision pending.
    now = datetime.now(timezone.utc)
    declared_at = now - timedelta(hours=3)
    burst_t0 = now - timedelta(hours=2)
    declaration = svc.storm.declare(
        "op.dana", "operator",
        "Storm cell crossing the Alder service area",
        declared_at, feeders=["FDR-07"],
    )
    burst = [
        Alarm(alarm_id="ALM-CHK-001", stream_id="STREAM-SCADA-NORTH",
              device_id="BRK-070", feeder_id="FDR-07", kind="breaker_trip",
              severity="critical", raised_at=burst_t0,
              message="Feeder 7 breaker tripped on protection"),
        Alarm(alarm_id="ALM-CHK-002", stream_id="STREAM-SCADA-NORTH",
              device_id="BRK-070", feeder_id="FDR-07", kind="relay_protection",
              severity="critical", raised_at=burst_t0 + timedelta(seconds=20),
              message="Distance relay operated, zone 1"),
        Alarm(alarm_id="ALM-CHK-003", stream_id="STREAM-SCADA-NORTH",
              device_id="REC-071", feeder_id="FDR-07", kind="recloser_lockout",
              severity="critical", raised_at=burst_t0 + timedelta(seconds=40),
              message="Recloser R1 locked out after unsuccessful reclose attempts"),
        Alarm(alarm_id="ALM-CHK-004", stream_id="STREAM-SCADA-NORTH",
              device_id="SEN-0722", feeder_id="FDR-07", kind="meter_ping_failure",
              severity="info", raised_at=burst_t0 + timedelta(minutes=5),
              message="Meter ping failure on SEN-0722 — AMI silent"),
        Alarm(alarm_id="ALM-CHK-005", stream_id="STREAM-SCADA-NORTH",
              device_id="SEC-072", feeder_id="FDR-07", kind="voltage_sag",
              severity="major", raised_at=burst_t0 + timedelta(minutes=12),
              message="Voltage sag reported by SEC-072"),
        Alarm(alarm_id="ALM-CHK-006", stream_id="STREAM-SCADA-NORTH",
              device_id="SEC-073", feeder_id="FDR-07", kind="phase_loss",
              severity="major", raised_at=burst_t0 + timedelta(minutes=20),
              message="Phase loss reported by SEC-073"),
    ]
    svc.intake.ingest(burst)
    storm_created = svc.correlator.correlate_new(svc.topology)
    storm_incident = next(
        i for i in storm_created if i.anchor_alarm_id == "ALM-CHK-001"
    )
    assert storm_incident.operating_mode == "storm", storm_incident
    svc.cause.classify(storm_incident.incident_id, burst_t0 + timedelta(minutes=25))
    svc.severity.assess(storm_incident.incident_id, burst_t0 + timedelta(minutes=90))
    svc.etr.estimate(storm_incident.incident_id, burst_t0 + timedelta(minutes=30))
    storm_order = svc.work_orders.draft(
        storm_incident.incident_id, burst_t0 + timedelta(minutes=35)
    )

    # The shift handover over this record, accepted by the
    # incoming operator.
    handover = svc.handover.assemble("op.dana", "operator", now - timedelta(minutes=15))
    svc.handover.accept(
        handover.handover_id, "op.reyes", "operator", now - timedelta(minutes=14)
    )
    return svc, {
        "flood": flood,
        "lone": lone,
        "storm_incident": storm_incident,
        "storm_order": storm_order,
        "declaration": declaration,
        "handover": handover,
    }


# -- independent re-performance, from raw store reads only -------


def _live_open(svc) -> list:
    store = svc.store
    superseded = {
        r.prior_incident_id for r in store.list_incident_revisions()
    } | {s.incident_id for s in store.list_incident_splits()}

    def closed(incident_id: str) -> bool:
        return (
            store.get_incident_closure(incident_id) is not None
            or store.get_incident_close_act(incident_id) is not None
        )

    return [
        i
        for i in store.list_incidents()
        if i.incident_id not in superseded and not closed(i.incident_id)
    ]


def expected_incidents(svc) -> list[dict]:
    store = svc.store
    rows = []
    for incident in _live_open(svc):
        history = store.list_severity_assessments(incident.incident_id)
        current = history[-1] if history else None
        classification = store.get_cause_classification(incident.incident_id)
        rows.append(
            {
                "incident_id": incident.incident_id,
                "feeder_id": incident.feeder_id,
                "substation_id": incident.substation_id,
                "created_at": incident.created_at.isoformat(),
                "anchor_alarm_id": incident.anchor_alarm_id,
                "member_alarm_count": len(
                    store.list_memberships(incident.incident_id)
                ),
                "topology_version": incident.topology_version,
                "band": current.band if current else "UNASSESSED",
                "score": current.score if current else None,
                "assessment_id": current.assessment_id if current else None,
                "cause_class": (
                    classification.cause_class if classification else None
                ),
                "operating_mode": incident.operating_mode,
                "storm_pack_version": incident.storm_pack_version,
                "storm_declaration_id": incident.storm_declaration_id,
            }
        )
    return rows


def expected_etr(svc) -> list[dict]:
    store = svc.store
    rows = []
    for incident in _live_open(svc):
        history = store.list_etr_estimates(incident.incident_id)
        current = history[-1] if history else None
        rows.append(
            {
                "incident_id": incident.incident_id,
                "feeder_id": incident.feeder_id,
                "estimate_id": current.estimate_id if current else None,
                "status": current.status if current else "no_estimate",
                "basis": current.basis if current else None,
                "total_minutes": current.total_minutes if current else None,
                "estimated_restored_at": (
                    current.estimated_restored_at.isoformat()
                    if current and current.estimated_restored_at
                    else None
                ),
                "operating_mode": (
                    current.operating_mode if current else incident.operating_mode
                ),
                "missing_inputs": (
                    list(current.missing_inputs) if current else []
                ),
            }
        )
    return rows


def expected_work_orders(svc) -> tuple[list[dict], dict]:
    """(open queue rows, pending approvals) from decisions,
    assignments and completions read straight off the store."""
    store = svc.store
    all_rows = []
    for incident in store.list_incidents():
        orders = store.list_work_orders(incident.incident_id)
        if not orders:
            continue
        head = orders[-1]
        decisions = store.list_work_order_decisions(head.order_id)
        decision = decisions[-1] if decisions else None
        assignments = store.list_work_order_assignments(head.order_id)
        assignment = assignments[-1] if assignments else None
        all_rows.append(
            {
                "order_id": head.order_id,
                "incident_id": head.incident_id,
                "version": head.version,
                "state": decision.decision if decision else "draft",
                "drafted_by": head.drafted_by,
                "cause_class": head.cause_class,
                "assigned_crew_id": assignment.crew_id if assignment else None,
                "completed": bool(
                    store.list_work_order_completions(head.order_id)
                ),
                "notifications_drafted": (
                    len(decision.notification_ids) if decision else 0
                ),
            }
        )
    all_rows.sort(key=lambda r: r["order_id"])
    open_rows = [
        r for r in all_rows if r["state"] in ("draft", "approved") and not r["completed"]
    ]
    acked = {a.assessment_id for a in store.list_severity_acknowledgements()}
    acks = []
    for incident in store.list_incidents():
        for assessment in store.list_severity_assessments(incident.incident_id):
            if assessment.escalation_required and assessment.assessment_id not in acked:
                acks.append(
                    {
                        "assessment_id": assessment.assessment_id,
                        "incident_id": incident.incident_id,
                        "band": assessment.band,
                        "score": assessment.score,
                    }
                )
    decisions_pending = [
        {
            "order_id": r["order_id"],
            "incident_id": r["incident_id"],
            "version": r["version"],
            "drafted_by": r["drafted_by"],
            "assigned_crew_id": r["assigned_crew_id"],
        }
        for r in all_rows
        if r["state"] == "draft"
    ]
    return open_rows, {
        "severity_acknowledgements": acks,
        "work_order_decisions": decisions_pending,
    }


def expected_restorations(svc) -> list[dict]:
    store = svc.store
    rows = []
    for incident in _live_open(svc):
        signals = store.list_restoration_signals(incident.incident_id)
        confirmations = store.list_restoration_confirmations(incident.incident_id)
        latest = confirmations[-1] if confirmations else None
        if not signals and latest is None:
            continue
        if latest is not None and latest.status == "complete":
            continue
        rows.append(
            {
                "incident_id": incident.incident_id,
                "confirmation_status": latest.status if latest else "unconfirmed",
                "confirmation_id": latest.confirmation_id if latest else None,
                "signal_count": len(signals),
                "restored_customers": latest.restored_customers if latest else None,
                "outstanding_customers": (
                    latest.outstanding_customers if latest else None
                ),
                "missing_classes": list(latest.missing_classes) if latest else [],
            }
        )
    return rows


def _node_render(script_text: str, data: dict, tmp: Path) -> dict:
    script_path = tmp / "console-script.js"
    script_path.write_text(script_text)
    data_path = tmp / "console-data.json"
    data_path.write_text(json.dumps(data))
    runner = tmp / "run-console-check.js"
    runner.write_text(
        """
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
eval(code);
const data = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const api = globalThis.GridConsole;
console.log(JSON.stringify({
  model: api.buildBoardModel(data),
  bannerHtml: api.renderStormBanner(data),
  bandHtml: api.renderBandGrid(data),
  incidentsHtml: api.renderIncidents(data),
  etrHtml: api.renderEtr(data),
  workOrdersHtml: api.renderWorkOrders(data),
  handoverHtml: api.renderHandover(data)
}));
""".strip()
    )
    result = subprocess.run(
        ["node", str(runner), str(script_path), str(data_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main() -> int:
    from fastapi.testclient import TestClient

    import grid_triage.api as api

    with tempfile.TemporaryDirectory(prefix="grid-console-check-") as tmp_name:
        tmp = Path(tmp_name)
        svc, planted = build_service(tmp)
        api.override_services(svc)
        client = TestClient(api.app)

        # -- the page itself: branding, footer, Law 1 surface ----
        page = client.get("/console")
        assert page.status_code == 200, page.status_code
        html = page.text
        assert "Visit Trida AI →" in html and "View all blueprints →" in html
        assert "Grid Operations Console" in html
        # Law 1, mechanically: no form, no POST, exactly one
        # fetch and it reads /console/data; no control verb
        # anywhere in the file.
        assert "<form" not in html, "console carries a form"
        assert "POST" not in html, "console carries a POST"
        assert html.count("fetch(") == 1 and 'fetch("/console/data")' in html
        for verb in (
            "reclos", "setpoint", "energize", "de-energize",
            "load shedding", "trip_breaker", "open_breaker",
            "close_breaker",
        ):
            assert verb not in html.lower(), f"control verb {verb!r} in console"
        # Drafts are labelled as drafts, in the page's own text.
        assert "drafts until a named operator approves them" in html
        assert "never sent" in html
        script_match = re.search(r"<script>(.*?)</script>", html, flags=re.S)
        assert script_match, "console script missing"
        script_text = script_match.group(1)

        data = client.get("/console/data").json()

        # -- every figure re-performs from the artefacts ---------
        exp_incidents = expected_incidents(svc)
        assert data["incidents"] == exp_incidents, (
            data["incidents"], exp_incidents
        )
        by_band: dict[str, int] = {}
        for row in exp_incidents:
            by_band[row["band"]] = by_band.get(row["band"], 0) + 1
        assert data["incident_counts_by_band"] == dict(sorted(by_band.items()))
        assert data["etr"] == expected_etr(svc)
        exp_orders, exp_pending = expected_work_orders(svc)
        assert data["work_orders"] == exp_orders
        assert data["pending_approvals"] == exp_pending
        assert data["pending_approval_count"] == (
            len(exp_pending["severity_acknowledgements"])
            + len(exp_pending["work_order_decisions"])
        )
        assert data["unconfirmed_restorations"] == expected_restorations(svc)

        # Feeders: counts recomputed from the payload + store.
        payload = svc.store.get_topology_payload("v1")
        open_ids = {i.incident_id for i in _live_open(svc)}
        for feeder_row in data["feeders"]:
            fid = feeder_row["feeder_id"]
            assert feeder_row["device_count"] == sum(
                1 for d in payload["devices"] if d["feeder_id"] == fid
            )
            assert feeder_row["open_incident_count"] == sum(
                1
                for i in _live_open(svc)
                if i.feeder_id == fid and i.incident_id in open_ids
            )
            assert feeder_row["uncorrelated_alarm_count"] == sum(
                1
                for a in svc.store.list_uncorrelated_alarms()
                if a.feeder_id == fid
            )
        feeder7 = next(r for r in data["feeders"] if r["feeder_id"] == "FDR-07")
        assert feeder7["operating_mode"] == "storm"
        assert feeder7["storm_declaration_id"] == (
            planted["declaration"].declaration_id
        )

        # The storm banner names the declaring act.
        assert data["storm"]["active"] is True
        windows = data["storm"]["windows"]
        assert [w["declaration_id"] for w in windows] == [
            planted["declaration"].declaration_id
        ]
        assert windows[0]["declared_by"] == "op.dana"
        assert windows[0]["feeders"] == ["FDR-07"]

        # Accuracy: counts recomputed from the outcomes table.
        outcomes = svc.store.list_etr_outcomes()
        ledger = data["etr_accuracy"]
        assert ledger["overall"]["compared_count"] == sum(
            1 for o in outcomes if o.status == "compared"
        )
        assert ledger["unestimated_closures"] == sum(
            1 for o in outcomes if o.status != "compared"
        )

        # The handover on the board is the artefact of record,
        # accepted, and not stale — and its sections are the
        # independently recomputed ones.
        handover_view = data["handover"]
        stored = svc.handover.get(planted["handover"].handover_id)
        assert handover_view["handover_id"] == stored.handover_id
        assert handover_view["sections"] == stored.sections
        assert handover_view["sections"]["open_incidents"] == exp_incidents
        assert handover_view["stale"] is False and handover_view["drift"] == []
        assert handover_view["acceptance"]["accepted_by"] == "op.reyes"
        assert handover_view["acceptance"]["record_hash"] == stored.record_hash
        verified = client.get(f"/handovers/{stored.handover_id}/verify")
        assert verified.json() == {
            "handover_id": stored.handover_id,
            "problems": [],
        }

        # -- the page's own JS renders the same figures ----------
        rendered = _node_render(script_text, data, tmp)
        model = rendered["model"]
        assert model["openIncidentCount"] == len(exp_incidents)
        assert model["bandCounts"] == data["incident_counts_by_band"]
        assert model["stormActive"] is True
        assert model["stormDeclarationIds"] == [
            planted["declaration"].declaration_id
        ]
        assert model["etrRows"] == [
            {
                "incident_id": r["incident_id"],
                "status": r["status"],
                "basis": r["basis"],
                "total_minutes": r["total_minutes"],
                "estimated_restored_at": r["estimated_restored_at"],
            }
            for r in data["etr"]
        ]
        assert model["workOrderStates"] == {
            r["order_id"]: r["state"] for r in exp_orders
        }
        assert model["pendingApprovalCount"] == data["pending_approval_count"]
        assert model["feederOpenCounts"] == {
            r["feeder_id"]: r["open_incident_count"] for r in data["feeders"]
        }
        assert model["unconfirmedRestorationCount"] == len(
            data["unconfirmed_restorations"]
        )
        assert planted["declaration"].declaration_id in rendered["bannerHtml"]
        assert "STORM MODE" in rendered["bannerHtml"]
        assert planted["flood"].incident_id in rendered["incidentsHtml"]
        flood_etr = next(
            r for r in data["etr"]
            if r["incident_id"] == planted["flood"].incident_id
        )
        assert flood_etr["basis"] == "computed_from_rates"
        assert flood_etr["estimated_restored_at"] in rendered["etrHtml"]
        assert "DRAFT" in rendered["workOrdersHtml"]
        assert planted["storm_order"].order_id in rendered["workOrdersHtml"]
        assert "op.reyes" in rendered["handoverHtml"]

        print("CONSOLE CHECK: PASS")
        print(
            f"open incidents: {len(exp_incidents)} "
            f"({data['incident_counts_by_band']}); "
            f"pending approvals: {data['pending_approval_count']}; "
            f"open work orders: {len(exp_orders)}; "
            f"unconfirmed restorations: "
            f"{len(data['unconfirmed_restorations'])}"
        )
        print(
            f"storm banner names declaration "
            f"{planted['declaration'].declaration_id}; handover "
            f"{stored.handover_id} accepted by op.reyes, not stale"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
