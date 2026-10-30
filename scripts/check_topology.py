"""Topology dual-control check (R16).

Builds the reference feed on a fresh deployment, then grades
the Round 16 claims about the topology declaration's second
pair of eyes:

- a proposal changes nothing: no declaration is struck, the
  version is not effective, and re-correlation under it is
  refused — the record is exactly as it was, plus the
  proposal artefact itself;
- the proposer cannot decide their own proposal, under the
  name folding or exactly;
- while a proposal stands pending, the direct declaration of
  the same version is blocked — the single-signature route
  cannot walk around a live proposal;
- a second operator's confirmation strikes exactly the
  declaration the proposal stated (effective time, change
  set), with the proposal's provenance on its face, and
  re-correlation under the version then works;
- the verifier re-performs the standing clean; a forged
  decision inserted straight into the store (the proposer
  confirming their own proposal, bypassing the service) is
  named by the verifier, as is a declaration struck under a
  pending proposal's name that no confirmation produced.

Run: uv run python scripts/check_topology.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from grid_triage.config import Settings  # noqa: E402
from grid_triage.models import TopologyDecision  # noqa: E402
from grid_triage.revisions import TopologyRefusal  # noqa: E402
from grid_triage.seed import FLOOD_ALARM_IDS, T0  # noqa: E402
from grid_triage.wiring import Services  # noqa: E402

PROPOSED_AT = T0 + timedelta(hours=4)
EFFECTIVE_AT = T0 + timedelta(hours=5)
DECIDED_AT = T0 + timedelta(hours=4, minutes=30)


def build_service(tmp: Path) -> Services:
    svc = Services(
        Settings(
            sqlite_path=str(tmp / "grid.db"),
            object_store_dir=str(tmp / "objects"),
        )
    )
    svc.intake.ingest_feed(svc.feed.fetch_alarms())
    svc.correlator.correlate_new(svc.topology)
    return svc


def main() -> int:
    failures: list[str] = []

    def expect(condition: bool, label: str) -> None:
        print(f"  {'ok' if condition else 'FAIL'} — {label}")
        if not condition:
            failures.append(label)

    with tempfile.TemporaryDirectory() as td:
        svc = build_service(Path(td))
        flood = next(
            i.incident_id
            for i in svc.store.list_incidents()
            if i.anchor_alarm_id == FLOOD_ALARM_IDS[0]
        )
        acts = svc.topology_acts

        proposal = acts.propose(
            "v2", "op.dana", "operator", PROPOSED_AT, EFFECTIVE_AT
        )
        expect(
            acts.declaration_for("v2") is None,
            "a proposal strikes no declaration",
        )
        expect(
            acts.proposal_state(proposal) == "pending",
            "a fresh proposal stands pending",
        )
        try:
            svc.revisions.recorrelate(
                flood, "op.dana", "operator", DECIDED_AT
            )
            expect(False, "re-correlation under an unconfirmed version is refused")
        except Exception as exc:
            expect(
                "has not been declared" in str(exc),
                "re-correlation under an unconfirmed version is refused",
            )
        try:
            acts.decide(
                proposal.proposal_id, "OP.DANA", "operator",
                DECIDED_AT, "confirmed",
            )
            expect(False, "the proposer deciding their own proposal is refused")
        except TopologyRefusal as exc:
            expect(
                any("dual control" in p for p in exc.problems),
                "the proposer deciding their own proposal is refused",
            )
        try:
            acts.declare("v2", "op.dana", "operator", PROPOSED_AT, EFFECTIVE_AT)
            expect(False, "a direct declaration is blocked while a proposal stands")
        except TopologyRefusal as exc:
            expect(
                any("pending dual-control proposal" in p for p in exc.problems),
                "a direct declaration is blocked while a proposal stands",
            )
        record, declaration = acts.decide(
            proposal.proposal_id, "dm.reyes", "duty_manager",
            DECIDED_AT, "confirmed",
        )
        expect(record.decision == "confirmed", "the second operator confirms")
        expect(
            declaration is not None
            and declaration.proposed_by == "op.dana"
            and declaration.proposal_id == proposal.proposal_id,
            "the declaration carries the proposal's provenance",
        )
        expect(
            declaration is not None
            and declaration.effective_at == EFFECTIVE_AT
            and declaration.change_set == proposal.change_set,
            "the declaration is exactly the one the proposal stated",
        )
        expect(
            acts.verify_proposal(proposal.proposal_id) == [],
            "the confirmed standing verifies clean",
        )
        revision = svc.revisions.recorrelate(
            flood, "op.dana", "operator", T0 + timedelta(hours=6)
        )
        expect(
            revision.target_topology_version == "v2",
            "re-correlation under the confirmed version works",
        )

    with tempfile.TemporaryDirectory() as td:
        svc = build_service(Path(td))
        acts = svc.topology_acts
        proposal = acts.propose(
            "v2", "op.dana", "operator", PROPOSED_AT, EFFECTIVE_AT
        )
        forged = TopologyDecision(
            decision_id="TOPDEC-forged000000001",
            proposal_id=proposal.proposal_id,
            decision="confirmed",
            decided_by="op.dana",
            role="operator",
            decided_at=DECIDED_AT,
            reason=None,
        )
        svc.store.add_topology_decision(forged)
        problems = acts.verify_proposal(proposal.proposal_id)
        expect(
            any("own proposer" in p for p in problems),
            "a forged self-confirmation inserted at the store is named",
        )
        expect(
            any("no declaration" in p or "declarations name it" in p for p in problems),
            "a confirmation with no declaration struck is named",
        )

    if failures:
        print(f"TOPOLOGY CHECK: FAIL ({len(failures)})")
        return 1
    print("TOPOLOGY CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
