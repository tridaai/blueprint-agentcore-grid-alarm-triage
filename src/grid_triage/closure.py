"""Incident closure and the ETR accuracy ledger — Round 4.

Round 3's memory port stores closed incidents, but nothing in
Rounds 1–3 records the act of closing one. This module adds the
minimal named act the rest of Round 4 rests on: an operator
closes an incident by recording who closed it, in what role,
when supply was actually restored, and the resolution facts.
The act does three things, in order:

1. the closure itself is stored and put on the incident's
   evidence chain;
2. the closure feeds the memory port — the incident's record is
   written from its stored facts with ``closed_at`` equal to the
   recorded restoration time;
3. an :class:`EtrOutcome` compares the actual duration against
   the estimate of record — the latest ETR estimate whose
   ``as_of`` does not postdate the restoration (an estimate made
   after the fact is not the estimate the incident was run
   against). A closure whose incident was never computably
   estimated still records its actual duration, marked
   ``no_computed_estimate``: the track record shows the gap
   instead of quietly omitting the incident.

The full restoration-confirmation workflow (meter/OMS signals)
is Round 8; this act is deliberately only the recorded facts.

The **accuracy ledger** is derived, never stored: per cause
class, the count of compared outcomes and the running error
figures (mean signed, mean absolute, max absolute), computed in
code from the outcome records. It is the only ETR track record a
brief may quote — and it quotes it as recorded, or not at all.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from grid_triage.access import ACT_CLOSE_INCIDENT, Principal, control_for
from grid_triage.evidence import EvidenceChain, canonical_json
from grid_triage.memory import MemoryService
from grid_triage.models import EtrOutcome, IncidentClosure, MemoryRecord


class ClosureRefusal(RuntimeError):
    """A closure act the record does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


CLOSURE_PACK = {
    "version": "2026.10.1",
    # The roles that may record a closure — the control-room
    # vocabulary every other act pack in the system uses
    # (work orders, restoration, storm, handover). Round 10's
    # red team found this act took any non-empty string here:
    # a closure recorded as "banana" or "system" stood, and
    # with it the accuracy outcome the ledger derives.
    "actor_roles": ["operator", "duty_manager"],
}


def _minutes(delta) -> float:
    return round(delta.total_seconds() / 60.0, 3)


