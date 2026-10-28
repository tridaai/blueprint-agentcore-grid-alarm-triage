"""Work orders — Round 5, Law 5: drafts until a named operator
approves.

Correlation, severity and the ETR engine say what happened, how
much it matters and when supply might return. This module turns
that record into the document a field crew would work from — and
stops there. A work order in this system is:

- **a draft, always, until an act says otherwise.** The draft is
  derived in code: scope from the classification of record and
  the affected devices on the incident's pinned topology,
  safety notes and materials from a versioned **work pack**, the
  crew qualification stated in the roster's skill vocabulary,
  and the R4 ETR estimate pinned by id. What the data cannot
  supply is named in ``gaps`` — an unclassified cause states no
  honest qualification, so the draft says so instead of
  inventing one.
- **content-addressed and never edited.** A change is a new
  version superseding by link; the superseded version stays
  exactly as it was, including when it was the approved one.
- **assigned and decided by recorded acts.** Assignment names a
  crew, an operator and a work window, and is checked in code:
  the crew must carry the draft's stated qualification on the
  roster, and must not already be committed — on the roster
  artefact or on another open work order — over the window.
  Approval is a named operator's act in a role the work pack
  allows, against the *current* version, and never by the
  operator who drafted or last revised that version (a
  system-drafted order carries no human drafter, so any named
  operator may approve it). Rejection requires its reason.
- **notified in draft only.** Approval drafts the field-crew and
  customer notifications through the notification records
  (Law 1's notify shape); there is no send surface — the bodies
  themselves are marked as drafts, and the decision act records
  their ids.

The state of a version (draft / approved / rejected) is derived
from its decision acts, never stored on the draft — so the draft
row an approval pins cannot drift from the artefact approved.

Round 7 adds the **completion act**: a named recording that the
field work on an approved version is done. It changes no derived
state — the gated incident close (``grid_triage/restoration.py``)
reads the decision state and this act together.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime

from grid_triage.cause import CauseService
from grid_triage.etr import EtrService
from grid_triage.evidence import EvidenceChain, canonical_json
from grid_triage.models import (
    Incident,
    Notification,
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderCompletion,
    WorkOrderDecision,
)
from grid_triage.roster import RosterView
from grid_triage.topology import TopologyView

SYSTEM_DRAFTER = "system"

WORK_PACK = {
    "version": "2026.10.1",
    # The roles allowed to approve or reject a work order — the
    # same control-room vocabulary the severity pack uses for its
    # acknowledgement acts.
    "approver_roles": ["operator", "duty_manager"],
    # Per cause class: the scope the draft states, the crew
    # qualification it requires (roster skill vocabulary — the
    # ETR pack's required skills, with lightning read as line
    # inspection work for a line crew), and the materials it
    # lists. `unknown` deliberately states no qualification:
    # there is no honest skill for "not yet diagnosed", so the
    # draft names the gap and cannot be assigned until the
    # incident is classified.
    "classes": {
        "vegetation_contact": {
            "scope": [
                "Clear the vegetation contact at anchor device "
                "{anchor_device_id} on feeder {feeder_id} and inspect "
                "the line section for conductor damage.",
                "Re-inspect every device listed on this order that "
                "reported a fault indication before the order is "
                "closed out.",
            ],
            "required_qualification": "vegetation_clearing",
            "materials": [
                {"item": "vegetation clearing kit", "quantity": 1, "unit": "kit"},
                {"item": "chipper", "quantity": 1, "unit": "unit"},
                {"item": "conductor repair sleeve", "quantity": 2, "unit": "each"},
            ],
        },
        "equipment_failure": {
            "scope": [
                "Isolate the failed plant at anchor device "
                "{anchor_device_id} on feeder {feeder_id}, test it, "
                "and replace or repair it as found.",
                "Record the failed component and its nameplate data "
                "on the order before the order is closed out.",
            ],
            "required_qualification": "equipment_repair",
            "materials": [
                {"item": "replacement plant kit (class stock)", "quantity": 1, "unit": "kit"},
                {"item": "insulating oil sample kit", "quantity": 1, "unit": "kit"},
                {"item": "torque and test set", "quantity": 1, "unit": "set"},
            ],
        },
        "lightning": {
            "scope": [
                "Inspect the line section and surge arresters around "
                "anchor device {anchor_device_id} on feeder "
                "{feeder_id} for strike damage.",
                "Replace any arrester that operated and shows "
                "damage, and record its condition on the order.",
            ],
            "required_qualification": "line_repair",
            "materials": [
                {"item": "surge arrester", "quantity": 2, "unit": "each"},
                {"item": "line inspection kit", "quantity": 1, "unit": "kit"},
            ],
        },
        "unknown": {
            "scope": [
                "Inspect and diagnose at anchor device "
                "{anchor_device_id} on feeder {feeder_id}: the "
                "cause is not yet classified, so this draft covers "
                "inspection only and states no repair.",
            ],
            "required_qualification": None,
            "materials": [
                {"item": "line inspection kit", "quantity": 1, "unit": "kit"},
            ],
        },
    },
    # Draft safety text per device kind — switching, earthing and
    # tagging notes written for the operator who will plan the
    # work. They are statements on a draft document, never
    # instructions this system executes: the system holds no tool
    # that switches, earths or tags anything (Law 1).
    "safety_notes": {
        "feeder_breaker": [
            "Switching: open the feeder breaker and lock it out before any downstream work begins.",
            "Earthing: apply protective earths on both sides of the work section.",
            "Tagging: fit crew-working tags at the breaker and at every normally open point feeding the section.",
        ],
        "recloser": [
            "Switching: place the recloser in non-reclose before work starts downstream.",
            "Earthing: earth the line side of the recloser before touching downstream plant.",
            "Tagging: tag the recloser control cabinet with the order number.",
        ],
        "sectionalizer": [
            "Switching: confirm the sectionalizer has counted out and stands open before approach.",
            "Earthing: apply protective earths on both sides of the sectionalizer.",
            "Tagging: tag the sectionalizer handle with the order number.",
        ],
        "transformer": [
            "Switching: isolate the transformer on both the HV and LV sides before opening any cover.",
            "Earthing: earth the HV bushings and LV terminals before work.",
            "Tagging: tag both isolation points with the order number.",
        ],
        "voltage_regulator": [
            "Switching: bypass and isolate the regulator before any tap-changer work.",
            "Earthing: earth the regulator bushings before opening the tank.",
            "Tagging: tag the bypass switch with the order number.",
        ],
        "line_sensor": [
            "Switching: treat the conductor as energised — sensors are removed under live-line rules only.",
            "Tagging: tag the sensor unit with the order number before removal.",
        ],
        "rtu": [
            "Tagging: tag the RTU cabinet with the order number — telemetry work only; this draft authorises no switching.",
        ],
    },
}

DRAFT = "draft"
APPROVED = "approved"
REJECTED = "rejected"


class WorkOrderRefusal(RuntimeError):
    """A work-order act the record does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _order_id(body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"WO-{digest[:16]}"


def _assignment_id(body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"WOA-{digest[:16]}"


def _decision_id(body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"WOD-{digest[:16]}"


def _completion_id(body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"WOC-{digest[:16]}"


# The roles that may record a completion (Round 7): the crew that
# did the work reports it done; the control room can record it on
# the crew's report. Kept beside the work pack's approver roles —
# completion is a field fact, not an approval.
COMPLETION_ROLES = ["field_crew", "operator", "duty_manager"]


def _notification_id(order_id: str, channel: str, body: str) -> str:
    digest = hashlib.sha256(
        canonical_json({"order_id": order_id, "channel": channel, "body": body})
    ).hexdigest()
    return f"NOTIF-{digest[:16]}"


class WorkOrderService:
    """Store-facing work-order work: draft, revise, assign,
    decide, and the derived-state reads every surface shares."""

    def __init__(
        self,
        store,
        chain: EvidenceChain,
        cause: CauseService,
        etr: EtrService,
        roster: RosterView,
        pack: dict = WORK_PACK,
    ):
        self.store = store
        self.chain = chain
        self.cause = cause
        self.etr = etr
        self.roster = roster
        self.pack = pack

    # -- derivation ---------------------------------------------------
    def _derive(self, incident: Incident, as_of: datetime) -> dict:
        """Everything a draft states, derived from the records of
        the incident — classification of record, estimate of
        record, pinned topology — with every unsuppliable input
        named in ``gaps``."""
        classification = self.cause.classify(incident.incident_id, as_of)
        estimate = self.etr.current(incident.incident_id)
        if estimate is None:
            estimate = self.etr.estimate(incident.incident_id, as_of)
        payload = self.store.get_topology_payload(incident.topology_version)
        topology = TopologyView(payload) if payload is not None else None

        gaps: list[str] = []
        device_ids = sorted(
            {
                alarm.device_id
                for alarm in (
                    self.store.get_alarm(m.alarm_id)
                    for m in self.store.list_memberships(incident.incident_id)
                )
                if alarm is not None
            }
        )
        anchor = self.store.get_alarm(incident.anchor_alarm_id)
        anchor_device_id = anchor.device_id if anchor else incident.anchor_alarm_id

        affected: list[dict] = []
        for device_id in device_ids:
            device = topology.devices.get(device_id) if topology else None
            if device is None:
                gaps.append(f"affected_device:{device_id}")
                affected.append(
                    {"device_id": device_id, "kind": None, "name": None}
                )
            else:
                affected.append(
                    {
                        "device_id": device_id,
                        "kind": device.get("kind"),
                        "name": device.get("name"),
                    }
                )

        class_pack = self.pack["classes"].get(
            classification.cause_class, self.pack["classes"]["unknown"]
        )
        scope = [
            line.format(
                anchor_device_id=anchor_device_id, feeder_id=incident.feeder_id
            )
            for line in class_pack["scope"]
        ]

        safety: list[dict] = []
        seen_kinds: list[str] = []
        for device in affected:
            kind = device["kind"]
            if kind is None or kind in seen_kinds:
                continue
            seen_kinds.append(kind)
            notes = self.pack["safety_notes"].get(kind)
            if notes is None:
                gaps.append(f"safety_notes:{kind}")
                continue
            safety.append({"device_kind": kind, "notes": list(notes)})

        qualification = class_pack["required_qualification"]
        if qualification is None:
            gaps.append("required_qualification")

        return {
            "classification_id": classification.classification_id,
            "cause_class": classification.cause_class,
            "estimate_id": estimate.estimate_id if estimate else None,
            "scope_of_work": scope,
            "affected_devices": affected,
            "safety_notes": safety,
            "required_qualification": qualification,
            "materials": copy.deepcopy(class_pack["materials"]),
            "gaps": gaps,
        }

    # -- derived state --------------------------------------------------
    def decision_for(self, order_id: str) -> WorkOrderDecision | None:
        decisions = self.store.list_work_order_decisions(order_id)
        return decisions[-1] if decisions else None

    def state_of(self, order: WorkOrder) -> str:
        decision = self.decision_for(order.order_id)
        if decision is None:
            return DRAFT
        return decision.decision

    def current_assignment(self, order_id: str) -> WorkOrderAssignment | None:
        assignments = self.store.list_work_order_assignments(order_id)
        return assignments[-1] if assignments else None

    def head(self, incident_id: str) -> WorkOrder | None:
        orders = self.store.list_work_orders(incident_id)
        return orders[-1] if orders else None

    def completion_for(self, order_id: str) -> WorkOrderCompletion | None:
        """The completion act recorded against this exact version,
        if any (Round 7). Completion is a separate fact from the
        decision state: ``state_of`` stays the decision-derived
        draft/approved/rejected, and this act is what the gated
        incident close reads."""
        completions = self.store.list_work_order_completions(order_id)
        return completions[-1] if completions else None

    def view(self, order: WorkOrder) -> dict:
        """The surface shape: the pinned artefact plus its derived
        state, current assignment and decision, read from the acts."""
        body = order.model_dump(mode="json")
        assignment = self.current_assignment(order.order_id)
        decision = self.decision_for(order.order_id)
        completion = self.completion_for(order.order_id)
        body["state"] = self.state_of(order)
        body["assignment"] = (
            assignment.model_dump(mode="json") if assignment else None
        )
        body["decision"] = decision.model_dump(mode="json") if decision else None
        body["completion"] = (
            completion.model_dump(mode="json") if completion else None
        )
        return body

    def list_by_state(self, state: str | None = None) -> list[WorkOrder]:
        orders = self.store.list_all_work_orders()
        if state is None:
            return orders
        return [o for o in orders if self.state_of(o) == state]

    # -- acts -----------------------------------------------------------
    def draft(
        self, incident_id: str, as_of: datetime, drafted_by: str = SYSTEM_DRAFTER
    ) -> WorkOrder:
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        derived = self._derive(incident, as_of)
        body = {
            "incident_id": incident_id,
            "version": 1,
            "supersedes": None,
            "drafted_by": drafted_by,
            "drafted_at": as_of.isoformat(),
            "work_pack_version": self.pack["version"],
            "operator_note": None,
            **derived,
        }
        candidate = WorkOrder(order_id=_order_id(body), **body)
        existing = self.store.list_work_orders(incident_id)
        if existing:
            head = existing[-1]
            link_free = {"order_id", "version", "supersedes"}
            if head.version == 1 and head.model_dump(
                exclude=link_free
            ) == candidate.model_dump(exclude=link_free):
                return head  # identical re-draft: a no-op, not a version
            raise WorkOrderRefusal(
                [
                    f"incident {incident_id} already has work order "
                    f"{head.order_id} (version {head.version}, "
                    f"{self.state_of(head)}) — revise it to change the draft"
                ]
            )
        self.store.add_work_order(candidate)
        self.chain.append(
            incident_id,
            "work_order",
            candidate.model_dump(mode="json"),
            recorded_on=as_of,
            item_id=f"EV-{candidate.order_id}",
        )
        return candidate

    def revise(
        self,
        order_id: str,
        revised_by: str,
        as_of: datetime,
        operator_note: str | None = None,
        extra_materials: list[dict] | None = None,
    ) -> WorkOrder:
        parent = self.store.get_work_order(order_id)
        if parent is None:
            raise KeyError(f"no such work order: {order_id}")
        problems: list[str] = []
        if not revised_by or not revised_by.strip():
            problems.append("a revision names the operator making it")
        head = self.head(parent.incident_id)
        if head is None or head.order_id != order_id:
            problems.append(
                f"work order {order_id} is not the current version — "
                "only the current version can be revised"
            )
        if problems:
            raise WorkOrderRefusal(problems)
        body = parent.model_dump(mode="json")
        body.pop("order_id")
        body.update(
            {
                "version": parent.version + 1,
                "supersedes": parent.order_id,
                "drafted_by": revised_by,
                "drafted_at": as_of.isoformat(),
                "operator_note": (
                    operator_note
                    if operator_note is not None
                    else parent.operator_note
                ),
                "materials": copy.deepcopy(parent.materials)
                + copy.deepcopy(extra_materials or []),
            }
        )
        candidate = WorkOrder(order_id=_order_id(body), **body)
        self.store.add_work_order(candidate)
        self.chain.append(
            parent.incident_id,
            "work_order",
            candidate.model_dump(mode="json"),
            recorded_on=as_of,
            item_id=f"EV-{candidate.order_id}",
        )
        return candidate

    def assign(
        self,
        order_id: str,
        crew_id: str,
        assigned_by: str,
        window_start: datetime,
        window_end: datetime,
        assigned_at: datetime,
    ) -> WorkOrderAssignment:
        order = self.store.get_work_order(order_id)
        if order is None:
            raise KeyError(f"no such work order: {order_id}")
        problems: list[str] = []
        if not assigned_by or not assigned_by.strip():
            problems.append("an assignment names its operator")
        head = self.head(order.incident_id)
        if head is None or head.order_id != order_id:
            problems.append(
                f"work order {order_id} is not the current version — "
                "only the current version can be assigned"
            )
        elif self.state_of(order) != DRAFT:
            problems.append(
                f"work order {order_id} is {self.state_of(order)} — "
                "only a draft can be assigned"
            )
        if window_end <= window_start:
            problems.append("the work window must end after it starts")
        if order.required_qualification is None:
            problems.append(
                "the draft states no required qualification (gap: "
                "required_qualification) — it cannot be assigned until "
                "the incident is classified"
            )
        crew = self.roster.crew(crew_id)
        if crew is None:
            problems.append(f"no such crew on the roster: {crew_id}")
        elif (
            order.required_qualification is not None
            and order.required_qualification not in crew.get("skills", [])
        ):
            problems.append(
                f"crew {crew_id} does not carry the required qualification "
                f"{order.required_qualification!r} (roster skills: "
                f"{', '.join(crew.get('skills', []))})"
            )
        if crew is not None:
            for commitment in crew.get("assignments", []):
                busy_until = datetime.fromisoformat(commitment["busy_until"])
                if window_start < busy_until:
                    problems.append(
                        f"crew {crew_id} is already committed to "
                        f"{commitment['incident_id']} until "
                        f"{commitment['busy_until']} (roster)"
                    )
            for other in self.store.list_all_work_orders():
                if other.order_id == order_id:
                    continue
                head = self.head(other.incident_id)
                if head is None or head.order_id != other.order_id:
                    continue  # a superseded version's assignment died with it
                if self.state_of(other) not in (DRAFT, APPROVED):
                    continue
                current = self.current_assignment(other.order_id)
                if current is None or current.crew_id != crew_id:
                    continue
                if window_start < current.window_end and (
                    current.window_start < window_end
                ):
                    problems.append(
                        f"crew {crew_id} is already assigned to work order "
                        f"{other.order_id} (assignment {current.assignment_id}) "
                        f"over {current.window_start.isoformat()} to "
                        f"{current.window_end.isoformat()}"
                    )
        if problems:
            raise WorkOrderRefusal(problems)
        previous = self.current_assignment(order_id)
        body = {
            "order_id": order_id,
            "incident_id": order.incident_id,
            "crew_id": crew_id,
            "assigned_by": assigned_by,
            "assigned_at": assigned_at.isoformat(),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "supersedes": previous.assignment_id if previous else None,
        }
        assignment = WorkOrderAssignment(
            assignment_id=_assignment_id(body), **body
        )
        self.store.add_work_order_assignment(assignment)
        self.chain.append(
            order.incident_id,
            "work_order_assignment",
            assignment.model_dump(mode="json"),
            recorded_on=assigned_at,
            item_id=f"EV-{assignment.assignment_id}",
        )
        return assignment

    def decide(
        self,
        order_id: str,
        decision: str,
        decided_by: str,
        role: str,
        decided_at: datetime,
        reason: str | None = None,
    ) -> tuple[WorkOrderDecision, list[Notification]]:
        order = self.store.get_work_order(order_id)
        if order is None:
            raise KeyError(f"no such work order: {order_id}")
        problems: list[str] = []
        if decision not in (APPROVED, REJECTED):
            problems.append(
                f"a decision is {APPROVED!r} or {REJECTED!r}, not {decision!r}"
            )
        if not decided_by or not decided_by.strip():
            problems.append("a decision names its operator")
        if role not in self.pack["approver_roles"]:
            problems.append(
                f"role {role!r} may not decide a work order (allowed: "
                f"{', '.join(self.pack['approver_roles'])})"
            )
        head = self.head(order.incident_id)
        if head is None or head.order_id != order_id:
            problems.append(
                f"work order {order_id} is not the current version — "
                "a decision pins the current version, never a superseded one"
            )
        elif self.state_of(order) != DRAFT:
            problems.append(
                f"work order {order_id} is already {self.state_of(order)} — "
                "a decided version cannot be decided again"
            )
        assignment = self.current_assignment(order_id)
        if decision == APPROVED and assignment is None:
            problems.append(
                "an approval requires a crew assignment — an approved "
                "order with no crew assigned sends nobody"
            )
        if (
            decision == APPROVED
            and order.drafted_by != SYSTEM_DRAFTER
            and decided_by == order.drafted_by
        ):
            problems.append(
                f"{decided_by} drafted or last revised this version — "
                "the approver must be a different operator"
            )
        if decision == REJECTED and (not reason or not reason.strip()):
            problems.append("a rejection states its reason")
        if problems:
            raise WorkOrderRefusal(problems)

        notifications: list[Notification] = []
        if decision == APPROVED:
            notifications = self._draft_notifications(
                order, assignment, decided_by, decided_at
            )
        body = {
            "order_id": order_id,
            "incident_id": order.incident_id,
            "decision": decision,
            "decided_by": decided_by,
            "role": role,
            "reason": reason,
            "decided_at": decided_at.isoformat(),
            "notification_ids": [n.notification_id for n in notifications],
        }
        act = WorkOrderDecision(decision_id=_decision_id(body), **body)
        self.store.add_work_order_decision(act)
        for notification in notifications:
            self.store.add_notification(notification)
        self.chain.append(
            order.incident_id,
            "work_order_decision",
            act.model_dump(mode="json"),
            recorded_on=decided_at,
            item_id=f"EV-{act.decision_id}",
        )
        return act, notifications

    def complete(
        self,
        order_id: str,
        completed_by: str,
        role: str,
        completed_at: datetime,
        note: str | None = None,
    ) -> WorkOrderCompletion:
        """Record that the field work on an approved order is done
        (Round 7). A named act against the current, approved
        version only: a draft has no work to complete, a rejected
        version never had any, a superseded version's completion
        would pin the wrong artefact, and a completion is recorded
        once — the fact does not get recorded twice."""
        order = self.store.get_work_order(order_id)
        if order is None:
            raise KeyError(f"no such work order: {order_id}")
        problems: list[str] = []
        if not completed_by or not completed_by.strip():
            problems.append("a completion names who records it")
        if role not in COMPLETION_ROLES:
            problems.append(
                f"role {role!r} may not record a completion (allowed: "
                f"{', '.join(COMPLETION_ROLES)})"
            )
        head = self.head(order.incident_id)
        if head is None or head.order_id != order_id:
            problems.append(
                f"work order {order_id} is not the current version — "
                "only the current version can be completed"
            )
        elif self.state_of(order) != APPROVED:
            problems.append(
                f"work order {order_id} is {self.state_of(order)} — "
                "only an approved order has field work to complete"
            )
        if self.completion_for(order_id) is not None:
            problems.append(
                f"work order {order_id} already has a completion act "
                "recorded — the fact is recorded once"
            )
        if problems:
            raise WorkOrderRefusal(problems)
        body = {
            "order_id": order_id,
            "incident_id": order.incident_id,
            "completed_by": completed_by,
            "role": role,
            "completed_at": completed_at.isoformat(),
            "note": note,
        }
        completion = WorkOrderCompletion(
            completion_id=_completion_id(body), **body
        )
        self.store.add_work_order_completion(completion)
        self.chain.append(
            order.incident_id,
            "work_order_completion",
            completion.model_dump(mode="json"),
            recorded_on=completed_at,
            item_id=f"EV-{completion.completion_id}",
        )
        return completion

    def _draft_notifications(
        self,
        order: WorkOrder,
        assignment: WorkOrderAssignment | None,
        decided_by: str,
        decided_at: datetime,
    ) -> list[Notification]:
        """The approval's notification drafts (Law 1, notify
        shape): recorded, marked as drafts in their own text, and
        never sent — there is no send surface. The restoration
        sentence follows the ETR basis discipline (Law 4): the
        pinned estimate's own time with its basis label, or its
        named missing inputs and no time at all."""
        incident = self.store.get_incident(order.incident_id)
        feeder_id = incident.feeder_id if incident else order.incident_id
        estimate = (
            self.store.get_etr_estimate(order.estimate_id)
            if order.estimate_id
            else None
        )
        if estimate is not None and estimate.status == "computed":
            etr_sentence = (
                f"Estimated restoration "
                f"{estimate.estimated_restored_at.isoformat()} "
                f"(basis: {estimate.basis})."
            )
        elif estimate is not None:
            etr_sentence = (
                "No restoration estimate: not computable from rates "
                f"(missing: {', '.join(estimate.missing_inputs)})."
            )
        else:
            etr_sentence = "No restoration estimate is pinned to this order."
        scope_line = order.scope_of_work[0] if order.scope_of_work else ""
        crew_line = (
            f"Crew {assignment.crew_id} is assigned "
            f"{assignment.window_start.isoformat()} to "
            f"{assignment.window_end.isoformat()}. "
            if assignment is not None
            else ""
        )
        bodies = [
            (
                "field_crew",
                f"DRAFT notification — not sent. Work order {order.order_id} "
                f"for incident {order.incident_id} on feeder {feeder_id} "
                f"is approved by {decided_by}. {crew_line}"
                f"Scope: {scope_line} {etr_sentence}",
            ),
            (
                "customer_outage",
                f"DRAFT notification — not sent. Outage notice for feeder "
                f"{feeder_id}: repair work under work order {order.order_id} "
                f"has been approved. {etr_sentence}",
            ),
        ]
        return [
            Notification(
                notification_id=_notification_id(order.order_id, channel, body),
                incident_id=order.incident_id,
                channel=channel,
                body=body,
                created_at=decided_at,
            )
            for channel, body in bodies
        ]
