"""The operations board — Round 9.

One computation behind the operator console, the shift
handover and their verifiers: the state of the control room,
assembled **only from artefacts of record**. Every figure on
the board is a read over stored artefacts — incidents and
their memberships, severity assessments, ETR estimates, work
orders and their acts, restoration signals and confirmations,
the storm declaration fold, the ETR accuracy ledger. Nothing
here writes anything, and nothing here invents a figure: a
fact the artefacts do not carry does not appear on the board.

The definitions are stated once, here, so the console and the
handover can never disagree about what they show:

- an incident is **live-open** when it has no closure and no
  gated close act, and it has not been superseded — it is not
  the prior of a re-correlation revision and not the source
  of a restoration split (its successors are the live rows);
- a work order's operative version is its incident's **head**
  (latest) version, and its state is the decision-derived
  state of that version;
- a restoration is **unconfirmed** when signals or a
  confirmation exist for a live-open incident but no complete
  confirmation does — an incident with no signals at all has
  no restoration under way and is not listed.
"""

from __future__ import annotations

from datetime import datetime

from grid_triage.closure import ClosureService
from grid_triage.etr import EtrService
from grid_triage.models import Incident
from grid_triage.storm import StormService
from grid_triage.work_orders import WorkOrderService


def _superseded_ids(store) -> set[str]:
    """Incident ids a later artefact has replaced: revision
    priors and split sources. Their successors carry the live
    record."""
    ids = {r.prior_incident_id for r in store.list_incident_revisions()}
    ids |= {s.incident_id for s in store.list_incident_splits()}
    return ids


def is_closed(store, incident_id: str) -> bool:
    return (
        store.get_incident_closure(incident_id) is not None
        or store.get_incident_close_act(incident_id) is not None
    )


def live_open_incidents(store) -> list[Incident]:
    superseded = _superseded_ids(store)
    return [
        incident
        for incident in store.list_incidents()
        if incident.incident_id not in superseded
        and not is_closed(store, incident.incident_id)
    ]


def open_incident_rows(store) -> list[dict]:
    """The board's incident rows: every live-open incident with
    its current severity (the latest assessment of record, or
    UNASSESSED), its classification and its mode stamp."""
    rows: list[dict] = []
    for incident in live_open_incidents(store):
        history = store.list_severity_assessments(incident.incident_id)
        current = history[-1] if history else None
        classification = store.get_cause_classification(incident.incident_id)
        rows.append(
            {
                "incident_id": incident.incident_id,
                "feeder_id": incident.feeder_id,
                "substation_id": incident.substation_id,
                "created_at": incident.created_at.isoformat(),
                "anchor_alarm_id": incident.anchor_alarm_id,
                "member_alarm_count": len(
                    store.list_memberships(incident.incident_id)
                ),
                "topology_version": incident.topology_version,
                "band": current.band if current else "UNASSESSED",
                "score": current.score if current else None,
                "assessment_id": (
                    current.assessment_id if current else None
                ),
                "cause_class": (
                    classification.cause_class if classification else None
                ),
                "operating_mode": incident.operating_mode,
                "storm_pack_version": incident.storm_pack_version,
                "storm_declaration_id": incident.storm_declaration_id,
            }
        )
    return rows


def pending_approval_rows(store, work_orders: WorkOrderService) -> dict:
    """What is waiting for a named decision: severity
    assessments whose escalation band no acknowledgement act
    has satisfied (the Round 2 queue, folded from the acts),
    and head work-order versions still in draft state."""
    acked = {
        a.assessment_id for a in store.list_severity_acknowledgements()
    }
    acknowledgements: list[dict] = []
    for incident in store.list_incidents():
        history = store.list_severity_assessments(incident.incident_id)
        for assessment in history:
            if (
                assessment.escalation_required
                and assessment.assessment_id not in acked
            ):
                acknowledgements.append(
                    {
                        "assessment_id": assessment.assessment_id,
                        "incident_id": incident.incident_id,
                        "band": assessment.band,
                        "score": assessment.score,
                    }
                )
    decisions: list[dict] = []
    for row in work_order_rows(store, work_orders):
        if row["state"] == "draft":
            decisions.append(
                {
                    "order_id": row["order_id"],
                    "incident_id": row["incident_id"],
                    "version": row["version"],
                    "drafted_by": row["drafted_by"],
                    "assigned_crew_id": row["assigned_crew_id"],
                }
            )
    return {
        "severity_acknowledgements": acknowledgements,
        "work_order_decisions": decisions,
    }


