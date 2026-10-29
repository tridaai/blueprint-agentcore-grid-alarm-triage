"""Oversight report check (R12).

Builds a planted record — the reference feed worked through
severity and one closure, a shift handover assembled and
accepted — then grades the oversight reporting claims:

- a report over the planted day verifies clean through the
  service's independent re-performance path, and its headline
  figures re-perform here from raw store reads (never via the
  service under test);
- no figure in any section names an incident: the report is
  aggregate by construction;
- an empty window's rates read no-data — value None, never
  0% or 100%;
- a forged report whose fingerprint honestly covers its own
  (wrong) sections is still caught: the verifier names the
  section that does not recompute;
- a report whose stored fingerprint does not cover its stored
  sections is named as such.

Run: uv run python scripts/check_reports.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from grid_triage.config import Settings  # noqa: E402
from grid_triage.models import OversightReport  # noqa: E402
from grid_triage.oversight import fingerprint_of  # noqa: E402
from grid_triage.seed import FLOOD_ALARM_IDS, T0  # noqa: E402
from grid_triage.wiring import Services  # noqa: E402

DAY_START = datetime(2026, 10, 14, 0, 0, 0, tzinfo=timezone.utc)
DAY_END = datetime(2026, 10, 15, 0, 0, 0, tzinfo=timezone.utc)
GENERATED_AT = datetime(2026, 10, 16, 9, 0, 0, tzinfo=timezone.utc)


def build_service(tmp: Path) -> Services:
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
    svc.severity.assess(flood.incident_id, T0 + timedelta(minutes=30))
    svc.severity.assess(lone.incident_id, lone.created_at + timedelta(minutes=5))
    svc.closure.close_incident(
        lone.incident_id,
        "op.dana",
        "operator",
        lone.created_at + timedelta(hours=2),
        "restored — check scenario",
        lone.created_at + timedelta(hours=2, minutes=5),
    )
    handover = svc.handover.assemble(
        "op.dana", "operator", DAY_START + timedelta(hours=20)
    )
    svc.handover.accept(
        handover.handover_id,
        "op.reyes",
        "operator",
        DAY_START + timedelta(hours=20, minutes=5),
    )
    return svc


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        svc = build_service(Path(tmp))
        report = svc.oversight.generate(
            "op.dana", "operator", DAY_START, DAY_END, GENERATED_AT
        )

        # Every stored report verifies through the independent path.
        for stored in svc.oversight.reports():
            problems = svc.oversight.verify_report(stored.report_id)
            if problems:
                failures.append(
                    f"report {stored.report_id} does not verify: {problems}"
                )

        # The headline figures, re-performed from raw store reads.
        incidents = svc.store.list_incidents()
        closures = svc.store.list_incident_closures()
        volumes = report.sections["incident_volumes"]
        if volumes["total"] != len(incidents):
            failures.append(
                f"incident total {volumes['total']} != stored {len(incidents)}"
            )
        if volumes["by_state"]["closed"] != len(closures):
            failures.append("closed count does not re-perform")
        states = volumes["by_state"]
        if sum(states.values()) != volumes["total"]:
            failures.append("state counts do not sum to the total")
        rate = volumes["closure_rate"]
        if rate["numerator"] != len(closures) or rate["denominator"] != len(incidents):
            failures.append("closure rate does not carry its population")

        # Aggregate only: no incident id anywhere in the sections.
        blob = json.dumps(report.sections)
        for incident in incidents:
            if incident.incident_id in blob:
                failures.append(
                    f"report names incident {incident.incident_id}"
                )

        # An empty window: rates read no-data, never 0% or 100%.
        empty = svc.oversight.generate(
            "op.dana",
            "operator",
            datetime(2027, 1, 1, tzinfo=timezone.utc),
            datetime(2027, 1, 2, tzinfo=timezone.utc),
            GENERATED_AT + timedelta(days=90),
        )
        empty_volumes = empty.sections["incident_volumes"]
        if empty_volumes["population"] != "no_data":
            failures.append("empty window does not read no_data")
        if empty_volumes["closure_rate"]["value"] is not None:
            failures.append("empty-window closure rate has a value")
        if svc.oversight.verify_report(empty.report_id):
            failures.append("empty-window report does not verify")

        # A forged report with an honest fingerprint over wrong
        # figures: the re-performance names the drifted section.
        forged_sections = json.loads(json.dumps(report.sections))
        forged_sections["incident_volumes"]["total"] += 1
        forged = OversightReport(
            report_id="REPORT-forged00000001",
            pack_version=report.pack_version,
            generated_by="op.dana",
            role="operator",
            window_start=DAY_START,
            window_end=DAY_END,
            generated_at=GENERATED_AT,
            sections=forged_sections,
            fingerprint=fingerprint_of(
                report.pack_version, DAY_START, DAY_END, forged_sections
            ),
        )
        svc.store.add_oversight_report(forged)
        problems = svc.oversight.verify_report(forged.report_id)
        if not any("incident_volumes" in p for p in problems):
            failures.append(
                f"forged figure not named by the verifier: {problems}"
            )

        # A report whose fingerprint does not cover its sections.
        stale = forged.model_copy(
            update={"report_id": "REPORT-forged00000002", "fingerprint": "0" * 64}
        )
        svc.store.add_oversight_report(stale)
        problems = svc.oversight.verify_report(stale.report_id)
        if not any("fingerprint" in p for p in problems):
            failures.append(
                f"fingerprint drift not named by the verifier: {problems}"
            )

    if failures:
        print("REPORTS CHECK: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("REPORTS CHECK: PASS")
    print(
        "  - reports verify through the independent path; headline"
        " figures re-perform from raw store reads"
    )
    print(
        "  - no section names an incident; empty-window rates read"
        " no-data; forged figures and fingerprint drift are named"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
