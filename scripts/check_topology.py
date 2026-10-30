"""Topology dual-control check (R16, extended R17).

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

Round 17 adds the closure claims:

- with no proposal standing at all, the direct declaration
  still refuses — naming the governed route — and strikes
  and loads nothing: dual control is the only route, not a
  route that binds only while a proposal is live;
- a direct declaration struck under the 2026.10.1 pack
  stands under the current pack: it verifies clean and
  re-correlation under it works;
- the declaration verifier re-performs every standing
  declaration; a direct declaration forged straight into
  the store under the current pack's stamp is named as
  struck by no route the record allows.

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
from grid_triage.models import TopologyDeclaration  # noqa: E402
from grid_triage.revisions import (  # noqa: E402
    TOPOLOGY_PACK_V1,
    TopologyRefusal,
    TopologyService,
    _hash_id,
)
from grid_triage.seed import FLOOD_ALARM_IDS, T0  # noqa: E402
from grid_triage.topology import load_topology_file  # noqa: E402
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
        expect(
            acts.verify_declarations() == [],
            "the struck declarations verify clean (R17)",
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

    # Round 17: the closure, the historical standing, and the
    # declaration-level forgery.
    with tempfile.TemporaryDirectory() as td:
        svc = build_service(Path(td))
        flood = next(
            i.incident_id
            for i in svc.store.list_incidents()
            if i.anchor_alarm_id == FLOOD_ALARM_IDS[0]
        )
        acts = svc.topology_acts
        try:
            acts.declare("v2", "op.dana", "operator", PROPOSED_AT, EFFECTIVE_AT)
            expect(False, "a direct declaration with no proposal standing is refused (R17)")
        except TopologyRefusal as exc:
            expect(
                any(
                    "closed" in p and "decide_topology" in p
                    for p in exc.problems
                ),
                "a direct declaration with no proposal standing is refused, naming the governed route (R17)",
            )
        expect(
            acts.declaration_for("v2") is None
            and svc.store.list_topology_versions() == ["v1"],
            "the refused direct act struck and loaded nothing (R17)",
        )
        legacy = TopologyService(svc.store, pack=TOPOLOGY_PACK_V1)
        declaration = legacy.declare(
            "v2", "op.dana", "operator", PROPOSED_AT, EFFECTIVE_AT
        )
        expect(
            declaration.proposal_id is None
            and declaration.pack_version == "2026.10.1",
            "a direct declaration under the 2026.10.1 pack strikes as it did (R17)",
        )
        expect(
            acts.verify_declarations() == [],
            "the historical declaration verifies under the current pack (R17)",
        )
        revision = svc.revisions.recorrelate(
            flood, "op.dana", "operator", T0 + timedelta(hours=6)
        )
        expect(
            revision.target_topology_version == "v2",
            "re-correlation under the historical declaration works (R17)",
        )

    with tempfile.TemporaryDirectory() as td:
        svc = build_service(Path(td))
        acts = svc.topology_acts
        expect(
            acts.verify_declarations() == [],
            "an empty record verifies clean (R17)",
        )
        body = {
            "version": "v2",
            "pack_version": "2026.10.2",
            "declared_by": "op.dana",
            "role": "operator",
            "declared_at": PROPOSED_AT.isoformat(),
            "effective_at": EFFECTIVE_AT.isoformat(),
            "change_set": load_topology_file("v2")["change_set"],
            "proposed_by": None,
            "proposal_id": None,
        }
        forged = TopologyDeclaration(
            declaration_id=_hash_id("TOPDECL", body), **body
        )
        svc.store.add_topology_declaration(forged)
        problems = acts.verify_declarations()
        expect(
            any("no route the record allows" in p for p in problems),
            "a direct declaration forged at the store under the current pack is named (R17)",
        )

    if failures:
        print(f"TOPOLOGY CHECK: FAIL ({len(failures)})")
        return 1
    print("TOPOLOGY CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
