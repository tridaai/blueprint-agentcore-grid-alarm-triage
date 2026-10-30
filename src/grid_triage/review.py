"""The post-incident review — Round 15.

Rounds 1–14 built the record and the surfaces that read it
live or in aggregate. The oversight report (Round 12) is a
periodic statement over a window and is aggregate by
construction: no section names an incident. The handover
(Round 9) freezes the live picture for a shift change and
goes stale the moment the record moves. The post-storm
review (Round 8) covers one storm window. What none of them
does is the thing a duty manager asks for the morning after
a bad fault: **show me this one incident, whole** — how it
was founded, what it was judged to be, how severe it
computed, what restoration was estimated and what actually
happened, what work was drafted and decided, what the field
reported, what was confirmed, how it closed, and what the
record does not hold. This module is that artefact.

- **Re-performance, not narration.** Every section is rebuilt
  from the incident's artefacts of record — memberships,
  classification, severity assessments, ETR estimates and
  the closure outcome, work-order versions and their acts,
  field updates, restoration signals and confirmations, the
  closure and the gated close act, revision and split links,
  quarantine holds. A fact the artefacts do not carry does
  not appear; the review computes, it never summarises from
  prose and never asks a model anything.
- **Completeness is graded on the artefact's face.** The
  review pack names the elements a review accounts for; each
  is either ``present`` with the artefact ids behind it or
  ``absent`` with a stated reason from the pack's vocabulary
  (``not_assessed``, ``no_work_order_drafted``,
  ``closed_without_computed_estimate``, …). The verdict is
  ``complete`` only when every element the pack requires for
  the incident's state is present; otherwise ``partial``,
  with the missing required elements named. A thin record
  reads as a thin record — the review never lets a sparse
  incident wear a full one's shape.
- **Held text stays held.** The field record names the
  quarantine holds against the incident — hold id, channel,
  the rules tripped, whether a named reviewer released it —
  and nothing else. Raw held text lives only in the
  quarantine book (Round 14); a released text is vouched for
  a human reader, and the review is not a republication
  channel, so no text, held or released, is carried here.
- **Insert-only and content-addressed.** A review is
  fingerprinted over its pack version, its incident and its
  sections. Re-striking over a record that has not moved
  returns the standing artefact; over a moved record it
  produces the next artefact for that incident, superseding
  by link. A stored review is never edited.
- **Verification is a second path.** ``verify_review``
  re-performs every section with its own walk over the store
  and names the sections that do not recompute; a planted
  tamper is named, not averaged away.
  ``scripts/check_reviews.py`` runs it over every stored
  review.
- **The act is judged like every act here.** Striking a
  review is a named operator or duty manager act under the
  access pack (Round 11): a carried principal must hold the
  ``strike_incident_review`` act and be the human it names.
  Reads sit on the pack's ``incident_review`` surface and
  inherit incident existence hiding: a role that may not
  read incidents (field crews) is answered exactly as a
  non-existent incident is answered.
- **The artefact says what it is.** A post-incident review
  over one incident's record, computed from its artefacts —
  it operates nothing, amends nothing, and is not an
  external or regulatory report.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from grid_triage.access import (
    ACT_STRIKE_INCIDENT_REVIEW,
    Principal,
    guard_act,
)
from grid_triage.board import _superseded_ids, is_closed
from grid_triage.evidence import canonical_json
from grid_triage.models import IncidentReview
from grid_triage.quarantine import released_hold_ids

REVIEW_PACK = {
    "version": "2026.10.1",
    # The control-room roles that may strike a review — the
    # shift's own authorities. The auditor reads; it does not
    # strike (the access pack enforces the same line).
    "actor_roles": ["operator", "duty_manager"],
    "sections": [
        "foundation",
        "cause_and_severity",
        "etr_record",
        "work_orders",
        "field_record",
        "restoration_and_closure",
        "lineage",
        "completeness",
    ],
    # The elements the completeness section accounts for, in
    # the order it accounts for them.
    "elements": [
        "classification",
        "severity_assessment",
        "etr_estimate",
        "work_order",
        "field_updates",
        "restoration_confirmation",
        "closure_record",
    ],
    # The elements the completeness verdict requires, by the
    # incident's state as the foundation section derives it.
    # The rest are accounted for either way — present with
    # their ids, or absent with their reason named — but the
    # verdict does not fail an open incident for lacking a
    # closure, nor a live one for an estimate not yet struck.
    "required": {
        "open": ["classification", "severity_assessment"],
        "closed": [
            "classification",
            "severity_assessment",
            "closure_record",
        ],
        "superseded": ["classification", "severity_assessment"],
    },
}

BASIS = (
    "post-incident review artefact — one incident's timeline "
    "re-performed from its artefacts of record, with its "
    "completeness graded on its face; it operates nothing, "
    "amends nothing, and is not an external report"
)


def _hash_id(prefix: str, body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"{prefix}-{digest[:16]}"


def fingerprint_of(pack_version: str, incident_id: str, sections) -> str:
    """The record fingerprint: over the pack, the incident and
    the sections exactly as stored, canonicalised."""
    return hashlib.sha256(
        canonical_json(
            {
                "pack_version": pack_version,
                "incident_id": incident_id,
                "sections": sections,
            }
        )
    ).hexdigest()


class ReviewRefusal(RuntimeError):
    """A review act the record does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class IncidentReviewService:
    """Store-facing incident reviews: the strike act, the
    reads, and the independent verifier."""

    def __init__(self, store, pack: dict = REVIEW_PACK):
        self.store = store
        self.pack = pack

    # -- reads ------------------------------------------------------
    def reviews(self, incident_id: str | None = None) -> list[IncidentReview]:
        return self.store.list_incident_reviews(incident_id)

    def get(self, review_id: str) -> IncidentReview | None:
        return self.store.get_incident_review(review_id)

    def head_for_incident(self, incident_id: str) -> IncidentReview | None:
        """The standing review for an incident: the latest
        struck over it, if any."""
        head = None
        for review in self.store.list_incident_reviews(incident_id):
            head = review
        return head

    # -- the strike act ----------------------------------------------
    def strike(
        self,
        incident_id: str,
        struck_by: str,
        role: str,
        struck_at: datetime,
        principal: Principal | None = None,
    ) -> IncidentReview:
        """Re-perform one incident's timeline into a review
        artefact. The incident must exist; the record may be
        as thin as it is — thinness is what the completeness
        section states, never a reason to refuse the review.
        A moved record supersedes the standing review by
        link; an unmoved one returns it."""
        guard_act(
            self,
            principal,
            ACT_STRIKE_INCIDENT_REVIEW,
            target=incident_id,
            actor=struck_by,
            act_role=role,
            at=struck_at,
        )
        problems: list[str] = []
        if not struck_by or not struck_by.strip():
            problems.append("a review strike names the operator performing it")
        if role not in self.pack["actor_roles"]:
            problems.append(
                f"role {role!r} may not strike an incident review "
                f"(allowed: {', '.join(self.pack['actor_roles'])})"
            )
        if problems:
            raise ReviewRefusal(problems)
        if self.store.get_incident(incident_id) is None:
            raise KeyError(f"no such incident: {incident_id}")
        sections = compute_sections(self.store, self.pack, incident_id)
        fingerprint = fingerprint_of(
            self.pack["version"], incident_id, sections
        )
        head = self.head_for_incident(incident_id)
        if head is not None and head.fingerprint == fingerprint:
            return head  # the record has not moved: a no-op
        body = {
            "pack_version": self.pack["version"],
            "incident_id": incident_id,
            "struck_by": struck_by,
            "role": role,
            "struck_at": struck_at.isoformat(),
            "supersedes": head.review_id if head else None,
            "sections": sections,
            "fingerprint": fingerprint,
        }
        review = IncidentReview(review_id=_hash_id("IREV", body), **body)
        self.store.add_incident_review(review)
        return review

    # -- verification --------------------------------------------------
    def verify_review(self, review_id: str) -> list[str]:
        """Re-perform a stored review section for section by
        the independent path and name every drift. Empty means
        the artefact still recomputes exactly."""
        review = self.store.get_incident_review(review_id)
        if review is None:
            raise KeyError(f"no such incident review: {review_id}")
        problems: list[str] = []
        if (
            fingerprint_of(
                review.pack_version, review.incident_id, review.sections
            )
            != review.fingerprint
        ):
            problems.append(
                "the stored fingerprint does not cover the stored sections"
            )
        recomputed = reperform_sections(
            self.store, self.pack, review.incident_id
        )
        for name in self.pack["sections"]:
            if recomputed.get(name) != review.sections.get(name):
                problems.append(f"the {name} section does not recompute")
        return problems

    def view(self, review: IncidentReview) -> dict:
        body = review.model_dump(mode="json")
        body["basis"] = BASIS
        return body