def work_order_rows(store, work_orders: WorkOrderService) -> list[dict]:
    """One row per incident that has work orders: its head
    version, the version's decision-derived state, its current
    assignment and whether a completion act is recorded."""
    rows: list[dict] = []
    for incident in store.list_incidents():
        head = work_orders.head(incident.incident_id)
        if head is None:
            continue
        assignment = work_orders.current_assignment(head.order_id)
        decision = work_orders.decision_for(head.order_id)
        rows.append(
            {
                "order_id": head.order_id,
                "incident_id": head.incident_id,
                "version": head.version,
                "state": work_orders.state_of(head),
                "drafted_by": head.drafted_by,
                "cause_class": head.cause_class,
                "assigned_crew_id": (
                    assignment.crew_id if assignment else None
                ),
                "completed": (
                    work_orders.completion_for(head.order_id) is not None
                ),
                "notifications_drafted": (
                    len(decision.notification_ids) if decision else 0
                ),
            }
        )
    return sorted(rows, key=lambda r: r["order_id"])


def open_work_order_rows(store, work_orders: WorkOrderService) -> list[dict]:
    """The work-order queue: head versions neither rejected nor
    completed — the orders the room is still working."""
    return [
        row
        for row in work_order_rows(store, work_orders)
        if row["state"] in ("draft", "approved") and not row["completed"]
    ]


def unconfirmed_restoration_rows(store) -> list[dict]:
    """Restorations under way but not confirmed complete:
    live-open incidents carrying signals or a confirmation,
    whose latest confirmation is not complete."""
    rows: list[dict] = []
    for incident in live_open_incidents(store):
        signals = store.list_restoration_signals(incident.incident_id)
        confirmations = store.list_restoration_confirmations(
            incident.incident_id
        )
        latest = confirmations[-1] if confirmations else None
        if not signals and latest is None:
            continue  # no restoration under way on this incident
        if latest is not None and latest.status == "complete":
            continue
        rows.append(
            {
                "incident_id": incident.incident_id,
                "confirmation_status": (
                    latest.status if latest else "unconfirmed"
                ),
                "confirmation_id": (
                    latest.confirmation_id if latest else None
                ),
                "signal_count": len(signals),
                "restored_customers": (
                    latest.restored_customers if latest else None
                ),
                "outstanding_customers": (
                    latest.outstanding_customers if latest else None
                ),
                "missing_classes": (
                    list(latest.missing_classes) if latest else []
                ),
            }
        )
    return rows


def storm_section(storm: StormService, at: datetime) -> dict:
    """The storm state at ``at``, folded from the declaration
    acts — the banner the console shows and the section the
    handover carries."""
    windows = storm.active_windows(at)
    return {"active": bool(windows), "at": at.isoformat(), "windows": windows}


