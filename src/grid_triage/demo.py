"""Demo: the planted streams end to end, offline.

Flood in → one incident out. Nuisance stream → no incident, every
alarm still visible. Lone trip → its own incident. Then the agent
assembles the flood incident's brief, every statement cited —
including the prior incidents retrieved from memory — the
evidence pack is sealed and verified, the ETR is computed with
its basis on the record, and the flood is closed so the accuracy
ledger gains its first entry.
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

    now = datetime.now(timezone.utc)
    loaded = svc.memory.load_history()
    print(
        f"\nMemory: {len(loaded)} closed historical incidents loaded "
        "into the memory port (local double)."
    )
    classification = svc.cause.classify(flood.incident_id, now)
    print(
        f"Root-cause classification: {classification.cause_class} "
        f"(rule: {classification.rule_id}, pack {classification.pack_version})."
    )
    estimate = svc.etr.estimate(flood.incident_id, T0 + timedelta(minutes=30))
    components = {c["name"]: c["minutes"] for c in estimate.components}
    print(
        f"ETR at T+30min (rate pack {estimate.rate_pack_version}, roster "
        f"{estimate.roster_version}): restoration {estimate.estimated_restored_at} "
        f"(basis: {estimate.basis}) — crew wait {components['crew_wait']} min "
        f"({estimate.inputs['crew_id']}), travel {components['travel_access']} min, "
        f"repair {components['base_repair']} min."
    )
    hits = svc.memory.retrieve_for_incident(flood.incident_id, now)
    for hit in hits:
        print(
            f"  prior {hit.record.incident_id} "
            f"({hit.record.cause_class}, closed "
            f"{hit.record.closed_at.date()}): score {hit.score}"
        )

    brief = svc.assembler.assemble(flood.incident_id, now)
    print(f"\nBrief {brief.brief_id} (profile: {brief.model_profile}):")
    for statement in brief.statements:
        cites = ", ".join(
            f"{c.evidence_item_id}:{c.field}" for c in statement.citations
        )
        print(f"  - {statement.text}  [{cites}]")

    pack = svc.packs.seal(flood.incident_id, "op.dana", now)
    problems = svc.packs.verify_pack(pack.pack_id)
    print(
        f"\nEvidence pack {pack.pack_id} sealed by op.dana: "
        f"{len(pack.members)} members, manifest {pack.manifest_hash[:16]}…, "
        f"verify problems: {problems}."
    )
    print(f"Evidence chain valid: {svc.chain.verify_chain(flood.incident_id)}")

    restored_at = T0 + timedelta(hours=4, minutes=30)  # 06:43 UTC
    closure, outcome, _record = svc.closure.close_incident(
        flood.incident_id,
        "op.dana",
        "operator",
        restored_at,
        "Vegetation crew cleared the tree contact at REC-071; "
        "Feeder 7 restored after inspection.",
        now,
    )
    print(
        f"\nClosure {closure.closure_id} recorded by {closure.closed_by}: "
        f"restored {restored_at} — actual duration "
        f"{outcome.actual_duration_minutes} min against the estimated "
        f"{outcome.estimated_duration_minutes} min (signed error "
        f"{outcome.signed_error_minutes:+} min)."
    )
    ledger = svc.closure.accuracy_ledger()
    print(f"ETR accuracy ledger: {ledger['by_cause_class']}")
    print(f"Evidence chain still valid: {svc.chain.verify_chain(flood.incident_id)}")
    return svc


def main() -> int:
    run_demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
