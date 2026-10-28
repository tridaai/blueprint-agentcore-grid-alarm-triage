"""Demo: the planted streams end to end, offline.

Flood in → one incident out. Nuisance stream → no incident, every
alarm still visible. Lone trip → its own incident. Then the agent
assembles the flood incident's brief, every statement cited.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from grid_triage.config import Settings
from grid_triage.seed import FLOOD_ALARM_IDS, NUISANCE_ALARM_IDS
from grid_triage.wiring import Services


def run_demo(settings: Settings | None = None) -> Services:
    if settings is None:
        tmp = Path(tempfile.mkdtemp(prefix="grid-demo-"))
        settings = Settings(
            sqlite_path=str(tmp / "grid.db"), object_store_dir=str(tmp / "objects")
        )
    svc = Services(settings)
    count = svc.intake.ingest_feed(svc.feed.fetch_alarms())
    created = svc.correlator.correlate_new(svc.topology)

    print(f"Ingested {count} alarms (append-only).")
    print(f"Incidents created: {len(created)}")
    for incident in created:
        members = svc.store.list_memberships(incident.incident_id)
        print(
            f"  {incident.incident_id} on {incident.feeder_id}: "
            f"{len(members)} alarms, anchor {incident.anchor_alarm_id}, "
            f"topology {incident.topology_version}"
        )
    uncorrelated = svc.store.list_uncorrelated_alarms()
    print(
        f"Uncorrelated alarms (visible, never dropped): {len(uncorrelated)} "
        f"— the {len(NUISANCE_ALARM_IDS)}-alarm nuisance stream among them."
    )
    flood = next(i for i in created if i.anchor_alarm_id == FLOOD_ALARM_IDS[0])
    members = {m.alarm_id for m in svc.store.list_memberships(flood.incident_id)}
    assert members == set(FLOOD_ALARM_IDS), "flood membership must be exact"
    print(f"Flood membership exact: {len(members)}/{len(FLOOD_ALARM_IDS)} alarms.")

    from datetime import timedelta

    from grid_triage.seed import T0

    assessment = svc.severity.assess(flood.incident_id, T0 + timedelta(minutes=30))
    print(
        f"Severity at T+30min (formula pack {assessment.pack_version}): "
        f"{assessment.band}, score {assessment.score} — "
        f"{assessment.inputs['customers_affected']} customers affected, "
        f"critical loads: "
        f"{[c['name'] for c in assessment.inputs['critical_loads']]}; "
        f"operator acknowledgement required: {assessment.escalation_required}."
    )

    from datetime import datetime, timezone

    brief = svc.assembler.assemble(flood.incident_id, datetime.now(timezone.utc))
    print(f"\nBrief {brief.brief_id} (profile: {brief.model_profile}):")
    for statement in brief.statements:
        cites = ", ".join(
            f"{c.evidence_item_id}:{c.field}" for c in statement.citations
        )
        print(f"  - {statement.text}  [{cites}]")
    print(f"\nEvidence chain valid: {svc.chain.verify_chain(flood.incident_id)}")
    return svc


def main() -> int:
    run_demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
