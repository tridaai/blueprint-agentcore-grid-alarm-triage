"""The ETR engine — Round 4, Law 4: estimates are labelled
estimates.

An ETR (estimated time of restoration) is computed in code from
stated inputs, never pronounced by the model and never guessed:

- **the cause class** — the incident's classification of record
  (Round 3's pack). An ``unknown`` class cannot be estimated:
  there is no honest rate for "we don't know what broke".
- **the repair locus** — the anchor alarm's device; its kind,
  from the incident's pinned topology version, selects the rate.
- **base repair minutes** — a versioned rate pack keyed by
  (cause class × device kind): the historical repair-duration
  base rates, as data.
- **crew wait** — the roster artefact: the earliest a qualified
  crew (the pack names the skill each cause class requires) can
  start, given its shift window and its current commitments.
- **travel / access** — the pack's drive-time table between
  topology substations: the chosen crew's depot to the incident's
  substation.

The crew the estimate rests on is the qualified crew minimising
wait + travel, ties broken by crew id — the choice is recorded,
not vibes. The result is a content-addressed
:class:`EtrEstimate`: each component is a recorded line, the
estimate pins (incident, rate pack version, roster version,
classification), and a recomputation supersedes by link with the
delta and the changed inputs named. When any input is missing
the estimate is ``not_computable`` and names the input — the
system never emits a guessed clock time.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from grid_triage.access import ACT_ESTIMATE_ETR, Principal, guard_act
from grid_triage.cause import UNKNOWN, CauseService
from grid_triage.evidence import EvidenceChain, canonical_json
from grid_triage.models import Alarm, CauseClassification, EtrEstimate, Incident
from grid_triage.roster import RosterView
from grid_triage.storm import STORM_PACK, storm_base_repair
from grid_triage.topology import TopologyView

ETR_PACK = {
    "version": "2026.10.1",
    # The one basis label a computed estimate may carry. A brief
    # that states a restoration time without this label attached
    # is refused (see grid_triage.agent.etr_problems).
    "basis_label": "computed_from_rates",
    # The skill a crew must carry to be estimated against a class.
    # `unknown` is deliberately absent: it has no skill because it
    # has no estimate.
    "required_skill": {
        "vegetation_contact": "vegetation_clearing",
        "equipment_failure": "equipment_repair",
        "lightning": "line_inspection",
    },
    # Historical repair-duration base rates, minutes, keyed by
    # "cause_class|anchor device kind". Gaps are deliberate: a
    # pair with no rate is a named missing input, not a zero.
    "base_minutes": {
        "vegetation_contact|feeder_breaker": 150,
        "vegetation_contact|recloser": 120,
        "vegetation_contact|sectionalizer": 90,
        "vegetation_contact|transformer": 180,
        "vegetation_contact|line_sensor": 60,
        "equipment_failure|feeder_breaker": 200,
        "equipment_failure|sectionalizer": 150,
        "equipment_failure|transformer": 240,
        "equipment_failure|voltage_regulator": 180,
        "lightning|feeder_breaker": 90,
        "lightning|recloser": 75,
        "lightning|transformer": 120,
    },
    # Drive minutes between topology substations: depot -> incident.
    "travel_minutes": {
        "SUB-ALDER": {"SUB-ALDER": 25, "SUB-SOUTH": 55},
        "SUB-SOUTH": {"SUB-ALDER": 55, "SUB-SOUTH": 20},
    },
}

COMPUTED = "computed"
NOT_COMPUTABLE = "not_computable"


class EtrRefusal(RuntimeError):
    """An estimate the record does not allow (Round 10: a
    recomputation whose as_of precedes the standing head's —
    history is insertion-ordered, so appending it would rewind
    the current estimate and poison the closure's choice of the
    estimate of record)."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _estimate_id(body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"ETR-{digest[:16]}"


def _minutes(delta: timedelta) -> float:
    return round(delta.total_seconds() / 60.0, 3)


def _component_minutes(estimate: EtrEstimate | None, name: str) -> int | None:
    if estimate is None:
        return None
    for line in estimate.components:
        if line["name"] == name:
            return line["minutes"]
    return None


