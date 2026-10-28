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

from grid_triage.evidence import EvidenceChain, canonical_json
from grid_triage.memory import MemoryService
from grid_triage.models import EtrOutcome, IncidentClosure, MemoryRecord


class ClosureRefusal(RuntimeError):
    """A closure act the record does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


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
    ) -> tuple[IncidentClosure, EtrOutcome, MemoryRecord]:
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        anchor = self.store.get_alarm(incident.anchor_alarm_id)
        problems: list[str] = []
        if not closed_by or not closed_by.strip():
            problems.append("a closure names its operator")
        if not role or not role.strip():
            problems.append("a closure names the operator's role")
        if not resolution_summary or not resolution_summary.strip():
            problems.append("a closure records the resolution facts")
        if self.store.get_incident_closure(incident_id) is not None:
            problems.append(f"incident {incident_id} is already closed")
        if anchor is not None and restored_at < anchor.raised_at:
            problems.append(
                "restored_at precedes the incident's first alarm — "
                "a restoration cannot predate the outage"
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

    def accuracy_ledger(self) -> dict:
        """Running ETR error statistics per cause class, computed
        from the stored outcomes. Only ``compared`` outcomes carry
        error figures; unestimated closures are counted separately
        so the ledger shows its own blind spots."""
        compared: dict[str, list[EtrOutcome]] = {}
        unestimated = 0
        for outcome in self.store.list_etr_outcomes():
            if outcome.status == "compared":
                compared.setdefault(outcome.cause_class, []).append(outcome)
            else:
                unestimated += 1

        def row(cause_class: str, outcomes: list[EtrOutcome]) -> dict:
            signed = [o.signed_error_minutes for o in outcomes]
            absolute = [o.absolute_error_minutes for o in outcomes]
            return {
                "cause_class": cause_class,
                "compared_count": len(outcomes),
                "mean_signed_error_minutes": (
                    round(sum(signed) / len(signed), 3) if signed else None
                ),
                "mean_absolute_error_minutes": (
                    round(sum(absolute) / len(absolute), 3) if absolute else None
                ),
                "max_absolute_error_minutes": max(absolute) if absolute else None,
            }

        by_class = [row(cc, compared[cc]) for cc in sorted(compared)]
        all_outcomes = [o for outcomes in compared.values() for o in outcomes]
        return {
            "by_cause_class": by_class,
            "overall": row("all", all_outcomes),
            "unestimated_closures": unestimated,
        }