class BoardService:
    """The board computation. Read-only: it holds the services
    whose derived states the board states, and writes nothing."""

    def __init__(
        self,
        store,
        work_orders: WorkOrderService,
        etr: EtrService,
        storm: StormService,
        closure: ClosureService,
        topology_payload: dict,
    ):
        self.store = store
        self.work_orders = work_orders
        self.etr = etr
        self.storm = storm
        self.closure = closure
        self.topology_payload = topology_payload

    # -- the shared sections (console + handover) -------------------
    def sections(self, at: datetime) -> dict:
        """The sections the shift handover carries, computed
        here so a handover and the board are the same figures
        by construction."""
        return {
            "open_incidents": open_incident_rows(self.store),
            "pending_approvals": pending_approval_rows(
                self.store, self.work_orders
            ),
            "open_work_orders": open_work_order_rows(
                self.store, self.work_orders
            ),
            "unconfirmed_restorations": unconfirmed_restoration_rows(
                self.store
            ),
            "storm": storm_section(self.storm, at),
            "etr_accuracy": self.closure.accuracy_ledger(),
        }

    # -- console-only views -------------------------------------------
    def etr_rows(self) -> list[dict]:
        """The ETR board: every live-open incident's current
        estimate, with its basis on the row — a not-computable
        estimate names its missing inputs; no estimate at all
        reads ``no_estimate``. Nothing is extrapolated."""
        rows: list[dict] = []
        for incident in live_open_incidents(self.store):
            history = self.store.list_etr_estimates(incident.incident_id)
            current = history[-1] if history else None
            rows.append(
                {
                    "incident_id": incident.incident_id,
                    "feeder_id": incident.feeder_id,
                    "estimate_id": (
                        current.estimate_id if current else None
                    ),
                    "status": (
                        current.status if current else "no_estimate"
                    ),
                    "basis": current.basis if current else None,
                    "total_minutes": (
                        current.total_minutes if current else None
                    ),
                    "estimated_restored_at": (
                        current.estimated_restored_at.isoformat()
                        if current and current.estimated_restored_at
                        else None
                    ),
                    "operating_mode": (
                        current.operating_mode
                        if current
                        else incident.operating_mode
                    ),
                    "missing_inputs": (
                        list(current.missing_inputs) if current else []
                    ),
                }
            )
        return rows

    def feeder_rows(self, at: datetime) -> list[dict]:
        """The feeder board: the loaded topology's feeders with
        their device counts, live-open incidents, uncorrelated
        alarms and storm state at ``at`` — all counted from the
        artefacts, none from the payload's say-so alone."""
        payload = self.topology_payload
        open_by_feeder: dict[str, int] = {}
        for incident in live_open_incidents(self.store):
            open_by_feeder[incident.feeder_id] = (
                open_by_feeder.get(incident.feeder_id, 0) + 1
            )
        uncorrelated_by_feeder: dict[str, int] = {}
        for alarm in self.store.list_uncorrelated_alarms():
            uncorrelated_by_feeder[alarm.feeder_id] = (
                uncorrelated_by_feeder.get(alarm.feeder_id, 0) + 1
            )
        devices_by_feeder: dict[str, int] = {}
        for device in payload.get("devices", []):
            devices_by_feeder[device["feeder_id"]] = (
                devices_by_feeder.get(device["feeder_id"], 0) + 1
            )
        rows: list[dict] = []
        for feeder in payload.get("feeders", []):
            state = self.storm.state_at(feeder["feeder_id"], at)
            rows.append(
                {
                    "feeder_id": feeder["feeder_id"],
                    "substation_id": feeder["substation_id"],
                    "name": feeder["name"],
                    "device_count": devices_by_feeder.get(
                        feeder["feeder_id"], 0
                    ),
                    "open_incident_count": open_by_feeder.get(
                        feeder["feeder_id"], 0
                    ),
                    "uncorrelated_alarm_count": uncorrelated_by_feeder.get(
                        feeder["feeder_id"], 0
                    ),
                    "operating_mode": state["mode"],
                    "storm_declaration_id": state.get("declaration_id"),
                }
            )
        return rows

    def board(self, at: datetime) -> dict:
        """The whole console payload at ``at``: the shared
        sections plus the console's own ETR and feeder boards
        and the severity counts the board groups by."""
        sections = self.sections(at)
        by_band: dict[str, int] = {}
        for row in sections["open_incidents"]:
            by_band[row["band"]] = by_band.get(row["band"], 0) + 1
        return {
            "generated_at": at.isoformat(),
            "storm": sections["storm"],
            "incidents": sections["open_incidents"],
            "incident_counts_by_band": dict(sorted(by_band.items())),
            "feeders": self.feeder_rows(at),
            "etr": self.etr_rows(),
            "pending_approvals": sections["pending_approvals"],
            "pending_approval_count": (
                len(sections["pending_approvals"]["severity_acknowledgements"])
                + len(sections["pending_approvals"]["work_order_decisions"])
            ),
            "work_orders": sections["open_work_orders"],
            "unconfirmed_restorations": sections[
                "unconfirmed_restorations"
            ],
            "etr_accuracy": sections["etr_accuracy"],
        }