class ClosureService:
    def __init__(
        self,
        store,
        chain: EvidenceChain,
        memory: MemoryService,
    ):
        self.store = store
        self.chain = chain
        self.memory = memory

    def closure_for(self, incident_id: str) -> IncidentClosure | None:
        return self.store.get_incident_closure(incident_id)

    def close_incident(
        self,
        incident_id: str,
        closed_by: str,
        role: str,
        restored_at: datetime,
        resolution_summary: str,
        recorded_at: datetime,
        principal: Principal | None = None,
    ) -> tuple[IncidentClosure, EtrOutcome, MemoryRecord]:
        if principal is not None:
            # RT-17, closed by Round 11 decision (see
            # docs/red-team.md, amendment). This surface predates
            # the Round 7 gate: it records closure facts and
            # grades the ETR ledger, and until Round 11 anyone
            # who could reach it could close an incident — no
            # confirmation, no work-order position, no check on
            # who was calling. The decision: a principal-carrying
            # call on this surface stands under the same
            # discipline as the gated close. The caller must hold
            # the access pack's close act, must BE the operator
            # the closure names, recorded under their own role —
            # and the Round 7 gate's preconditions must hold
            # (a complete restoration confirmation of record and
            # no open work-order position), or the call refuses
            # naming them. The gated surface remains the way to
            # close anything short of that position: it can waive
            # an order with a reason, which this surface cannot
            # express. A principal-less call is the trusted local
            # path, unchanged since Round 4 — the seeded history
            # and the ETR accuracy ledger depend on it, and the
            # gated close calls this service internally as an
            # already-judged step (its own door enforces the
            # gated-close act; see restoration.py).
            control = control_for(self)
            control.require_act(
                principal,
                ACT_CLOSE_INCIDENT,
                target=incident_id,
                at=recorded_at,
            )
            control.require_actor(
                principal,
                closed_by,
                act=ACT_CLOSE_INCIDENT,
                target=incident_id,
                at=recorded_at,
            )
            control.require_role_match(
                principal,
                role,
                act=ACT_CLOSE_INCIDENT,
                target=incident_id,
                at=recorded_at,
            )
            gate_problems = self._carried_gate_problems(incident_id)
            if gate_problems:
                raise ClosureRefusal(gate_problems)
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        anchor = self.store.get_alarm(incident.anchor_alarm_id)
        problems: list[str] = []
        if not closed_by or not closed_by.strip():
            problems.append("a closure names its operator")
        if role not in CLOSURE_PACK["actor_roles"]:
            problems.append(
                f"role {role!r} may not record a closure (allowed: "
                f"{', '.join(CLOSURE_PACK['actor_roles'])})"
            )
        if not resolution_summary or not resolution_summary.strip():
            problems.append("a closure records the resolution facts")
        if self.store.get_incident_closure(incident_id) is not None:
            problems.append(f"incident {incident_id} is already closed")
        if anchor is not None and restored_at < anchor.raised_at:
            problems.append(
                "restored_at precedes the incident's first alarm — "
                "a restoration cannot predate the outage"
            )
        if restored_at > recorded_at:
            problems.append(
                "restored_at postdates the closure's own recording — "
                "a restoration is recorded after it happens, never "
                "dated into the future the ledger would grade as fact"
            )
        if problems:
            raise ClosureRefusal(problems)

        body = {
            "incident_id": incident_id,
            "closed_by": closed_by,
            "role": role,
            "restored_at": restored_at.isoformat(),
            "resolution_summary": resolution_summary,
            "recorded_at": recorded_at.isoformat(),
        }
        closure = IncidentClosure(
            closure_id=f"CLOSE-{hashlib.sha256(canonical_json(body)).hexdigest()[:16]}",
            **body,
        )
        self.store.add_incident_closure(closure)
        self.chain.append(
            incident_id,
            "closure",
            closure.model_dump(mode="json"),
            recorded_on=recorded_at,
            item_id=f"EV-{closure.closure_id}",
        )

        record = self.memory.record_incident(
            incident_id, resolution_summary, closed_at=restored_at
        )

        actual = _minutes(restored_at - anchor.raised_at) if anchor else 0.0
        classification = self.store.get_cause_classification(incident_id)
        cause_class = classification.cause_class if classification else "unknown"
        estimate = None
        for candidate in self.store.list_etr_estimates(incident_id):
            if candidate.as_of <= restored_at:
                estimate = candidate  # latest recorded at or before restoration
        outcome_body = {
            "incident_id": incident_id,
            "closure_id": closure.closure_id,
            "estimate_id": None,
            "cause_class": cause_class,
            # Round 8: the incident's operating mode, copied from
            # its stamp — the accuracy ledger's storm/normal
            # separation reads this field, never an inference.
            "operating_mode": incident.operating_mode,
            "storm_declaration_id": incident.storm_declaration_id,
            "status": "no_computed_estimate",
            "estimated_duration_minutes": None,
            "actual_duration_minutes": actual,
            "signed_error_minutes": None,
            "absolute_error_minutes": None,
            "recorded_at": recorded_at.isoformat(),
        }
        if estimate is not None and estimate.status == "computed":
            estimated = estimate.estimated_duration_minutes
            signed = round(actual - estimated, 3)
            outcome_body.update(
                {
                    "estimate_id": estimate.estimate_id,
                    "status": "compared",
                    "estimated_duration_minutes": estimated,
                    "signed_error_minutes": signed,
                    "absolute_error_minutes": abs(signed),
                }
            )
        outcome = EtrOutcome(
            outcome_id=(
                f"OUT-{hashlib.sha256(canonical_json(outcome_body)).hexdigest()[:16]}"
            ),
            **outcome_body,
        )
        self.store.add_etr_outcome(outcome)
        self.chain.append(
            incident_id,
            "etr_outcome",
            outcome.model_dump(mode="json"),
            recorded_on=recorded_at,
            item_id=f"EV-{outcome.outcome_id}",
        )
        return closure, outcome, record

    def _carried_gate_problems(self, incident_id: str) -> list[str]:
        """The Round 7 gate's preconditions, read from the store,
        for a principal-carrying call on this surface (RT-17).
        Mirrors RestorationService.close_incident's position
        derivation exactly: the confirmation of record is the
        latest computed; the work-order position is the head
        order's — a rejected head carries no open work, an order
        with a completion is done, and anything else open must be
        closed through the gated surface, which alone can waive
        an order with a reason. A missing incident defers to the
        engine's own KeyError below (returns no problems)."""
        if self.store.get_incident(incident_id) is None:
            return []
        problems: list[str] = []
        confirmations = self.store.list_restoration_confirmations(
            incident_id
        )
        latest = confirmations[-1] if confirmations else None
        if latest is None:
            problems.append(
                f"no restoration confirmation has been computed for"
                f" incident {incident_id} — under a carried principal"
                " this surface requires the Round 7 gate's"
                " preconditions: a complete restoration"
                " confirmation of record, or a close through the"
                " gated surface"
            )
        elif latest.status != "complete":
            missing = ", ".join(latest.missing_classes) or "none named"
            problems.append(
                f"restoration confirmation {latest.confirmation_id}"
                f" is {latest.status} (missing evidence classes:"
                f" {missing}; devices still out:"
                f" {latest.outstanding_devices}) — under a carried"
                " principal this surface requires a complete"
                " confirmation; a partial restoration is split or"
                " closed through the gated surface"
            )
        orders = self.store.list_work_orders(incident_id)
        if orders:
            head = orders[-1]
            decisions = self.store.list_work_order_decisions(head.order_id)
            decision = decisions[-1].decision if decisions else None
            completions = self.store.list_work_order_completions(
                head.order_id
            )
            if decision != "rejected" and not completions:
                state = decision or "draft"
                problems.append(
                    f"work order {head.order_id} is {state} with no"
                    " completion recorded — complete it, or close"
                    " through the gated surface, which can waive an"
                    " order with a reason; this surface cannot"
                    " express a waiver"
                )
        return problems

    def accuracy_ledger(self) -> dict:
        """Running ETR error statistics per cause class, computed
        from the stored outcomes. Only ``compared`` outcomes carry
        error figures; unestimated closures are counted separately
        so the ledger shows its own blind spots.

        Round 8 adds ``by_operating_mode``: the same statistics
        computed separately for normal and storm outcomes, from
        the mode recorded on each outcome. Storm performance is
        never blended into the normal figures — a reader who
        wants the storm record reads the storm row, and the
        rows' compared counts sum to the overall count exactly,
        so the separation itself is checkable."""
        compared: dict[str, list[EtrOutcome]] = {}
        unestimated = 0
        outcomes = self.store.list_etr_outcomes()
        for outcome in outcomes:
            if outcome.status == "compared":
                compared.setdefault(outcome.cause_class, []).append(outcome)
            else:
                unestimated += 1

        def stats(compared_outcomes: list[EtrOutcome]) -> dict:
            signed = [o.signed_error_minutes for o in compared_outcomes]
            absolute = [o.absolute_error_minutes for o in compared_outcomes]
            return {
                "compared_count": len(compared_outcomes),
                "mean_signed_error_minutes": (
                    round(sum(signed) / len(signed), 3) if signed else None
                ),
                "mean_absolute_error_minutes": (
                    round(sum(absolute) / len(absolute), 3) if absolute else None
                ),
                "max_absolute_error_minutes": max(absolute) if absolute else None,
            }

        def row(cause_class: str, outcomes_: list[EtrOutcome]) -> dict:
            return {"cause_class": cause_class, **stats(outcomes_)}

        by_class = [row(cc, compared[cc]) for cc in sorted(compared)]
        all_outcomes = [o for outcomes_ in compared.values() for o in outcomes_]
        by_mode = []
        for mode in ("normal", "storm"):
            mode_outcomes = [o for o in outcomes if o.operating_mode == mode]
            mode_compared = [o for o in mode_outcomes if o.status == "compared"]
            if not mode_outcomes:
                continue
            by_mode.append(
                {
                    "operating_mode": mode,
                    **stats(mode_compared),
                    "unestimated_closures": len(mode_outcomes)
                    - len(mode_compared),
                }
            )
        return {
            "by_cause_class": by_class,
            "by_operating_mode": by_mode,
            "overall": row("all", all_outcomes),
            "unestimated_closures": unestimated,
        }