def compute_estimate(
    incident: Incident,
    anchor: Alarm | None,
    classification: CauseClassification | None,
    topology: TopologyView | None,
    roster: RosterView | None,
    as_of: datetime,
    pack: dict = ETR_PACK,
    supersedes: str | None = None,
    prior: EtrEstimate | None = None,
    storm: dict | None = None,
    storm_pack: dict = STORM_PACK,
) -> EtrEstimate:
    """Pure ETR computation. Every input the formula needs is
    either used and recorded, or named in ``missing_inputs``.

    ``storm`` — for an incident stamped storm (Round 8) — is the
    stamp context ``{"pack_version", "declaration_id"}``: the
    base repair minutes scale by the storm pack's stated factor
    and the added minutes land as their own component line, so
    the estimate is labelled with the mode on its face."""
    missing: list[str] = []

    cause_class = classification.cause_class if classification else None
    if classification is None or cause_class == UNKNOWN:
        missing.append("cause_class")

    if anchor is None:
        missing.append("anchor_alarm")

    device_kind: str | None = None
    if topology is None:
        missing.append("topology_version")
    elif anchor is not None:
        device = topology.devices.get(anchor.device_id)
        if device is None:
            missing.append("anchor_device_kind")
        else:
            device_kind = device.get("kind")

    if roster is None:
        missing.append("roster_version")

    skill = pack["required_skill"].get(cause_class) if cause_class else None
    crews: list[dict] = []
    if skill and roster is not None:
        crews = roster.qualified(skill)
        if not crews:
            missing.append("qualified_crew")

    # Candidate crews: (wait + travel, crew_id, crew, wait, travel).
    chosen: tuple[int, str, dict, int, int] | None = None
    if crews:
        travel_table = pack["travel_minutes"]
        for crew in crews:
            depot = roster.depot_of(crew)
            travel = travel_table.get(depot, {}).get(incident.substation_id)
            if travel is None:
                continue
            start = roster.available_start(crew, as_of)
            wait = int(_minutes(start - as_of))
            candidate = (wait + travel, crew["crew_id"], crew, wait, travel)
            if chosen is None or candidate[:2] < chosen[:2]:
                chosen = candidate
        if chosen is None:
            missing.append("travel_minutes")

    rate_key = (
        f"{cause_class}|{device_kind}"
        if cause_class and device_kind
        else None
    )
    base = pack["base_minutes"].get(rate_key) if rate_key else None
    if rate_key is not None and base is None and "cause_class" not in missing:
        missing.append("base_rate")

    elapsed = (
        _minutes(as_of - anchor.raised_at)
        if anchor is not None and as_of >= anchor.raised_at
        else None
    )
    if anchor is not None and elapsed is None:
        missing.append("elapsed_minutes")

    inputs = {
        "cause_class": cause_class,
        "classification_id": (
            classification.classification_id if classification else None
        ),
        "anchor_alarm_id": incident.anchor_alarm_id,
        "anchor_device_id": anchor.device_id if anchor else None,
        "device_kind": device_kind,
        "substation_id": incident.substation_id,
        "required_skill": skill,
        "rate_key": rate_key,
        "crew_id": chosen[1] if chosen else None,
        "crew_depot_substation_id": (
            roster.depot_of(chosen[2]) if chosen and roster else None
        ),
        "crew_available_at": (
            roster.available_start(chosen[2], as_of).isoformat() if chosen else None
        ),
        "elapsed_minutes": elapsed,
    }

    components: list[dict] = []
    total: int | None = None
    restored_at: datetime | None = None
    duration: float | None = None
    if missing:
        status = NOT_COMPUTABLE
        basis = None
    else:
        _, crew_id, _crew, wait, travel = chosen
        components = [
            {
                "name": "crew_wait",
                "minutes": wait,
                "detail": {
                    "crew_id": crew_id,
                    "available_at": inputs["crew_available_at"],
                },
            },
            {
                "name": "travel_access",
                "minutes": travel,
                "detail": {
                    "from_substation": inputs["crew_depot_substation_id"],
                    "to_substation": incident.substation_id,
                },
            },
            {
                "name": "base_repair",
                "minutes": base,
                "detail": {"rate_key": rate_key, "cause_class": cause_class},
            },
        ]
        total = wait + travel + base
        if storm:
            storm_base = storm_base_repair(base, storm_pack)
            components.append(
                {
                    "name": storm_pack["etr"]["component_name"],
                    "minutes": storm_base - base,
                    "detail": {
                        "base_repair_minutes": base,
                        "storm_base_repair_minutes": storm_base,
                        "factor": storm_pack["etr"]["base_repair_factor"],
                        "storm_pack_version": storm["pack_version"],
                        "storm_declaration_id": storm["declaration_id"],
                    },
                }
            )
            total += storm_base - base
        restored_at = as_of + timedelta(minutes=total)
        duration = _minutes(restored_at - anchor.raised_at)
        status = COMPUTED
        basis = pack["basis_label"]

    body = {
        "incident_id": incident.incident_id,
        "as_of": as_of.isoformat(),
        "rate_pack_version": pack["version"],
        "roster_version": roster.version if roster else "",
        "operating_mode": "storm" if storm else "normal",
        "storm_pack_version": storm["pack_version"] if storm else None,
        "storm_declaration_id": storm["declaration_id"] if storm else None,
        "classification_id": inputs["classification_id"],
        "supersedes": supersedes,
        "status": status,
        "basis": basis,
        "inputs": json.loads(canonical_json(inputs)),
        "components": components,
        "total_minutes": total,
        "estimated_restored_at": (
            restored_at.isoformat() if restored_at else None
        ),
        "estimated_duration_minutes": duration,
        "missing_inputs": missing,
    }
    if prior is not None:
        body["revision"] = _revision_from(prior, inputs, components, body)
    return EtrEstimate(
        estimate_id=_estimate_id(body),
        as_of=as_of,
        estimated_restored_at=restored_at,
        **{k: v for k, v in body.items() if k not in ("as_of", "estimated_restored_at")},
    )


