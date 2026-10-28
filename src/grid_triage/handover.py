"""Shift handover — Round 9.

A control room changes hands on a record, not on a briefing.
At a named operator's **handover act**, the state of the room
is frozen into an artefact assembled only from artefacts of
record — the computation is the operations board's own
(:mod:`grid_triage.board`), so a handover and the console can
never tell different stories:

- the open incidents, with severity and operating mode;
- the approvals still pending a named decision (severity
  acknowledgements and undecided work-order drafts);
- the open work orders;
- the restorations not yet confirmed complete;
- the storm state, folded from the declaration acts at the
  handover's own moment;
- the ETR accuracy snapshot.

The artefact carries a ``record_hash`` over those sections.
The incoming operator's **acceptance** is a second named act
bound to that hash: it says the record being taken over is
the record the hash covers. Two disciplines follow:

- **Staleness is on the artefact's face.** The verifier
  re-performs the sections from the store; when the record
  has moved, the handover reads stale and the drift is named
  section by section. Accepting a stale handover is refused
  — the way forward is a fresh handover, which supersedes by
  link. The stale artefact itself is never edited.
- **Nothing here mutates the record.** Assembling, verifying
  and accepting write only the handover and acceptance rows;
  no incident, order, estimate or act underneath changes.

Assembling the same record again for the same operator is a
no-op returning the standing artefact (the severity/review
discipline); a different record produces the next artefact in
the chain.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from grid_triage.board import BoardService
from grid_triage.evidence import canonical_json
from grid_triage.models import HandoverAcceptance, ShiftHandover

HANDOVER_PACK = {
    "version": "2026.10.1",
    # The control-room roles that may hand over or accept —
    # the same vocabulary the work pack and the storm pack
    # use for their named acts.
    "actor_roles": ["operator", "duty_manager"],
}

SECTION_NAMES = (
    "open_incidents",
    "pending_approvals",
    "open_work_orders",
    "unconfirmed_restorations",
    "storm",
    "etr_accuracy",
)


def _hash_id(prefix: str, body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"{prefix}-{digest[:16]}"


def record_hash_of(sections: dict) -> str:
    """The hash an acceptance binds: over the sections exactly
    as stored, canonicalised."""
    return hashlib.sha256(canonical_json(sections)).hexdigest()


class HandoverRefusal(RuntimeError):
    """A handover or acceptance act the record does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class HandoverService:
    """Store-facing handover work: the assembly act, the
    acceptance act, and the verifier that names drift."""

    def __init__(self, store, board: BoardService, pack: dict = HANDOVER_PACK):
        self.store = store
        self.board = board
        self.pack = pack

    # -- reads ---------------------------------------------------------
    def handovers(self) -> list[ShiftHandover]:
        return self.store.list_shift_handovers()

    def get(self, handover_id: str) -> ShiftHandover | None:
        return self.store.get_shift_handover(handover_id)

    def latest(self) -> ShiftHandover | None:
        history = self.store.list_shift_handovers()
        return history[-1] if history else None

    def acceptance_for(
        self, handover_id: str
    ) -> HandoverAcceptance | None:
        acts = self.store.list_handover_acceptances(handover_id)
        return acts[-1] if acts else None

    # -- the acts --------------------------------------------------------
    def _actor_problems(
        self, actor: str, role: str, verb: str
    ) -> list[str]:
        problems: list[str] = []
        if not actor or not actor.strip():
            problems.append(f"a handover {verb} names the operator performing it")
        if role not in self.pack["actor_roles"]:
            problems.append(
                f"role {role!r} may not {verb} a shift handover (allowed: "
                f"{', '.join(self.pack['actor_roles'])})"
            )
        return problems

    def assemble(
        self, handed_over_by: str, role: str, handed_over_at: datetime
    ) -> ShiftHandover:
        """Freeze the room's state into a handover artefact.
        The sections are the board's computation at the
        handover's moment; the artefact is content-addressed
        over them and supersedes the previous handover by
        link when the record has moved."""
        problems = self._actor_problems(handed_over_by, role, "assembly")
        if problems:
            raise HandoverRefusal(problems)
        sections = self.board.sections(handed_over_at)
        digest = record_hash_of(sections)
        latest = self.latest()
        if (
            latest is not None
            and latest.record_hash == digest
            and latest.handed_over_by == handed_over_by
        ):
            return latest  # the same record, handed over again: a no-op
        body = {
            "pack_version": self.pack["version"],
            "handed_over_by": handed_over_by,
            "role": role,
            "handed_over_at": handed_over_at.isoformat(),
            "supersedes": latest.handover_id if latest else None,
            "sections": sections,
            "record_hash": digest,
        }
        handover = ShiftHandover(
            handover_id=_hash_id("HANDOVER", body), **body
        )
        self.store.add_shift_handover(handover)
        return handover

    def accept(
        self,
        handover_id: str,
        accepted_by: str,
        role: str,
        accepted_at: datetime,
    ) -> HandoverAcceptance:
        """The incoming operator's act, bound to the handover's
        record hash. Refused — with the drift named — when the
        record has moved since the handover was assembled."""
        handover = self.store.get_shift_handover(handover_id)
        if handover is None:
            raise KeyError(f"no such shift handover: {handover_id}")
        problems = self._actor_problems(accepted_by, role, "acceptance")
        if accepted_by and accepted_by == handover.handed_over_by:
            problems.append(
                "the operator handing over cannot accept their own "
                "handover — acceptance is the incoming operator's act"
            )
        existing = self.acceptance_for(handover_id)
        if existing is not None:
            problems.append(
                f"handover {handover_id} is already accepted "
                f"(act {existing.acceptance_id}, by {existing.accepted_by})"
            )
        drift = self.verify(handover_id)
        if drift:
            problems.append(
                "the handover is stale — the record has moved since "
                "it was assembled: " + "; ".join(drift)
            )
        if problems:
            raise HandoverRefusal(problems)
        body = {
            "handover_id": handover_id,
            "record_hash": handover.record_hash,
            "accepted_by": accepted_by,
            "role": role,
            "accepted_at": accepted_at.isoformat(),
        }
        acceptance = HandoverAcceptance(
            acceptance_id=_hash_id("HACC", body), **body
        )
        self.store.add_handover_acceptance(acceptance)
        return acceptance

    # -- verification ----------------------------------------------------
    def verify(self, handover_id: str) -> list[str]:
        """Re-perform a handover's sections from the store and
        name every drift. Empty means the artefact still
        recomputes exactly — the record it froze is the record
        that stands."""
        handover = self.store.get_shift_handover(handover_id)
        if handover is None:
            raise KeyError(f"no such shift handover: {handover_id}")
        problems: list[str] = []
        if record_hash_of(handover.sections) != handover.record_hash:
            problems.append(
                "the stored record hash does not cover the stored sections"
            )
        recomputed = self.board.sections(handover.handed_over_at)
        for name in SECTION_NAMES:
            if recomputed.get(name) != handover.sections.get(name):
                problems.append(f"the {name} section does not recompute")
        acceptance = self.acceptance_for(handover_id)
        if acceptance is not None and (
            acceptance.record_hash != handover.record_hash
        ):
            problems.append(
                "the acceptance act does not bind this handover's record hash"
            )
        return problems

    def view(self, handover: ShiftHandover) -> dict:
        """The surface shape: the artefact, its acceptance, and
        its staleness — computed live, so a handover over a
        moved record is stale on its face."""
        problems = self.verify(handover.handover_id)
        acceptance = self.acceptance_for(handover.handover_id)
        body = handover.model_dump(mode="json")
        body["acceptance"] = (
            acceptance.model_dump(mode="json") if acceptance else None
        )
        body["stale"] = bool(problems)
        body["drift"] = problems
        return body

    def latest_view(self) -> dict | None:
        latest = self.latest()
        return self.view(latest) if latest else None