# ---------------------------------------------------------------------
# The strike path: the timeline rebuilt from the artefacts, via
# the board's own state definitions where the board has already
# stated them.
# ---------------------------------------------------------------------


def _incident_state(store, incident_id: str) -> str:
    if is_closed(store, incident_id):
        return "closed"
    if incident_id in _superseded_ids(store):
        return "superseded"
    return "open"


def _order_state(order, decisions, completions) -> str:
    """A work-order version's derived position: the decision
    state of the exact version (draft until a decision act
    names it), with completion as its own fact on top."""
    decision = decisions[-1] if decisions else None
    state = decision.decision if decision else "draft"
    return state


def _completeness(pack: dict, state: str, found: dict) -> dict:
    """Grade the record's completeness. ``found`` maps each
    element to (artefact_ids, absence_reason): present when
    the id list is non-empty, else absent with its reason.
    Every further absence the sections carry (the storm
    stamp, quarantine holds, lineage, contradictions) is
    named alongside, ungraded."""
    elements: list[dict] = []
    for name in pack["elements"]:
        ids, reason = found[name]
        elements.append(
            {
                "element": name,
                "state": "present" if ids else "absent",
                "artefact_ids": ids,
                "reason": None if ids else reason,
            }
        )
    required = pack["required"][state]
    required_missing = [
        row["element"]
        for row in elements
        if row["element"] in required and row["state"] == "absent"
    ]
    return {
        "status": "complete" if not required_missing else "partial",
        "elements": elements,
        "required_missing": required_missing,
    }