def _revision_from(
    prior: EtrEstimate, inputs: dict, components: list[dict], body: dict
) -> dict:
    """The delta against the superseded estimate, with the changed
    inputs named — a revision explains itself."""
    changed: list[str] = []
    for key in ("cause_class", "crew_id", "rate_key"):
        if prior.inputs.get(key) != inputs.get(key):
            changed.append(key)
    current = {line["name"]: line["minutes"] for line in components}
    for name in ("crew_wait", "travel_access", "base_repair"):
        if _component_minutes(prior, name) != current.get(name):
            changed.append(name)
    revision: dict = {
        "superseded_status": prior.status,
        "changed_inputs": changed,
        "total_minutes_delta": None,
        "estimated_restored_at_delta_minutes": None,
    }
    if (
        prior.status == COMPUTED
        and body["status"] == COMPUTED
        and prior.total_minutes is not None
        and body["total_minutes"] is not None
        and prior.estimated_restored_at is not None
        and body["estimated_restored_at"] is not None
    ):
        revision["total_minutes_delta"] = body["total_minutes"] - prior.total_minutes
        revision["estimated_restored_at_delta_minutes"] = _minutes(
            datetime.fromisoformat(body["estimated_restored_at"])
            - prior.estimated_restored_at
        )
    return revision


class EtrService:
    """Store-facing ETR work: estimate (classify + compute +
    persist + put the estimate on the incident's evidence chain),
    read current and history. Estimates are never edited — a
    recomputation is a new estimate linked to the one it
    supersedes."""

    def __init__(
        self,
        store,
        chain: EvidenceChain,
        cause: CauseService,
        roster: RosterView | None,
        pack: dict = ETR_PACK,
        storm_pack: dict = STORM_PACK,
    ):
        self.store = store
        self.chain = chain
        self.cause = cause
        self.roster = roster
        self.pack = pack
        # Round 8: the storm pack whose stated ETR factor applies
        # to incidents stamped storm.
        self.storm_pack = storm_pack

    def _topology_for(self, incident: Incident) -> TopologyView | None:
        payload = self.store.get_topology_payload(incident.topology_version)
        return TopologyView(payload) if payload is not None else None

    def current(self, incident_id: str) -> EtrEstimate | None:
        history = self.store.list_etr_estimates(incident_id)
        return history[-1] if history else None

    def history(self, incident_id: str) -> list[EtrEstimate]:
        return self.store.list_etr_estimates(incident_id)

    def estimate(
        self,
        incident_id: str,
        as_of: datetime,
        principal: Principal | None = None,
    ) -> EtrEstimate:
        guard_act(
            self, principal, ACT_ESTIMATE_ETR, target=incident_id, at=as_of
        )
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        classification = self.cause.classify(incident_id, as_of)
        anchor = self.store.get_alarm(incident.anchor_alarm_id)
        prior = self.current(incident_id)
        storm: dict | None = None
        if incident.operating_mode == "storm":
            storm = {
                "pack_version": incident.storm_pack_version,
                "declaration_id": incident.storm_declaration_id,
            }
        candidate = compute_estimate(
            incident,
            anchor,
            classification,
            self._topology_for(incident),
            self.roster,
            as_of,
            pack=self.pack,
            supersedes=prior.estimate_id if prior else None,
            prior=prior,
            storm=storm,
            storm_pack=self.storm_pack,
        )
        if prior is not None:
            # Identical recomputation against the same head: a
            # no-op, not a new link (the severity discipline).
            link_free = {"estimate_id", "supersedes", "revision"}
            if candidate.model_dump(exclude=link_free) == prior.model_dump(
                exclude=link_free
            ):
                return prior
        existing = self.store.get_etr_estimate(candidate.estimate_id)
        if existing is not None:
            return existing
        if prior is not None and candidate.as_of < prior.as_of:
            raise EtrRefusal(
                [
                    f"estimate at {candidate.as_of.isoformat()} precedes "
                    f"the current estimate's as_of "
                    f"({prior.as_of.isoformat()}) — a recomputation "
                    "supersedes the head going forward, never rewinds it"
                ]
            )
        self.store.add_etr_estimate(candidate)
        self.chain.append(
            incident_id,
            "etr_estimate",
            candidate.model_dump(mode="json"),
            recorded_on=as_of,
            item_id=f"EV-{candidate.estimate_id}",
        )
        return candidate
