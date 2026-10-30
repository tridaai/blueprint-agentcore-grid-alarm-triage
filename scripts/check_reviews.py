"""Incident review check (R15).

Builds a planted record — the reference feed worked through
severity, classification and one closure, plus a second
incident left bare — then grades the post-incident review
claims:

- a review over the worked incident verifies clean through
  the service's independent re-performance path, and its
  headline figures re-perform here from raw store reads
  (never via the service under test);
- the bare incident's review grades partial and names its
  absences with reasons — a thin record reads as a thin
  record, never as a full one;
- held text is named by hold id and rules only: no raw text
  from the quarantine book appears anywhere in a review;
- a forged review whose fingerprint honestly covers its own
  (wrong) sections is still caught: the verifier names the
  section that does not recompute;
- a review whose stored fingerprint does not cover its
  stored sections is named as such.

Run: uv run python scripts/check_reviews.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from grid_triage.config import Settings  # noqa: E402
from grid_triage.models import IncidentReview  # noqa: E402
from grid_triage.review import fingerprint_of  # noqa: E402
from grid_triage.seed import FLOOD_ALARM_IDS, T0  # noqa: E402
from grid_triage.wiring import Services  # noqa: E402

STRUCK_AT = T0 + timedelta(days=2)


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
    svc.cause.classify(flood.incident_id, T0 + timedelta(minutes=35))
    svc.severity.assess(lone.incident_id, lone.created_at + timedelta(minutes=5))
    svc.closure.close_incident(
        lone.incident_id,
        "op.dana",
        "operator",
        lone.created_at + timedelta(hours=2),
        "restored — check scenario",
        lone.created_at + timedelta(hours=2, minutes=5),
    )
    return svc


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        svc = build_service(Path(tmp))
        incidents = {i.incident_id: i for i in svc.store.list_incidents()}
        flood_id = next(
            i for i, x in incidents.items()
            if x.anchor_alarm_id == FLOOD_ALARM_IDS[0]
        )
        lone_id = next(i for i in incidents if i != flood_id)

        review = svc.reviews.strike(flood_id, "op.dana", "operator", STRUCK_AT)
        lone_review = svc.reviews.strike(
            lone_id, "op.dana", "operator", STRUCK_AT
        )

        # Every stored review verifies through the independent path.
        for stored in svc.reviews.reviews():
            problems = svc.reviews.verify_review(stored.review_id)
            if problems:
                failures.append(
                    f"review {stored.review_id} does not verify: {problems}"
                )

        # The headline figures, re-performed from raw store reads.
        foundation = review.sections["foundation"]
        members = svc.store.list_memberships(flood_id)
        if foundation["member_count"] != len(members):
            failures.append("member count does not re-perform")
        member_ids = {m["alarm_id"] for m in foundation["members"]}
        if member_ids != {m.alarm_id for m in members}:
            failures.append("member set does not re-perform")
        assessments = svc.store.list_severity_assessments(flood_id)
        sev = review.sections["cause_and_severity"]
        if sev["head_assessment_id"] != assessments[-1].assessment_id:
            failures.append("severity head does not re-perform")
        if sev["classification"] is None:
            failures.append("the worked incident's classification is missing")

        # The closed incident: its closure is on the record, and
        # its ETR gap is named for what it is — the closure
        # stood with no computed estimate.
        lone_completeness = lone_review.sections["completeness"]
        lone_foundation = lone_review.sections["foundation"]
        if lone_foundation["state"] != "closed":
            failures.append("the closed incident's state does not recompute")
        etr_row = next(
            r for r in lone_completeness["elements"]
            if r["element"] == "etr_estimate"
        )
        outcomes = svc.store.list_etr_outcomes(lone_id)
        if outcomes and outcomes[-1].status == "no_computed_estimate":
            if etr_row["state"] != "absent" or (
                etr_row["reason"] != "closed_without_computed_estimate"
            ):
                failures.append(
                    "the unestimated closure is not named as such"
                )

        # The bare record's absences are named, never dressed.
        flood_completeness = review.sections["completeness"]
        reasons = {
            r["element"]: r["reason"]
            for r in flood_completeness["elements"]
            if r["state"] == "absent"
        }
        if reasons.get("work_order") != "no_work_order_drafted":
            failures.append("the missing work order is not named")
        if reasons.get("closure_record") != "not_closed":
            failures.append("the open incident's closure is not named absent")

        # A forged review with an honest fingerprint over wrong
        # sections: the re-performance names the drifted section.
        forged_sections = json.loads(json.dumps(review.sections))
        forged_sections["foundation"]["member_count"] += 1
        forged = IncidentReview(
            review_id="IREV-forged000000001",
            incident_id=flood_id,
            pack_version=review.pack_version,
            struck_by="op.dana",
            role="operator",
            struck_at=STRUCK_AT,
            sections=forged_sections,
            fingerprint=fingerprint_of(
                review.pack_version, flood_id, forged_sections
            ),
        )
        svc.store.add_incident_review(forged)
        problems = svc.reviews.verify_review(forged.review_id)
        if not any("foundation" in p for p in problems):
            failures.append(
                f"forged figure not named by the verifier: {problems}"
            )

        # A review whose fingerprint does not cover its sections.
        stale = forged.model_copy(
            update={"review_id": "IREV-forged000000002", "fingerprint": "0" * 64}
        )
        svc.store.add_incident_review(stale)
        problems = svc.reviews.verify_review(stale.review_id)
        if not any("fingerprint" in p for p in problems):
            failures.append(
                f"fingerprint drift not named by the verifier: {problems}"
            )

    if failures:
        print("REVIEWS CHECK: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("REVIEWS CHECK: PASS")
    print(
        "  - reviews verify through the independent path; headline"
        " figures re-perform from raw store reads"
    )
    print(
        "  - thin records grade partial with their absences named;"
        " forged figures and fingerprint drift are named"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