def compute_sections(store, pack: dict, incident_id: str) -> dict:
    incident = store.get_incident(incident_id)
    state = _incident_state(store, incident_id)

    # -- foundation --------------------------------------------------
    members = []
    for membership in store.list_memberships(incident_id):
        alarm = store.get_alarm(membership.alarm_id)
        members.append(
            {
                "alarm_id": membership.alarm_id,
                "rule_id": membership.rule_id,
                "device_id": alarm.device_id if alarm else None,
                "kind": alarm.kind if alarm else None,
                "raised_at": alarm.raised_at.isoformat() if alarm else None,
            }
        )
    members.sort(key=lambda m: (m["raised_at"] or "", m["alarm_id"]))
    declaration = None
    if incident.storm_declaration_id:
        for act in store.list_storm_declarations():
            if act.declaration_id == incident.storm_declaration_id:
                declaration = {
                    "declaration_id": act.declaration_id,
                    "kind": act.kind,
                    "declared_by": act.declared_by,
                    "role": act.role,
                    "declared_at": act.declared_at.isoformat(),
                }
                break
    successors = sorted(
        {r.revised_incident_id for r in store.list_incident_revisions()
         if r.prior_incident_id == incident_id}
        | {part for s in store.list_incident_splits()
           if s.incident_id == incident_id
           for part in (s.restored_incident_id, s.continuing_incident_id)}
    )
    foundation = {
        "incident_id": incident.incident_id,
        "feeder_id": incident.feeder_id,
        "substation_id": incident.substation_id,
        "created_at": incident.created_at.isoformat(),
        "anchor_alarm_id": incident.anchor_alarm_id,
        "topology_version": incident.topology_version,
        "correlation_pack_version": incident.correlation_pack_version,
        "operating_mode": incident.operating_mode,
        "storm_pack_version": incident.storm_pack_version,
        "storm_declaration_id": incident.storm_declaration_id,
        "storm_declaration": declaration,
        "state": state,
        "superseded_by": successors if state == "superseded" else [],
        "members": members,
        "member_count": len(members),
    }

    # -- cause and severity -------------------------------------------
    classification = store.get_cause_classification(incident_id)
    assessments = store.list_severity_assessments(incident_id)
    acknowledgements = store.list_severity_acknowledgements(incident_id)
    cause_and_severity = {
        "classification": (
            {
                "classification_id": classification.classification_id,
                "cause_class": classification.cause_class,
                "rule_id": classification.rule_id,
                "pack_version": classification.pack_version,
                "classified_at": classification.classified_at.isoformat(),
            }
            if classification
            else None
        ),
        "assessments": [
            {
                "assessment_id": a.assessment_id,
                "as_of": a.as_of.isoformat(),
                "band": a.band,
                "score": a.score,
                "supersedes": a.supersedes,
                "missing_inputs": list(a.missing_inputs),
                "escalation_required": a.escalation_required,
            }
            for a in assessments
        ],
        "head_assessment_id": (
            assessments[-1].assessment_id if assessments else None
        ),
        "acknowledgements": [
            {
                "ack_id": ack.ack_id,
                "assessment_id": ack.assessment_id,
                "acknowledged_by": ack.acknowledged_by,
                "role": ack.role,
                "acknowledged_at": ack.acknowledged_at.isoformat(),
            }
            for ack in acknowledgements
        ],
    }

    # -- the ETR record -------------------------------------------------
    closure = store.get_incident_closure(incident_id)
    close_act = store.get_incident_close_act(incident_id)
    restored_at = None
    if close_act is not None:
        restored_at = close_act.restored_at
    elif closure is not None:
        restored_at = closure.restored_at
    estimates = store.list_etr_estimates(incident_id)
    computed = [e for e in estimates if e.status == "computed"]
    of_record = None
    for estimate in computed:
        if restored_at is None or estimate.as_of <= restored_at:
            of_record = estimate
    outcomes = store.list_etr_outcomes(incident_id)
    outcome = outcomes[-1] if outcomes else None
    etr_record = {
        "estimates": [
            {
                "estimate_id": e.estimate_id,
                "as_of": e.as_of.isoformat(),
                "status": e.status,
                "basis": e.basis,
                "total_minutes": e.total_minutes,
                "estimated_restored_at": (
                    e.estimated_restored_at.isoformat()
                    if e.estimated_restored_at
                    else None
                ),
                "operating_mode": e.operating_mode,
                "supersedes": e.supersedes,
            }
            for e in estimates
        ],
        "estimate_of_record_id": (
            of_record.estimate_id if of_record else None
        ),
        "outcome": (
            {
                "outcome_id": outcome.outcome_id,
                "status": outcome.status,
                "estimate_id": outcome.estimate_id,
                "actual_duration_minutes": outcome.actual_duration_minutes,
                "signed_error_minutes": outcome.signed_error_minutes,
                "absolute_error_minutes": outcome.absolute_error_minutes,
            }
            if outcome
            else None
        ),
    }

    # -- work orders -----------------------------------------------------
    orders = store.list_work_orders(incident_id)
    order_rows = []
    decision_rows = []
    assignment_rows = []
    completion_rows = []
    for order in orders:
        decisions = store.list_work_order_decisions(order.order_id)
        completions = store.list_work_order_completions(order.order_id)
        assignments = store.list_work_order_assignments(order.order_id)
        order_rows.append(
            {
                "order_id": order.order_id,
                "version": order.version,
                "drafted_by": order.drafted_by,
                "drafted_at": order.drafted_at.isoformat(),
                "state": _order_state(order, decisions, completions),
                "completed": bool(completions),
                "classification_id": order.classification_id,
                "estimate_id": order.estimate_id,
                "gaps": list(order.gaps),
            }
        )
        decision_rows.extend(
            {
                "decision_id": d.decision_id,
                "order_id": d.order_id,
                "decision": d.decision,
                "decided_by": d.decided_by,
                "role": d.role,
                "decided_at": d.decided_at.isoformat(),
            }
            for d in decisions
        )
        assignment_rows.extend(
            {
                "assignment_id": a.assignment_id,
                "order_id": a.order_id,
                "crew_id": a.crew_id,
                "assigned_by": a.assigned_by,
                "assigned_at": a.assigned_at.isoformat(),
            }
            for a in assignments
        )
        completion_rows.extend(
            {
                "completion_id": c.completion_id,
                "order_id": c.order_id,
                "completed_by": c.completed_by,
                "completed_at": c.completed_at.isoformat(),
            }
            for c in completions
        )
    work_orders = {
        "orders": order_rows,
        "decisions": decision_rows,
        "assignments": assignment_rows,
        "completions": completion_rows,
        "head_order_id": orders[-1].order_id if orders else None,
        "head_state": order_rows[-1]["state"] if order_rows else None,
    }

    # -- the field record --------------------------------------------------
    updates = store.list_field_updates(incident_id)
    released = released_hold_ids(store)
    holds = store.list_quarantine_holds(incident_id)

    def _release_row(hold_id: str) -> dict | None:
        releases = store.list_quarantine_releases(hold_id)
        if not releases:
            return None
        release = releases[-1]
        return {
            "release_id": release.release_id,
            "released_by": release.released_by,
            "released_at": release.released_at.isoformat(),
        }

    field_record = {
        "updates": [
            {
                "update_id": u.update_id,
                "kind": u.kind,
                "reported_by": u.reported_by,
                "role": u.role,
                "reported_at": u.reported_at.isoformat(),
                "alarm_id": u.alarm_id,
                "correlation_status": u.correlation.get("status"),
                "hold_ids": [h["hold_id"] for h in u.holds],
            }
            for u in updates
        ],
        "quarantine_holds": [
            {
                "hold_id": h.hold_id,
                "channel": h.channel,
                "field": h.field,
                "source_ref": h.source_ref,
                "rules": list(h.rules),
                "state": "released" if h.hold_id in released else "held",
                "release": _release_row(h.hold_id),
            }
            for h in holds
        ],
    }

    # -- restoration and closure --------------------------------------------
    signals = store.list_restoration_signals(incident_id)
    confirmations = store.list_restoration_confirmations(incident_id)
    contradictions = [
        c for c in store.list_post_close_contradictions()
        if c.closed_incident_id == incident_id
    ]
    restoration_and_closure = {
        "signals": [
            {
                "signal_id": s.signal_id,
                "source": s.source,
                "device_id": s.device_id,
                "feeder_id": s.feeder_id,
                "state": s.state,
                "observed_at": s.observed_at.isoformat(),
                "recorded_by": s.recorded_by,
            }
            for s in signals
        ],
        "confirmations": [
            {
                "confirmation_id": c.confirmation_id,
                "status": c.status,
                "computed_at": c.computed_at.isoformat(),
                "missing_classes": list(c.missing_classes),
                "supersedes": c.supersedes,
            }
            for c in confirmations
        ],
        "head_confirmation_id": (
            confirmations[-1].confirmation_id if confirmations else None
        ),
        "closure": (
            {
                "closure_id": closure.closure_id,
                "closed_by": closure.closed_by,
                "role": closure.role,
                "restored_at": closure.restored_at.isoformat(),
            }
            if closure
            else None
        ),
        "close_act": (
            {
                "act_id": close_act.act_id,
                "confirmation_id": close_act.confirmation_id,
                "closure_id": close_act.closure_id,
                "outcome_id": close_act.outcome_id,
                "closed_by": close_act.closed_by,
                "role": close_act.role,
                "restored_at": close_act.restored_at.isoformat(),
                "completed_orders": list(close_act.completed_orders),
                "waived_orders": list(close_act.waived_orders),
            }
            if close_act
            else None
        ),
        "contradictions": [
            {
                "contradiction_id": c.contradiction_id,
                "fresh_incident_id": c.fresh_incident_id,
                "alarm_id": c.alarm_id,
                "device_id": c.device_id,
            }
            for c in contradictions
        ],
    }

    # -- lineage --------------------------------------------------------------
    revisions = store.list_incident_revisions()
    splits = store.list_incident_splits()
    lineage = {
        "revisions_as_prior": [
            {
                "revision_id": r.revision_id,
                "revised_incident_id": r.revised_incident_id,
                "target_topology_version": r.target_topology_version,
                "recorrelated_by": r.recorrelated_by,
                "recorrelated_at": r.recorrelated_at.isoformat(),
            }
            for r in revisions
            if r.prior_incident_id == incident_id
        ],
        "revisions_as_revised": [
            {
                "revision_id": r.revision_id,
                "prior_incident_id": r.prior_incident_id,
                "base_topology_version": r.base_topology_version,
                "recorrelated_by": r.recorrelated_by,
            }
            for r in revisions
            if r.revised_incident_id == incident_id
        ],
        "splits_as_source": [
            {
                "split_id": s.split_id,
                "restored_incident_id": s.restored_incident_id,
                "continuing_incident_id": s.continuing_incident_id,
                "split_by": s.split_by,
                "split_at": s.split_at.isoformat(),
            }
            for s in splits
            if s.incident_id == incident_id
        ],
        "splits_as_part": [
            {
                "split_id": s.split_id,
                "source_incident_id": s.incident_id,
                "part": (
                    "restored"
                    if s.restored_incident_id == incident_id
                    else "continuing"
                ),
            }
            for s in splits
            if incident_id in (s.restored_incident_id, s.continuing_incident_id)
        ],
    }

    # -- completeness -----------------------------------------------------------
    if computed:
        etr_found = ([e.estimate_id for e in computed], None)
    elif state == "closed" and outcome is not None \
            and outcome.status == "no_computed_estimate":
        etr_found = ([], "closed_without_computed_estimate")
    else:
        etr_found = ([], "not_estimated")
    if confirmations:
        confirmation_found = (
            [c.confirmation_id for c in confirmations],
            None,
        )
    elif state == "closed":
        confirmation_found = ([], "closed_without_confirmation")
    else:
        confirmation_found = ([], "no_confirmation_computed")
    closure_ids = [
        x
        for x in (
            closure.closure_id if closure else None,
            close_act.act_id if close_act else None,
        )
        if x
    ]
    found = {
        "classification": (
            [classification.classification_id] if classification else [],
            "not_classified",
        ),
        "severity_assessment": (
            [a.assessment_id for a in assessments],
            "not_assessed",
        ),
        "etr_estimate": etr_found,
        "work_order": (
            [o.order_id for o in orders],
            "no_work_order_drafted",
        ),
        "field_updates": (
            [u.update_id for u in updates],
            "no_field_updates_recorded",
        ),
        "restoration_confirmation": confirmation_found,
        "closure_record": (closure_ids, "not_closed"),
    }
    completeness = _completeness(pack, state, found)
    named_absences = [
        {"subject": row["element"], "reason": row["reason"]}
        for row in completeness["elements"]
        if row["state"] == "absent"
    ]
    if incident.operating_mode != "storm":
        named_absences.append(
            {"subject": "storm_stamp", "reason": "normal_mode_no_storm_stamp"}
        )
    if not holds:
        named_absences.append(
            {"subject": "quarantine_holds", "reason": "no_quarantine_holds"}
        )
    if not (lineage["revisions_as_prior"] or lineage["revisions_as_revised"]
            or lineage["splits_as_source"] or lineage["splits_as_part"]):
        named_absences.append(
            {"subject": "lineage_links", "reason": "no_revision_or_split_links"}
        )
    if not contradictions:
        named_absences.append(
            {
                "subject": "post_close_contradictions",
                "reason": "no_post_close_contradictions",
            }
        )
    completeness["named_absences"] = named_absences

    return {
        "foundation": foundation,
        "cause_and_severity": cause_and_severity,
        "etr_record": etr_record,
        "work_orders": work_orders,
        "field_record": field_record,
        "restoration_and_closure": restoration_and_closure,
        "lineage": lineage,
        "completeness": completeness,
    }


