"""Restoration confirmation, the gated close, splits, post-close
contradictions and the incident event log — Round 7.

Rounds 1–6 built the incident: correlation, severity, priors,
ETR, work orders, field updates, re-correlation. Round 4 added a
minimal closure act — who closed the incident, when supply was
restored, the resolution facts — because the ETR accuracy ledger
and the memory port needed the fact of a close. What nothing in
Rounds 1–6 did was *earn* the close: no artefact anywhere said
the restoration was real. This module is that earning, in four
parts:

- **Restoration is confirmed from signals, in code.** A
  restoration signal is a reading recorded against the incident
  — per-device readings from the meter/OMS-shaped sources, a
  feeder supply reading from the topology-shaped layer. The
  **confirmation** is computed from those signals, the incident's
  field updates (the crew-confirmation class) and its pinned
  topology: every evidence class the restoration pack requires
  is named present or absent with the artefact ids behind the
  verdict, restored vs outstanding customers are summed device
  by device, and the artefact reads ``complete`` only when every
  class is present and every device in the outage set is
  restored. Anything less reads ``partial`` — and a partial
  confirmation cannot close an incident.
- **The close is a gate, not a form.** An incident closes only
  through a named operator's close act binding (a) a complete
  confirmation, (b) the R4 closure and its ETR-vs-actual outcome
  — the accuracy-ledger entry, linked by id, never duplicated —
  and (c) the work-order position: the current work order
  approved and its completion recorded, or explicitly waived by
  the closer with a reason. Open items refuse the close, named.
- **A partial restoration can be split.** When only part of the
  outage set is restored, a named act divides the incident into
  a restored part (closable on its own evidence — a split part
  inherits the source incident's signals and crew updates
  through the split link) and a continuing incident holding the
  outstanding devices. The source incident is never edited, and
  it can never be closed whole on partial evidence.
- **A closed incident is never reopened.** A post-close alarm on
  a closed incident's device set contradicts the recorded
  restoration, so it is recorded as a **post-close contradiction**
  and opens a fresh incident linked back — the R6 rule that
  closed incidents refuse re-correlation is untouched, and the
  closed incident's own record does not change by one byte.

The **event log** closes the round: a regulatory-style export of
one incident's whole chain — alarms, correlation, severity and
ETR history, work orders and their acts, field updates,
restoration, close — assembled only from artefacts of record in
timestamp order, each line carrying its artefact id, with an
independent verifier that re-performs the export against the
store.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from grid_triage.access import (
    ACT_CONFIRM_RESTORATION,
    ACT_GATED_CLOSE,
    ACT_RECORD_SIGNAL,
    ACT_SPLIT_INCIDENT,
    Principal,
    guard_act,
)
from grid_triage.closure import ClosureRefusal, ClosureService
from grid_triage.evidence import EvidenceChain, canonical_json
from grid_triage.models import (
    Alarm,
    Incident,
    IncidentCloseAct,
    IncidentMembership,
    IncidentSplit,
    PostCloseContradiction,
    RestorationConfirmation,
    RestorationSignal,
)
from grid_triage.quarantine import released_hold_ids
from grid_triage.severity import SeverityService
from grid_triage.topology import TopologyView
from grid_triage.work_orders import WorkOrderService

RESTORATION_PACK = {
    "version": "2026.10.1",
    # The evidence classes a complete confirmation requires.
    # Every class is named on the artefact, present or absent —
    # a class the signals cannot speak for is a gap on the face
    # of the confirmation, never silently assumed.
    "evidence_classes": [
        "meter_restoration",
        "feeder_supply_reading",
        "crew_confirmation",
    ],
    # Which sources may speak for which class, and the states
    # each source's readings carry.
    "signal_sources": {
        "meter": {"states": ["restored", "still_out"]},
        "oms": {"states": ["restored", "still_out"]},
        "topology": {"states": ["supplied", "not_supplied"]},
    },
    "signal_roles": ["dispatcher", "operator"],
    # The control-room roles for the close and split acts — the
    # same vocabulary the work pack uses for approvals.
    "actor_roles": ["operator", "duty_manager"],
}


class RestorationRefusal(RuntimeError):
    """A restoration, close or split act the record does not
    allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _hash_id(prefix: str, body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"{prefix}-{digest[:16]}"


class RestorationService:
    """Store-facing restoration work: signals, the computed
    confirmation, the gated close, splits and contradictions."""

    def __init__(
        self,
        store,
        chain: EvidenceChain,
        closure: ClosureService,
        work_orders: WorkOrderService,
        severity: SeverityService,
        cause=None,
        etr=None,
        pack: dict = RESTORATION_PACK,
        storm=None,
    ):
        # Round 8: the storm state fold, when wired. Incidents
        # this service brings into being — split parts and
        # post-close fresh incidents — are stamped with the mode
        # in force at the act that creates them.
        self.storm = storm
        self.store = store
        self.chain = chain
        self.closure = closure
        self.work_orders = work_orders
        self.severity = severity
        self.cause = cause
        self.etr = etr
        self.pack = pack

    # -- signals ----------------------------------------------------
    def record_signal(
        self,
        incident_id: str,
        source: str,
        recorded_by: str,
        role: str,
        observed_at: datetime,
        state: str,
        device_id: str | None = None,
        feeder_id: str | None = None,
        principal: Principal | None = None,
    ) -> RestorationSignal:
        guard_act(
            self,
            principal,
            ACT_RECORD_SIGNAL,
            target=incident_id,
            actor=recorded_by,
            act_role=role,
            at=observed_at,
        )
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        problems: list[str] = []
        spec = self.pack["signal_sources"].get(source)
        if spec is None:
            problems.append(
                f"signal source {source!r} is not in the restoration pack "
                f"(sources: {', '.join(sorted(self.pack['signal_sources']))})"
            )
        elif state not in spec["states"]:
            problems.append(
                f"state {state!r} is not a state a {source} signal carries "
                f"(states: {', '.join(spec['states'])})"
            )
        if not recorded_by or not recorded_by.strip():
            problems.append("a signal names who recorded it")
        if role not in self.pack["signal_roles"]:
            problems.append(
                f"role {role!r} may not record restoration signals "
                f"(allowed: {', '.join(self.pack['signal_roles'])})"
            )
        if source in ("meter", "oms") and not device_id:
            problems.append(f"a {source} signal names the device it reads")
        if source == "topology" and not feeder_id:
            problems.append("a topology signal names the feeder it reads")
        if problems:
            raise RestorationRefusal(problems)
        body = {
            "incident_id": incident_id,
            "pack_version": self.pack["version"],
            "source": source,
            "device_id": device_id,
            "feeder_id": feeder_id,
            "state": state,
            "observed_at": observed_at.isoformat(),
            "recorded_by": recorded_by,
            "role": role,
        }
        signal = RestorationSignal(signal_id=_hash_id("SIG", body), **body)
        for existing in self.store.list_restoration_signals(incident_id):
            if existing.signal_id == signal.signal_id:
                return existing  # identical re-recording: a no-op
        self.store.add_restoration_signal(signal)
        self.chain.append(
            incident_id,
            "restoration_signal",
            signal.model_dump(mode="json"),
            recorded_on=observed_at,
            item_id=f"EV-{signal.signal_id}",
        )
        return signal

    def list_signals(self, incident_id: str) -> list[RestorationSignal]:
        return self.store.list_restoration_signals(incident_id)

    # -- the split link ----------------------------------------------
    def split_of(self, incident_id: str) -> IncidentSplit | None:
        """The split this incident was divided by, if it was the
        source of one."""
        for split in self.store.list_incident_splits():
            if split.incident_id == incident_id:
                return split
        return None

    def split_part(self, incident_id: str) -> tuple[IncidentSplit, str] | None:
        """(split, side) when this incident is one side of a split."""
        for split in self.store.list_incident_splits():
            if split.restored_incident_id == incident_id:
                return split, "restored"
            if split.continuing_incident_id == incident_id:
                return split, "continuing"
        return None

    def splits_for(self, incident_id: str) -> list[IncidentSplit]:
        return [
            s
            for s in self.store.list_incident_splits()
            if incident_id
            in (s.incident_id, s.restored_incident_id, s.continuing_incident_id)
        ]

    def _evidence_incident_ids(self, incident_id: str) -> list[str]:
        """The incidents whose signals and crew updates speak for
        this incident: itself, plus — for a split part — the
        source incident it was divided from, inherited through
        the split link."""
        part = self.split_part(incident_id)
        if part is not None:
            return [incident_id, part[0].incident_id]
        return [incident_id]

    # -- the confirmation ---------------------------------------------
    def _device_universe(
        self, incident: Incident
    ) -> tuple[list[str] | None, TopologyView | None]:
        """The outage set restoration is accounted over: for a
        split part, the partition the split act recorded; for any
        other incident, the severity slice — the anchor device and
        its descendants under the pinned topology."""
        payload = self.store.get_topology_payload(incident.topology_version)
        view = TopologyView(payload) if payload is not None else None
        part = self.split_part(incident.incident_id)
        if part is not None:
            split, side = part
            devices = (
                split.restored_devices
                if side == "restored"
                else split.continuing_devices
            )
            return sorted(devices), view
        if view is None:
            return None, None
        anchor = self.store.get_alarm(incident.anchor_alarm_id)
        if anchor is None:
            return None, view
        slice_set = view.outage_slice(anchor.device_id)
        if slice_set is None:
            return None, view
        return sorted(slice_set), view

    def _latest_device_states(
        self, incident_ids: list[str]
    ) -> dict[str, RestorationSignal]:
        """Each device's current meter/OMS reading across the
        evidence incidents: the latest by observed time, with an
        incident's own readings winning ties over inherited ones."""
        latest: dict[str, RestorationSignal] = {}
        for incident_id in reversed(incident_ids):
            for signal in self.store.list_restoration_signals(incident_id):
                if signal.source not in ("meter", "oms"):
                    continue
                if signal.device_id is None:
                    continue
                current = latest.get(signal.device_id)
                if current is None or signal.observed_at >= current.observed_at:
                    latest[signal.device_id] = signal
        return latest

    def _latest_feeder_reading(
        self, incident: Incident, incident_ids: list[str]
    ) -> RestorationSignal | None:
        latest: RestorationSignal | None = None
        for incident_id in reversed(incident_ids):
            for signal in self.store.list_restoration_signals(incident_id):
                if signal.source != "topology":
                    continue
                if signal.feeder_id != incident.feeder_id:
                    continue
                if latest is None or signal.observed_at >= latest.observed_at:
                    latest = signal
        return latest

    def _crew_update_ids(self, incident_ids: list[str]) -> list[str]:
        # Round 14: a crew note whose text stands held in the
        # quarantine book counts as ABSENT here — its stored
        # note is None, so it falls out below exactly as a note
        # that was never written does. Once a reviewer releases
        # the hold, the fold of release acts counts the note
        # again: the crew wrote it, and a second person has
        # vouched for it.
        released = released_hold_ids(self.store)
        ids: list[str] = []
        for incident_id in incident_ids:
            for update in self.store.list_field_updates(incident_id):
                if update.kind != "crew_note" or update.role != "field_crew":
                    continue
                if update.note:
                    ids.append(update.update_id)
                elif any(
                    hold["field"] == "note" and hold["hold_id"] in released
                    for hold in update.holds
                ):
                    ids.append(update.update_id)
        return ids

    def confirm(
        self,
        incident_id: str,
        computed_at: datetime,
        principal: Principal | None = None,
    ) -> RestorationConfirmation:
        """Compute the incident's restoration position from the
        recorded signals and store it as a new confirmation
        artefact (an identical recomputation is a no-op)."""
        guard_act(
            self,
            principal,
            ACT_CONFIRM_RESTORATION,
            target=incident_id,
            at=computed_at,
        )
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        evidence_ids = self._evidence_incident_ids(incident_id)
        universe, view = self._device_universe(incident)
        states = self._latest_device_states(evidence_ids)

        classes: dict = {}
        restored_devices: list[str] = []
        outstanding_devices: list[str] = []
        total_customers: int | None = None
        restored_customers = 0
        outstanding_customers = 0
        if universe is None:
            classes["meter_restoration"] = {
                "present": False,
                "reason": (
                    "the outage set cannot be derived — the pinned "
                    f"topology {incident.topology_version} or the anchor "
                    "alarm is not on record"
                ),
                "signal_ids": [],
                "devices_without_readings": [],
            }
            classes["customer_counts"] = {
                "present": False,
                "reason": "no outage set, so no customer accounting",
            }
        else:
            without_readings = [d for d in universe if d not in states]
            restored_devices = [
                d for d in universe if states.get(d) and states[d].state == "restored"
            ]
            outstanding_devices = [
                d for d in universe if d not in restored_devices
            ]
            classes["meter_restoration"] = {
                "present": not without_readings,
                "signal_ids": sorted(
                    states[d].signal_id for d in universe if d in states
                ),
                "devices_without_readings": without_readings,
            }
            counts_known = view is not None and all(
                view.devices.get(d, {}).get("customers") is not None
                for d in universe
            )
            if counts_known:
                assert view is not None
                total_customers = sum(
                    int(view.devices[d]["customers"]) for d in universe
                )
                restored_customers = sum(
                    int(view.devices[d]["customers"]) for d in restored_devices
                )
                outstanding_customers = sum(
                    int(view.devices[d]["customers"]) for d in outstanding_devices
                )
            else:
                classes["customer_counts"] = {
                    "present": False,
                    "reason": (
                        "the pinned topology carries no customer count "
                        "for every device in the outage set — the counts "
                        "cannot be computed, so they are not stated"
                    ),
                }

        reading = self._latest_feeder_reading(incident, evidence_ids)
        classes["feeder_supply_reading"] = {
            "present": reading is not None and reading.state == "supplied",
            "signal_ids": [reading.signal_id] if reading else [],
            "feeder_id": incident.feeder_id,
            "latest_state": reading.state if reading else None,
        }
        crew_ids = self._crew_update_ids(evidence_ids)
        classes["crew_confirmation"] = {
            "present": bool(crew_ids),
            "update_ids": crew_ids,
        }

        missing = [name for name, entry in classes.items() if not entry["present"]]
        complete = (
            not missing
            and universe is not None
            and not outstanding_devices
            and total_customers is not None
        )
        prior = self.confirmation(incident_id)
        body = {
            "incident_id": incident_id,
            "pack_version": self.pack["version"],
            "status": "complete" if complete else "partial",
            "classes": classes,
            "missing_classes": missing,
            "restored_devices": restored_devices,
            "outstanding_devices": outstanding_devices,
            "restored_customers": restored_customers,
            "outstanding_customers": outstanding_customers,
            "total_customers": total_customers,
            "computed_at": computed_at.isoformat(),
            "supersedes": prior.confirmation_id if prior else None,
        }
        candidate = RestorationConfirmation(
            confirmation_id="CONF-pending", **body
        )
        link_free = {"confirmation_id", "supersedes"}
        if prior is not None and prior.model_dump(
            mode="json", exclude=link_free
        ) == candidate.model_dump(mode="json", exclude=link_free):
            return prior  # identical recomputation: a no-op
        confirmation = RestorationConfirmation(
            confirmation_id=_hash_id("CONF", body), **body
        )
        self.store.add_restoration_confirmation(confirmation)
        self.chain.append(
            incident_id,
            "restoration_confirmation",
            confirmation.model_dump(mode="json"),
            recorded_on=computed_at,
            item_id=f"EV-{confirmation.confirmation_id}",
        )
        return confirmation

    def confirmation(
        self, incident_id: str
    ) -> RestorationConfirmation | None:
        confirmations = self.store.list_restoration_confirmations(incident_id)
        return confirmations[-1] if confirmations else None

    # -- the gated close ----------------------------------------------
    def close_incident(
        self,
        incident_id: str,
        closed_by: str,
        role: str,
        restored_at: datetime,
        resolution_summary: str,
        waivers: dict[str, str] | None = None,
        recorded_at: datetime | None = None,
        principal: Principal | None = None,
    ) -> tuple[IncidentCloseAct, object, object, object]:
        # Round 11: the gated close is an act surface — a carried
        # principal is judged at this door (the access pack's
        # gated-close act, the named closer, their own role)
        # before the gate's own preconditions run. The internal
        # call into ClosureService below is an already-judged
        # step and deliberately carries no principal: the Round 4
        # surface's principal-carried gate (RT-17) is for direct
        # callers of that surface.
        guard_act(
            self,
            principal,
            ACT_GATED_CLOSE,
            target=incident_id,
            actor=closed_by,
            act_role=role,
            at=recorded_at,
        )
        """Close an incident through the gate. Returns (close act,
        R4 closure, ETR outcome, memory record) — the R4 artefacts
        are produced by the R4 closure service itself; this act
        binds them by id."""
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        waivers = dict(waivers or {})
        recorded_at = recorded_at or restored_at
        problems: list[str] = []
        if not closed_by or not closed_by.strip():
            problems.append("a close names the operator closing the incident")
        if role not in self.pack["actor_roles"]:
            problems.append(
                f"role {role!r} may not close an incident (allowed: "
                f"{', '.join(self.pack['actor_roles'])})"
            )
        if not resolution_summary or not resolution_summary.strip():
            problems.append("a close records the resolution facts")
        existing = self.store.get_incident_closure(incident_id)
        if existing is not None:
            problems.append(
                f"incident {incident_id} is already closed (closure "
                f"{existing.closure_id} recorded by {existing.closed_by})"
            )
        confirmation = self.confirmation(incident_id)
        if confirmation is None:
            problems.append(
                f"no restoration confirmation has been computed for "
                f"incident {incident_id} — restoration is confirmed from "
                "signals first, and the close binds that confirmation"
            )
        elif confirmation.status != "complete":
            detail = (
                f"restoration confirmation {confirmation.confirmation_id} "
                "is partial"
            )
            if confirmation.missing_classes:
                detail += (
                    " — missing evidence classes: "
                    + ", ".join(confirmation.missing_classes)
                )
            if confirmation.outstanding_devices:
                detail += (
                    f" — {len(confirmation.outstanding_devices)} devices "
                    f"still out ({', '.join(confirmation.outstanding_devices)}), "
                    f"{confirmation.outstanding_customers} customers "
                    "unrestored; a partial restoration is split, never "
                    "closed whole"
                )
            problems.append(detail)

        completed_orders: list[str] = []
        waived_orders: list[dict] = []
        head = self.work_orders.head(incident_id)
        if head is not None:
            state = self.work_orders.state_of(head)
            if state == "rejected":
                if head.order_id in waivers:
                    problems.append(
                        f"work order {head.order_id} is rejected — there "
                        "is no open work to waive"
                    )
            elif self.work_orders.completion_for(head.order_id) is not None:
                completed_orders.append(head.order_id)
                if head.order_id in waivers:
                    problems.append(
                        f"work order {head.order_id} is completed — a "
                        "completed order cannot also be waived"
                    )
            elif head.order_id in waivers:
                reason = waivers[head.order_id]
                if not reason or not reason.strip():
                    problems.append(
                        f"waiving work order {head.order_id} states a reason"
                    )
                else:
                    waived_orders.append(
                        {"order_id": head.order_id, "reason": reason}
                    )
            else:
                problems.append(
                    f"work order {head.order_id} is {state} with no "
                    "completion recorded — complete it, or waive it here "
                    "with a reason"
                )
        for order_id in waivers:
            if head is None or order_id != head.order_id:
                problems.append(
                    f"waiver names work order {order_id}, which is not "
                    f"incident {incident_id}'s current work order"
                )
        if problems:
            raise RestorationRefusal(problems)
        assert confirmation is not None
        try:
            closure, outcome, record = self.closure.close_incident(
                incident_id,
                closed_by,
                role,
                restored_at,
                resolution_summary,
                recorded_at,
            )
        except ClosureRefusal as exc:
            raise RestorationRefusal(exc.problems) from exc
        body = {
            "incident_id": incident_id,
            "pack_version": self.pack["version"],
            "confirmation_id": confirmation.confirmation_id,
            "closure_id": closure.closure_id,
            "outcome_id": outcome.outcome_id,
            "memory_record_id": record.record_id,
            "closed_by": closed_by,
            "role": role,
            "restored_at": restored_at.isoformat(),
            "completed_orders": completed_orders,
            "waived_orders": waived_orders,
            "closed_at": recorded_at.isoformat(),
        }
        act = IncidentCloseAct(act_id=_hash_id("CLOSEACT", body), **body)
        self.store.add_incident_close_act(act)
        self.chain.append(
            incident_id,
            "incident_close_act",
            act.model_dump(mode="json"),
            recorded_on=recorded_at,
            item_id=f"EV-{act.act_id}",
        )
        return act, closure, outcome, record

    def close_act(self, incident_id: str) -> IncidentCloseAct | None:
        return self.store.get_incident_close_act(incident_id)

    # -- the split ------------------------------------------------------
    def split_incident(
        self,
        incident_id: str,
        split_by: str,
        role: str,
        split_at: datetime,
        principal: Principal | None = None,
    ) -> IncidentSplit:
        guard_act(
            self,
            principal,
            ACT_SPLIT_INCIDENT,
            target=incident_id,
            actor=split_by,
            act_role=role,
            at=split_at,
        )
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        problems: list[str] = []
        if not split_by or not split_by.strip():
            problems.append("a split names the operator dividing the incident")
        if role not in self.pack["actor_roles"]:
            problems.append(
                f"role {role!r} may not split an incident (allowed: "
                f"{', '.join(self.pack['actor_roles'])})"
            )
        closure = self.store.get_incident_closure(incident_id)
        if closure is not None:
            problems.append(
                f"incident {incident_id} is closed (closure "
                f"{closure.closure_id}) — a closed incident is final and "
                "is never split"
            )
        part = self.split_part(incident_id)
        if part is not None and part[1] == "restored":
            problems.append(
                f"incident {incident_id} is the restored part of split "
                f"{part[0].split_id} — it is fully restored by "
                "construction and has nothing to split off"
            )
        source_split = self.split_of(incident_id)
        if source_split is not None:
            problems.append(
                f"incident {incident_id} was already split (split "
                f"{source_split.split_id}) — split its continuing "
                f"incident {source_split.continuing_incident_id} instead"
            )
        confirmation = self.confirmation(incident_id)
        if confirmation is None:
            problems.append(
                f"no restoration confirmation has been computed for "
                f"incident {incident_id} — the split divides what the "
                "confirmation of record states"
            )
        elif confirmation.status == "complete":
            problems.append(
                f"restoration confirmation {confirmation.confirmation_id} "
                "is complete — close the incident; there is nothing "
                "partial to split"
            )
        elif not confirmation.restored_devices:
            problems.append(
                "the confirmation restores no device — there is no "
                "restored part to close off"
            )
        elif not confirmation.outstanding_devices:
            problems.append(
                "the confirmation leaves no device outstanding — the "
                "confirmation is partial on evidence classes only "
                f"({', '.join(confirmation.missing_classes)}), not on "
                "restoration; complete the evidence and close instead"
            )
        if problems:
            raise RestorationRefusal(problems)
        assert confirmation is not None

        restored_set = set(confirmation.restored_devices)
        continuing_set = set(confirmation.outstanding_devices)
        payload = self.store.get_topology_payload(incident.topology_version)
        view = TopologyView(payload) if payload is not None else None

        members = [
            (m, self.store.get_alarm(m.alarm_id))
            for m in self.store.list_memberships(incident_id)
        ]
        members = [(m, a) for m, a in members if a is not None]

        def side_of(device_id: str) -> str:
            if device_id in restored_set:
                return "restored"
            if device_id in continuing_set:
                return "continuing"
            if view is not None:
                ancestors = view.ancestors(device_id)
                if ancestors & restored_set and not ancestors & continuing_set:
                    return "restored"
            return "continuing"

        restored_alarms = [a for _, a in members if side_of(a.device_id) == "restored"]
        continuing_alarms = [
            a for _, a in members if side_of(a.device_id) == "continuing"
        ]
        if not restored_alarms:
            raise RestorationRefusal(
                [
                    "the restored devices carry no member alarms — there "
                    "is no restored incident to form"
                ]
            )
        if not continuing_alarms:
            raise RestorationRefusal(
                [
                    "the outstanding devices carry no member alarms — "
                    "there is no continuing incident to form"
                ]
            )

        def anchor_of(alarms: list[Alarm]) -> Alarm:
            def depth(alarm: Alarm) -> int:
                return len(view.ancestors(alarm.device_id)) if view else 0

            return sorted(
                alarms, key=lambda a: (depth(a), a.raised_at, a.alarm_id)
            )[0]

        restored_id = f"{incident_id}-RESTORED"
        continuing_id = f"{incident_id}-CONT"
        # Round 8: both parts are stamped with the mode in force
        # at the split act — a part divided out under storm mode
        # carries the storm on its face, exactly like an incident
        # correlated under it.
        stamp = (
            self.storm.stamp_for(incident.feeder_id, split_at)
            if self.storm is not None
            else {}
        )
        for new_id, alarms in (
            (restored_id, restored_alarms),
            (continuing_id, continuing_alarms),
        ):
            anchor = anchor_of(alarms)
            self.store.add_incident(
                Incident(
                    incident_id=new_id,
                    feeder_id=incident.feeder_id,
                    substation_id=incident.substation_id,
                    topology_version=incident.topology_version,
                    correlation_pack_version=incident.correlation_pack_version,
                    anchor_alarm_id=anchor.alarm_id,
                    created_at=split_at,
                    **stamp,
                )
            )
            rules = {m.alarm_id: m.rule_id for m, _ in members}
            for alarm in sorted(
                alarms, key=lambda a: (a.raised_at, a.alarm_id)
            ):
                self.store.add_membership(
                    IncidentMembership(
                        incident_id=new_id,
                        alarm_id=alarm.alarm_id,
                        rule_id=rules[alarm.alarm_id],
                    )
                )
                self.chain.append(
                    new_id,
                    "alarm",
                    alarm.model_dump(mode="json"),
                    recorded_on=split_at,
                    item_id=f"EV-{new_id}-{alarm.alarm_id}",
                )
            self.severity.assess(new_id, split_at)

        continuing_classification = None
        if self.cause is not None:
            self.cause.classify(restored_id, split_at)
            continuing_classification = self.cause.classify(
                continuing_id, split_at
            )
        continuing_estimate_id = None
        if self.etr is not None and continuing_classification is not None:
            estimate = self.etr.estimate(continuing_id, split_at)
            continuing_estimate_id = estimate.estimate_id

        body = {
            "incident_id": incident_id,
            "restored_incident_id": restored_id,
            "continuing_incident_id": continuing_id,
            "confirmation_id": confirmation.confirmation_id,
            "pack_version": self.pack["version"],
            "split_by": split_by,
            "role": role,
            "restored_devices": list(confirmation.restored_devices),
            "continuing_devices": list(confirmation.outstanding_devices),
            "restored_customers": confirmation.restored_customers,
            "continuing_customers": confirmation.outstanding_customers,
            "continuing_estimate_id": continuing_estimate_id,
            "split_at": split_at.isoformat(),
        }
        split = IncidentSplit(split_id=_hash_id("SPLIT", body), **body)
        self.store.add_incident_split(split)
        self.chain.append(
            incident_id,
            "incident_split",
            split.model_dump(mode="json"),
            recorded_on=split_at,
            item_id=f"EV-{split.split_id}",
        )
        for new_id in (restored_id, continuing_id):
            self.chain.append(
                new_id,
                "incident_split",
                split.model_dump(mode="json"),
                recorded_on=split_at,
                item_id=f"EV-{new_id}-{split.split_id}",
            )
        return split

    # -- post-close contradictions --------------------------------------
    def handle_post_close_alarm(
        self, closed_incident: Incident, alarm: Alarm, recorded_at: datetime
    ) -> dict:
        """The FieldUpdateService hook: a new alarm has arrived on
        a device of a closed incident's member set. The closed
        incident is not touched — the alarm opens a fresh incident
        and the contradiction is recorded against both."""
        closure = self.store.get_incident_closure(closed_incident.incident_id)
        fresh_id = f"INC-{alarm.alarm_id}"
        if self.store.get_incident(fresh_id) is None:
            payload = self.store.get_topology_payload(
                closed_incident.topology_version
            )
            view = TopologyView(payload) if payload is not None else None
            substation = closed_incident.substation_id
            if view is not None:
                substation = (
                    view.substation_of_feeder(alarm.feeder_id) or substation
                )
            # Round 8: the fresh incident arises from this alarm,
            # so it is stamped with the mode in force at the
            # alarm's own time, like any correlated incident.
            stamp = (
                self.storm.stamp_for(alarm.feeder_id, alarm.raised_at)
                if self.storm is not None
                else {}
            )
            self.store.add_incident(
                Incident(
                    incident_id=fresh_id,
                    feeder_id=alarm.feeder_id,
                    substation_id=substation,
                    topology_version=closed_incident.topology_version,
                    correlation_pack_version=(
                        closed_incident.correlation_pack_version
                    ),
                    anchor_alarm_id=alarm.alarm_id,
                    created_at=alarm.raised_at,
                    **stamp,
                )
            )
            self.store.add_membership(
                IncidentMembership(
                    incident_id=fresh_id,
                    alarm_id=alarm.alarm_id,
                    rule_id="post_close_contradiction",
                )
            )
            self.chain.append(
                fresh_id,
                "alarm",
                alarm.model_dump(mode="json"),
                recorded_on=alarm.raised_at,
                item_id=f"EV-{alarm.alarm_id}",
            )
            self.chain.append(
                fresh_id,
                "correlation_decisions",
                {
                    "status": "post_close_contradiction",
                    "closed_incident_id": closed_incident.incident_id,
                    "alarm_id": alarm.alarm_id,
                    "reason": (
                        "the alarm's device belongs to a closed incident; "
                        "it opens this fresh incident instead of joining "
                        "or reopening the closed one"
                    ),
                },
                recorded_on=recorded_at,
                item_id=f"EV-CORR-{fresh_id}",
            )
            self.severity.assess(fresh_id, recorded_at)
        body = {
            "closed_incident_id": closed_incident.incident_id,
            "fresh_incident_id": fresh_id,
            "alarm_id": alarm.alarm_id,
            "device_id": alarm.device_id,
            "closure_id": closure.closure_id if closure else None,
            "recorded_at": recorded_at.isoformat(),
        }
        contradiction = PostCloseContradiction(
            contradiction_id=_hash_id("PCC", body), **body
        )
        self.store.add_post_close_contradiction(contradiction)
        self.chain.append(
            closed_incident.incident_id,
            "post_close_contradiction",
            contradiction.model_dump(mode="json"),
            recorded_on=recorded_at,
            item_id=f"EV-{contradiction.contradiction_id}",
        )
        self.chain.append(
            fresh_id,
            "post_close_contradiction",
            contradiction.model_dump(mode="json"),
            recorded_on=recorded_at,
            item_id=f"EV-{fresh_id}-{contradiction.contradiction_id}",
        )
        return {
            "status": "post_close_contradiction",
            "topology_version": closed_incident.topology_version,
            "contradiction_id": contradiction.contradiction_id,
            "fresh_incident_id": fresh_id,
            "reason": (
                f"incident {closed_incident.incident_id} is closed "
                f"(closure {closure.closure_id if closure else '?'}) — a "
                f"new alarm on {alarm.device_id} contradicts the recorded "
                f"restoration, so it opens fresh incident {fresh_id}; the "
                "closed incident is not reopened, re-correlated or edited"
            ),
        }

    def contradictions_for(
        self, incident_id: str
    ) -> list[PostCloseContradiction]:
        return [
            c
            for c in self.store.list_post_close_contradictions()
            if incident_id in (c.closed_incident_id, c.fresh_incident_id)
        ]


# ---------------------------------------------------------------------
# The incident event log
# ---------------------------------------------------------------------

_KIND_ORDER = [
    "incident",
    "topology_declaration",
    "alarm",
    "correlation_decisions",
    "severity_assessment",
    "severity_acknowledgement",
    "cause_classification",
    "etr_estimate",
    "brief",
    "evidence_pack",
    "work_order",
    "work_order_assignment",
    "work_order_decision",
    "work_order_completion",
    "notification",
    "field_update",
    "incident_revision",
    "restoration_signal",
    "restoration_confirmation",
    "incident_split",
    "closure",
    "etr_outcome",
    "incident_close_act",
    "post_close_contradiction",
]


class EventLogService:
    """The regulatory-style incident event log: assembled only
    from artefacts of record, in timestamp order, each line
    carrying its artefact id — plus the independent verifier that
    re-performs an export against the store."""

    def __init__(self, store, chain: EvidenceChain):
        self.store = store
        self.chain = chain

    def _collect(self, incident_id: str) -> list[dict]:
        s = self.store
        incident = s.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        rows: list[tuple[datetime, str, str, str]] = []
        rows.append(
            (
                incident.created_at,
                "incident",
                incident.incident_id,
                f"Incident opened on feeder {incident.feeder_id} "
                f"(anchor {incident.anchor_alarm_id}, topology "
                f"{incident.topology_version}, correlation pack "
                f"{incident.correlation_pack_version})",
            )
        )
        for declaration in s.list_topology_declarations(
            version=incident.topology_version
        ):
            rows.append(
                (
                    declaration.declared_at,
                    "topology_declaration",
                    declaration.declaration_id,
                    f"Topology {declaration.version} declared effective "
                    f"{declaration.effective_at.isoformat()} by "
                    f"{declaration.declared_by}",
                )
            )
        for membership in s.list_memberships(incident_id):
            alarm = s.get_alarm(membership.alarm_id)
            if alarm is None:
                continue
            rows.append(
                (
                    alarm.raised_at,
                    "alarm",
                    alarm.alarm_id,
                    f"Alarm: {alarm.kind} on {alarm.device_id} "
                    f"({alarm.severity}) — {alarm.message} "
                    f"[membership rule: {membership.rule_id}]",
                )
            )
        for assessment in s.list_severity_assessments(incident_id):
            rows.append(
                (
                    assessment.as_of,
                    "severity_assessment",
                    assessment.assessment_id,
                    f"Severity {assessment.band} (score "
                    f"{assessment.score}) under pack "
                    f"{assessment.pack_version}",
                )
            )
        for ack in s.list_severity_acknowledgements(incident_id):
            rows.append(
                (
                    ack.acknowledged_at,
                    "severity_acknowledgement",
                    ack.ack_id,
                    f"Severity acknowledged by {ack.acknowledged_by} "
                    f"({ack.role})",
                )
            )
        classification = s.get_cause_classification(incident_id)
        if classification is not None:
            rows.append(
                (
                    classification.classified_at,
                    "cause_classification",
                    classification.classification_id,
                    f"Cause classified {classification.cause_class} "
                    f"(rule: {classification.rule_id})",
                )
            )
        for estimate in s.list_etr_estimates(incident_id):
            rows.append(
                (
                    estimate.as_of,
                    "etr_estimate",
                    estimate.estimate_id,
                    f"ETR {estimate.status}"
                    + (
                        f": restoration "
                        f"{estimate.estimated_restored_at.isoformat()} "
                        f"(basis: {estimate.basis})"
                        if estimate.status == "computed"
                        and estimate.estimated_restored_at
                        else f" (missing: {', '.join(estimate.missing_inputs)})"
                    ),
                )
            )
        for brief in s.list_briefs(incident_id):
            rows.append(
                (
                    brief.assembled_at,
                    "brief",
                    brief.brief_id,
                    f"Brief assembled ({brief.model_profile} profile, "
                    f"{len(brief.statements)} cited statements)",
                )
            )
        for pack in s.list_evidence_packs(incident_id):
            rows.append(
                (
                    pack.sealed_at,
                    "evidence_pack",
                    pack.pack_id,
                    f"Evidence pack v{pack.version} sealed by "
                    f"{pack.sealed_by} ({len(pack.members)} members)",
                )
            )
        for order in s.list_work_orders(incident_id):
            rows.append(
                (
                    order.drafted_at,
                    "work_order",
                    order.order_id,
                    f"Work order v{order.version} drafted by "
                    f"{order.drafted_by} (cause {order.cause_class})",
                )
            )
            for assignment in s.list_work_order_assignments(order.order_id):
                rows.append(
                    (
                        assignment.assigned_at,
                        "work_order_assignment",
                        assignment.assignment_id,
                        f"Crew {assignment.crew_id} assigned by "
                        f"{assignment.assigned_by} for "
                        f"{assignment.window_start.isoformat()} to "
                        f"{assignment.window_end.isoformat()}",
                    )
                )
            for decision in s.list_work_order_decisions(order.order_id):
                rows.append(
                    (
                        decision.decided_at,
                        "work_order_decision",
                        decision.decision_id,
                        f"Work order {decision.decision} by "
                        f"{decision.decided_by} ({decision.role})"
                        + (
                            f" — {decision.reason}"
                            if decision.reason
                            else ""
                        ),
                    )
                )
            for completion in s.list_work_order_completions(order.order_id):
                rows.append(
                    (
                        completion.completed_at,
                        "work_order_completion",
                        completion.completion_id,
                        f"Work completed, recorded by "
                        f"{completion.completed_by} ({completion.role})",
                    )
                )
        for notification in s.list_notifications(incident_id):
            rows.append(
                (
                    notification.created_at,
                    "notification",
                    notification.notification_id,
                    f"Notification draft recorded ({notification.channel}) "
                    "— a draft, not sent",
                )
            )
        for update in s.list_field_updates(incident_id):
            status = update.correlation.get("status")
            rows.append(
                (
                    update.reported_at,
                    "field_update",
                    update.update_id,
                    f"Field update {update.kind} by {update.reported_by} "
                    f"({update.role})"
                    + (f" — correlation: {status}" if status else ""),
                )
            )
        for revision in s.list_incident_revisions():
            if incident_id in (
                revision.prior_incident_id,
                revision.revised_incident_id,
            ):
                rows.append(
                    (
                        revision.recorrelated_at,
                        "incident_revision",
                        revision.revision_id,
                        f"Re-correlated under topology "
                        f"{revision.target_topology_version}: "
                        f"{revision.prior_incident_id} → "
                        f"{revision.revised_incident_id} by "
                        f"{revision.recorrelated_by}",
                    )
                )
        for signal in s.list_restoration_signals(incident_id):
            subject = signal.device_id or signal.feeder_id or ""
            rows.append(
                (
                    signal.observed_at,
                    "restoration_signal",
                    signal.signal_id,
                    f"{signal.source} signal: {subject} {signal.state} "
                    f"(recorded by {signal.recorded_by})",
                )
            )
        for confirmation in s.list_restoration_confirmations(incident_id):
            rows.append(
                (
                    confirmation.computed_at,
                    "restoration_confirmation",
                    confirmation.confirmation_id,
                    f"Restoration {confirmation.status}: "
                    f"{confirmation.restored_customers} of "
                    f"{confirmation.total_customers} customers restored"
                    + (
                        f"; missing classes: "
                        f"{', '.join(confirmation.missing_classes)}"
                        if confirmation.missing_classes
                        else ""
                    ),
                )
            )
        for split in s.list_incident_splits():
            if incident_id in (
                split.incident_id,
                split.restored_incident_id,
                split.continuing_incident_id,
            ):
                rows.append(
                    (
                        split.split_at,
                        "incident_split",
                        split.split_id,
                        f"Split by {split.split_by}: restored part "
                        f"{split.restored_incident_id}, continuing "
                        f"{split.continuing_incident_id}",
                    )
                )
        closure = s.get_incident_closure(incident_id)
        if closure is not None:
            rows.append(
                (
                    closure.recorded_at,
                    "closure",
                    closure.closure_id,
                    f"Closed by {closure.closed_by} ({closure.role}) — "
                    f"restored {closure.restored_at.isoformat()}",
                )
            )
        for outcome in s.list_etr_outcomes(incident_id):
            rows.append(
                (
                    outcome.recorded_at,
                    "etr_outcome",
                    outcome.outcome_id,
                    f"ETR outcome {outcome.status}: actual "
                    f"{outcome.actual_duration_minutes} min"
                    + (
                        f" vs estimated "
                        f"{outcome.estimated_duration_minutes} min "
                        f"(signed error {outcome.signed_error_minutes:+} min)"
                        if outcome.status == "compared"
                        else ""
                    ),
                )
            )
        act = s.get_incident_close_act(incident_id)
        if act is not None:
            rows.append(
                (
                    act.closed_at,
                    "incident_close_act",
                    act.act_id,
                    f"Gated close by {act.closed_by} ({act.role}) binding "
                    f"confirmation {act.confirmation_id}, closure "
                    f"{act.closure_id}, outcome {act.outcome_id}",
                )
            )
        for contradiction in s.list_post_close_contradictions():
            if incident_id in (
                contradiction.closed_incident_id,
                contradiction.fresh_incident_id,
            ):
                rows.append(
                    (
                        contradiction.recorded_at,
                        "post_close_contradiction",
                        contradiction.contradiction_id,
                        f"Post-close contradiction: alarm "
                        f"{contradiction.alarm_id} on "
                        f"{contradiction.device_id} against closed "
                        f"incident {contradiction.closed_incident_id}; "
                        f"fresh incident {contradiction.fresh_incident_id}",
                    )
                )
        rank = {kind: i for i, kind in enumerate(_KIND_ORDER)}
        rows.sort(key=lambda r: (r[0], rank.get(r[1], 99), r[2]))
        return [
            {
                "seq": index + 1,
                "at": at.isoformat(),
                "kind": kind,
                "artefact_id": artefact_id,
                "summary": summary,
            }
            for index, (at, kind, artefact_id, summary) in enumerate(rows)
        ]

    def build_log(self, incident_id: str) -> dict:
        lines = self._collect(incident_id)
        return {
            "incident_id": incident_id,
            "assembled_from": (
                "artefacts of record only — alarms, correlation, "
                "severity and ETR history, work orders and their acts, "
                "field updates, restoration signals and confirmations, "
                "closure and close acts"
            ),
            "line_count": len(lines),
            "chain_valid": self.chain.verify_chain(incident_id),
            "lines": lines,
        }

    def render_text(self, log: dict) -> str:
        out = [
            f"Incident event log — {log['incident_id']}",
            f"Assembled from: {log['assembled_from']}",
            f"Lines: {log['line_count']} · evidence chain valid: "
            f"{log['chain_valid']}",
            "",
        ]
        for line in log["lines"]:
            out.append(
                f"{line['seq']:>3}  {line['at']}  {line['kind']:<26} "
                f"{line['artefact_id']}  {line['summary']}"
            )
        return "\n".join(out) + "\n"

    def verify_log(self, incident_id: str, log: dict) -> list[str]:
        """Re-perform an export against the store of record: every
        line must name an artefact that exists with the timestamp
        the line states, and every artefact of record must appear.
        Returns the list of problems — empty means the export
        re-performs exactly."""
        expected = self._collect(incident_id)
        expected_keys = [
            (line["kind"], line["artefact_id"], line["at"]) for line in expected
        ]
        problems: list[str] = []
        remaining = list(expected_keys)
        for line in log.get("lines", []):
            key = (line.get("kind"), line.get("artefact_id"), line.get("at"))
            if key in remaining:
                remaining.remove(key)
            else:
                problems.append(
                    f"line {line.get('seq')} ({line.get('kind')} "
                    f"{line.get('artefact_id')} at {line.get('at')}) does "
                    "not re-perform against the store"
                )
        for kind, artefact_id, at in remaining:
            problems.append(
                f"missing line: {kind} {artefact_id} at {at} is on record "
                "but absent from the export"
            )
        if not self.chain.verify_chain(incident_id):
            problems.append(
                f"the evidence chain for {incident_id} fails verification"
            )
        return problems