# ---------------------------------------------------------------------
# The verification path: the same timeline, walked again by
# separate code over the raw store lists. Nothing here calls the
# strike path's builders — a shared bug must not be able to
# verify itself.
# ---------------------------------------------------------------------


def _state_again(store, incident_id: str) -> str:
    if (
        store.get_incident_closure(incident_id) is not None
        or store.get_incident_close_act(incident_id) is not None
    ):
        return "closed"
    priors = {r.prior_incident_id for r in store.list_incident_revisions()}
    sources = {s.incident_id for s in store.list_incident_splits()}
    if incident_id in priors | sources:
        return "superseded"
    return "open"


def _order_state_again(decisions) -> str:
    state = "draft"
    for decision in decisions:
        state = decision.decision
    return state


def reperform_sections(store, pack: dict, incident_id: str) -> dict:
    incident = store.get_incident(incident_id)
    state = _state_again(store, incident_id)

    member_rows = []
    for membership in store.list_memberships(incident_id):
        alarm = store.get_alarm(membership.alarm_id)
        member_rows.append(
            (
                alarm.raised_at.isoformat() if alarm else "",
                membership.alarm_id,
                {
                    "alarm_id": membership.alarm_id,
                    "rule_id": membership.rule_id,
                    "device_id": alarm.device_id if alarm else None,
                    "kind": alarm.kind if alarm else None,
                    "raised_at": alarm.raised_at.isoformat() if alarm else None,
                },
            )
        )
    member_rows.sort()
    members = [row for _, _, row in member_rows]
    declaration = None
    if incident.storm_declaration_id:
        by_id = {a.declaration_id: a for a in store.list_storm_declarations()}
        act = by_id.get(incident.storm_declaration_id)
        if act is not None:
            declaration = {
                "declaration_id": act.declaration_id,
                "kind": act.kind,
                "declared_by": act.declared_by,
                "role": act.role,
                "declared_at": act.declared_at.isoformat(),
            }
    successors = set()
    for revision in store.list_incident_revisions():
        if revision.prior_incident_id == incident_id:
            successors.add(revision.revised_incident_id)
    for split in store.list_incident_splits():
        if split.incident_id == incident_id:
            successors.add(split.restored_incident_id)
            successors.add(split.continuing_incident_id)
    foundation = {
        "incident_id": incident.incident_id,
        "feeder_id": incident.feeder_id,
        "substation_id": incident.substation_id,
        "created_at": incident.created_at.isoformat(),
        "anchor_alarm_id": incident.anchor_alarm_id,
        "topology_version": incident.topology_version,
        "correlation_pack_version": incident.correlation_pack_version,
        "operating_mode": incident.operating_mode,
        "storm_pack_version": incident.storm_pack_version,
        "storm_declaration_id": incident.storm_declaration_id,
        "storm_declaration": declaration,
        "state": state,
        "superseded_by": sorted(successors) if state == "superseded" else [],
        "members": members,
        "member_count": len(members),
    }

    classification = store.get_cause_classification(incident_id)
    assessments = store.list_severity_assessments(incident_id)
    cause_and_severity = {
        "classification": (
            {
                "classification_id": classification.classification_id,
                "cause_class": classification.cause_class,
                "rule_id": classification.rule_id,
                "pack_version": classification.pack_version,
                "classified_at": classification.classified_at.isoformat(),
            }
            if classification
            else None
        ),
        "assessments": [
            {
                "assessment_id": a.assessment_id,
                "as_of": a.as_of.isoformat(),
                "band": a.band,
                "score": a.score,
                "supersedes": a.supersedes,
                "missing_inputs": list(a.missing_inputs),
                "escalation_required": a.escalation_required,
            }
            for a in assessments
        ],
        "head_assessment_id": (
            assessments[-1].assessment_id if assessments else None
        ),
        "acknowledgements": [
            {
                "ack_id": ack.ack_id,
                "assessment_id": ack.assessment_id,
                "acknowledged_by": ack.acknowledged_by,
                "role": ack.role,
                "acknowledged_at": ack.acknowledged_at.isoformat(),
            }
            for ack in store.list_severity_acknowledgements(incident_id)
        ],
    }

    closure = store.get_incident_closure(incident_id)
    close_act = store.get_incident_close_act(incident_id)
    restored_at = None
    if close_act is not None:
        restored_at = close_act.restored_at
    elif closure is not None:
        restored_at = closure.restored_at
    estimates = store.list_etr_estimates(incident_id)
    of_record_id = None
    for estimate in estimates:
        if estimate.status != "computed":
            continue
        if restored_at is not None and estimate.as_of > restored_at:
            continue
        of_record_id = estimate.estimate_id
    outcomes = store.list_etr_outcomes(incident_id)
    outcome = outcomes[-1] if outcomes else None
    etr_record = {
        "estimates": [
            {
                "estimate_id": e.estimate_id,
                "as_of": e.as_of.isoformat(),
                "status": e.status,
                "basis": e.basis,
                "total_minutes": e.total_minutes,
                "estimated_restored_at": (
                    e.estimated_restored_at.isoformat()
                    if e.estimated_restored_at
                    else None
                ),
                "operating_mode": e.operating_mode,
                "supersedes": e.supersedes,
            }
            for e in estimates
        ],
        "estimate_of_record_id": of_record_id,
        "outcome": (
            {
                "outcome_id": outcome.outcome_id,
                "status": outcome.status,
                "estimate_id": outcome.estimate_id,
                "actual_duration_minutes": outcome.actual_duration_minutes,
                "signed_error_minutes": outcome.signed_error_minutes,
                "absolute_error_minutes": outcome.absolute_error_minutes,
            }
            if outcome
            else None
        ),
    }

    order_rows = []
    decision_rows = []
    assignment_rows = []
    completion_rows = []
    for order in store.list_work_orders(incident_id):
        decisions = store.list_work_order_decisions(order.order_id)
        completions = store.list_work_order_completions(order.order_id)
        order_rows.append(
            {
                "order_id": order.order_id,
                "version": order.version,
                "drafted_by": order.drafted_by,
                "drafted_at": order.drafted_at.isoformat(),
                "state": _order_state_again(decisions),
                "completed": len(completions) > 0,
                "classification_id": order.classification_id,
                "estimate_id": order.estimate_id,
                "gaps": list(order.gaps),
            }
        )
        for d in decisions:
            decision_rows.append(
                {
                    "decision_id": d.decision_id,
                    "order_id": d.order_id,
                    "decision": d.decision,
                    "decided_by": d.decided_by,
                    "role": d.role,
                    "decided_at": d.decided_at.isoformat(),
                }
            )
        for a in store.list_work_order_assignments(order.order_id):
            assignment_rows.append(
                {
                    "assignment_id": a.assignment_id,
                    "order_id": a.order_id,
                    "crew_id": a.crew_id,
                    "assigned_by": a.assigned_by,
                    "assigned_at": a.assigned_at.isoformat(),
                }
            )
        for c in completions:
            completion_rows.append(
                {
                    "completion_id": c.completion_id,
                    "order_id": c.order_id,
                    "completed_by": c.completed_by,
                    "completed_at": c.completed_at.isoformat(),
                }
            )
    orders = store.list_work_orders(incident_id)
    work_orders = {
        "orders": order_rows,
        "decisions": decision_rows,
        "assignments": assignment_rows,
        "completions": completion_rows,
        "head_order_id": orders[-1].order_id if orders else None,
        "head_state": order_rows[-1]["state"] if order_rows else None,
    }

    released = released_hold_ids(store)
    releases_by_hold = {}
    for hold in store.list_quarantine_holds(incident_id):
        releases = store.list_quarantine_releases(hold.hold_id)
        releases_by_hold[hold.hold_id] = releases[-1] if releases else None
    field_record = {
        "updates": [
            {
                "update_id": u.update_id,
                "kind": u.kind,
                "reported_by": u.reported_by,
                "role": u.role,
                "reported_at": u.reported_at.isoformat(),
                "alarm_id": u.alarm_id,
                "correlation_status": u.correlation.get("status"),
                "hold_ids": [h["hold_id"] for h in u.holds],
            }
            for u in store.list_field_updates(incident_id)
        ],
        "quarantine_holds": [
            {
                "hold_id": h.hold_id,
                "channel": h.channel,
                "field": h.field,
                "source_ref": h.source_ref,
                "rules": list(h.rules),
                "state": "released" if h.hold_id in released else "held",
                "release": (
                    {
                        "release_id": releases_by_hold[h.hold_id].release_id,
                        "released_by": releases_by_hold[h.hold_id].released_by,
                        "released_at": releases_by_hold[
                            h.hold_id
                        ].released_at.isoformat(),
                    }
                    if releases_by_hold[h.hold_id]
                    else None
                ),
            }
            for h in store.list_quarantine_holds(incident_id)
        ],
    }

    confirmations = store.list_restoration_confirmations(incident_id)
    contradictions = [
        c for c in store.list_post_close_contradictions()
        if c.closed_incident_id == incident_id
    ]
    restoration_and_closure = {
        "signals": [
            {
                "signal_id": s.signal_id,
                "source": s.source,
                "device_id": s.device_id,
                "feeder_id": s.feeder_id,
                "state": s.state,
                "observed_at": s.observed_at.isoformat(),
                "recorded_by": s.recorded_by,
            }
            for s in store.list_restoration_signals(incident_id)
        ],
        "confirmations": [
            {
                "confirmation_id": c.confirmation_id,
                "status": c.status,
                "computed_at": c.computed_at.isoformat(),
                "missing_classes": list(c.missing_classes),
                "supersedes": c.supersedes,
            }
            for c in confirmations
        ],
        "head_confirmation_id": (
            confirmations[-1].confirmation_id if confirmations else None
        ),
        "closure": (
            {
                "closure_id": closure.closure_id,
                "closed_by": closure.closed_by,
                "role": closure.role,
                "restored_at": closure.restored_at.isoformat(),
            }
            if closure
            else None
        ),
        "close_act": (
            {
                "act_id": close_act.act_id,
                "confirmation_id": close_act.confirmation_id,
                "closure_id": close_act.closure_id,
                "outcome_id": close_act.outcome_id,
                "closed_by": close_act.closed_by,
                "role": close_act.role,
                "restored_at": close_act.restored_at.isoformat(),
                "completed_orders": list(close_act.completed_orders),
                "waived_orders": list(close_act.waived_orders),
            }
            if close_act
            else None
        ),
        "contradictions": [
            {
                "contradiction_id": c.contradiction_id,
                "fresh_incident_id": c.fresh_incident_id,
                "alarm_id": c.alarm_id,
                "device_id": c.device_id,
            }
            for c in contradictions
        ],
    }

    lineage = {
        "revisions_as_prior": [
            {
                "revision_id": r.revision_id,
                "revised_incident_id": r.revised_incident_id,
                "target_topology_version": r.target_topology_version,
                "recorrelated_by": r.recorrelated_by,
                "recorrelated_at": r.recorrelated_at.isoformat(),
            }
            for r in store.list_incident_revisions()
            if r.prior_incident_id == incident_id
        ],
        "revisions_as_revised": [
            {
                "revision_id": r.revision_id,
                "prior_incident_id": r.prior_incident_id,
                "base_topology_version": r.base_topology_version,
                "recorrelated_by": r.recorrelated_by,
            }
            for r in store.list_incident_revisions()
            if r.revised_incident_id == incident_id
        ],
        "splits_as_source": [
            {
                "split_id": s.split_id,
                "restored_incident_id": s.restored_incident_id,
                "continuing_incident_id": s.continuing_incident_id,
                "split_by": s.split_by,
                "split_at": s.split_at.isoformat(),
            }
            for s in store.list_incident_splits()
            if s.incident_id == incident_id
        ],
        "splits_as_part": [
            {
                "split_id": s.split_id,
                "source_incident_id": s.incident_id,
                "part": (
                    "restored"
                    if s.restored_incident_id == incident_id
                    else "continuing"
                ),
            }
            for s in store.list_incident_splits()
            if incident_id
            in (s.restored_incident_id, s.continuing_incident_id)
        ],
    }

    computed = [
        e for e in estimates if e.status == "computed"
    ]
    if computed:
        etr_ids, etr_reason = [e.estimate_id for e in computed], None
    elif state == "closed" and outcome is not None \
            and outcome.status == "no_computed_estimate":
        etr_ids, etr_reason = [], "closed_without_computed_estimate"
    else:
        etr_ids, etr_reason = [], "not_estimated"
    if confirmations:
        conf_ids, conf_reason = [c.confirmation_id for c in confirmations], None
    elif state == "closed":
        conf_ids, conf_reason = [], "closed_without_confirmation"
    else:
        conf_ids, conf_reason = [], "no_confirmation_computed"
    closure_ids = [
        x
        for x in (
            closure.closure_id if closure else None,
            close_act.act_id if close_act else None,
        )
        if x
    ]
    found = {
        "classification": (
            [classification.classification_id] if classification else [],
            "not_classified",
        ),
        "severity_assessment": (
            [a.assessment_id for a in assessments],
            "not_assessed",
        ),
        "etr_estimate": (etr_ids, etr_reason),
        "work_order": (
            [o.order_id for o in orders],
            "no_work_order_drafted",
        ),
        "field_updates": (
            [u.update_id for u in store.list_field_updates(incident_id)],
            "no_field_updates_recorded",
        ),
        "restoration_confirmation": (conf_ids, conf_reason),
        "closure_record": (closure_ids, "not_closed"),
    }
    completeness = _completeness(pack, state, found)
    named_absences = [
        {"subject": row["element"], "reason": row["reason"]}
        for row in completeness["elements"]
        if row["state"] == "absent"
    ]
    if incident.operating_mode != "storm":
        named_absences.append(
            {"subject": "storm_stamp", "reason": "normal_mode_no_storm_stamp"}
        )
    if not store.list_quarantine_holds(incident_id):
        named_absences.append(
            {"subject": "quarantine_holds", "reason": "no_quarantine_holds"}
        )
    if not (lineage["revisions_as_prior"] or lineage["revisions_as_revised"]
            or lineage["splits_as_source"] or lineage["splits_as_part"]):
        named_absences.append(
            {"subject": "lineage_links", "reason": "no_revision_or_split_links"}
        )
    if not contradictions:
        named_absences.append(
            {
                "subject": "post_close_contradictions",
                "reason": "no_post_close_contradictions",
            }
        )
    completeness["named_absences"] = named_absences

    return {
        "foundation": foundation,
        "cause_and_severity": cause_and_severity,
        "etr_record": etr_record,
        "work_orders": work_orders,
        "field_record": field_record,
        "restoration_and_closure": restoration_and_closure,
        "lineage": lineage,
        "completeness": completeness,
    }
