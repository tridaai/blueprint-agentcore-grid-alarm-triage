"""The severity engine — Law 3: severity is computed.

A band is the output of a versioned formula pack over recorded
inputs, never a model's opinion and never a source-stated alarm
severity:

- **customers affected** — the sum of per-device customer counts
  (topology data) over the de-energised slice: the anchor device
  plus everything downstream of it in the incident's pinned
  topology version.
- **critical loads affected** — critical loads served from a device
  in the slice, weighted by kind from the pack.
- **duration** — minutes from the anchor alarm to an explicit
  ``as_of`` (no wall clock anywhere in the computation).
- **alarm breadth** — the incident's member-alarm count.

The computation is a pure function. Its result is a
content-addressed :class:`SeverityAssessment`: every input and
weight is recorded, the assessment pins (incident, topology
version, pack version), and a recomputation supersedes by link —
assessments are never edited. An input the data cannot supply is
named in ``missing_inputs`` and the band is ``UNRATED``: unknowns
are never zero-filled and never guessed.

Bands at or above the pack's escalation thresholds require a
named operator's acknowledgement. The requirement and the queue
are derived from stored assessments plus recorded acknowledgement
acts — an acknowledgement names a person and a role the pack
allows for that band, and it satisfies exactly one assessment.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from grid_triage.evidence import EvidenceChain, canonical_json
from grid_triage.models import (
    Alarm,
    Incident,
    SeverityAcknowledgement,
    SeverityAssessment,
)
from grid_triage.storm import STORM_PACK, severity_adjustments, storm_severity_pack
from grid_triage.topology import TopologyView

SEVERITY_PACK = {
    "version": "2026.10.1",
    # score terms (integer arithmetic throughout — hand-computable)
    "customers_per_point": 25,
    "customer_points_cap": 50,
    "critical_load_weights": {
        "hospital": 30,
        "water_treatment": 20,
        "emergency_911": 20,
    },
    "minutes_per_duration_point": 30,
    "duration_points_cap": 10,
    "alarms_per_breadth_point": 10,
    "breadth_points_cap": 5,
    # highest threshold first; the band is the first threshold the
    # score reaches.
    "band_thresholds": [[75, "SEV-1"], [45, "SEV-2"], [20, "SEV-3"], [0, "SEV-4"]],
    # escalation: bands whose assessments require a named operator
    # acknowledgement, and the roles allowed to give it.
    "escalation": {
        "SEV-1": {"requires_acknowledgement": True, "ack_roles": ["operator", "duty_manager"]},
        "SEV-2": {"requires_acknowledgement": True, "ack_roles": ["operator"]},
        "SEV-3": {"requires_acknowledgement": False, "ack_roles": []},
        "SEV-4": {"requires_acknowledgement": False, "ack_roles": []},
        "UNRATED": {"requires_acknowledgement": True, "ack_roles": ["operator", "duty_manager"]},
    },
}

UNRATED = "UNRATED"


def band_for_score(score: int, pack: dict = SEVERITY_PACK) -> str:
    for threshold, band in pack["band_thresholds"]:
        if score >= threshold:
            return band
    return pack["band_thresholds"][-1][1]


def _assessment_id(body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"SEVA-{digest[:16]}"


def compute_assessment(
    incident: Incident,
    alarms_by_id: dict[str, Alarm],
    topology: TopologyView | None,
    as_of: datetime,
    pack: dict = SEVERITY_PACK,
    supersedes: str | None = None,
    storm: dict | None = None,
) -> SeverityAssessment:
    """Pure severity computation. ``topology`` is the view of the
    incident's pinned topology version, or None when that version
    is not loaded — the affected inputs are then named missing.

    ``storm`` — for an incident stamped storm (Round 8) — is the
    stamp context ``{"pack_version", "declaration_id",
    "adjustments"}``; the caller passes the storm-merged pack
    as ``pack``, and the assessment's inputs record the mode,
    the declaration and the pack's stated adjustments, so the
    storm computation is labelled on its face."""
    missing: list[str] = []
    anchor = alarms_by_id.get(incident.anchor_alarm_id)

    member_count = len(alarms_by_id)
    breadth_points = min(
        pack["breadth_points_cap"], member_count // pack["alarms_per_breadth_point"]
    )

    duration_minutes: float | None = None
    duration_points: int | None = None
    if anchor is None:
        missing.append("anchor_alarm")
    elif as_of < anchor.raised_at:
        missing.append("duration_minutes")
    else:
        duration_minutes = round((as_of - anchor.raised_at).total_seconds() / 60.0, 3)
        duration_points = min(
            pack["duration_points_cap"],
            int(duration_minutes) // pack["minutes_per_duration_point"],
        )

    customers: int | None = None
    customer_points: int | None = None
    critical: list[dict] = []
    critical_points: int | None = None
    slice_devices: list[str] = []
    if topology is None:
        missing.extend(["topology_version", "customers_affected", "critical_loads"])
    elif anchor is None:
        missing.extend(["customers_affected", "critical_loads"])
    else:
        slice_set = topology.outage_slice(anchor.device_id)
        if slice_set is None:
            missing.extend(["customers_affected", "critical_loads"])
        else:
            slice_devices = sorted(slice_set)
            customers = topology.customers_in(slice_set)
            if customers is None:
                missing.append("customers_affected")
            else:
                customer_points = min(
                    pack["customer_points_cap"],
                    customers // pack["customers_per_point"],
                )
            loads = topology.critical_loads_in(slice_set)
            if loads is None:
                missing.append("critical_loads")
            else:
                weights = pack["critical_load_weights"]
                if any(load["kind"] not in weights for load in loads):
                    missing.append("critical_load_weights")
                else:
                    critical = [
                        {
                            "load_id": load["load_id"],
                            "kind": load["kind"],
                            "name": load["name"],
                            "device_id": load["device_id"],
                            "weight": weights[load["kind"]],
                        }
                        for load in sorted(loads, key=lambda l: l["load_id"])
                    ]
                    critical_points = sum(c["weight"] for c in critical)

    terms = {
        "customers": customer_points,
        "critical_loads": critical_points,
        "duration": duration_points,
        "breadth": breadth_points,
    }
    if missing:
        band = UNRATED
        score = None
    else:
        score = sum(int(v) for v in terms.values())
        band = band_for_score(score, pack)

    escalation = pack["escalation"][band]
    inputs = {
        "anchor_device_id": anchor.device_id if anchor else None,
        "slice_device_ids": slice_devices,
        "customers_affected": customers,
        "critical_loads": critical,
        "duration_minutes": duration_minutes,
        "member_alarm_count": member_count,
        "points": terms,
        # Round 8: the operating mode is on every assessment's
        # face. A storm assessment also names the declaration it
        # rests on and the pack's stated parameter adjustments.
        "operating_mode": "storm" if storm else "normal",
        "storm_pack_version": storm["pack_version"] if storm else None,
        "storm_declaration_id": storm["declaration_id"] if storm else None,
        "storm_adjustments": storm["adjustments"] if storm else None,
    }
    body = {
        "incident_id": incident.incident_id,
        "as_of": as_of.isoformat(),
        "topology_version": incident.topology_version,
        "pack_version": pack["version"],
        "supersedes": supersedes,
        "inputs": json.loads(canonical_json(inputs)),
        "score": score,
        "band": band,
        "missing_inputs": missing,
        "escalation_required": bool(escalation["requires_acknowledgement"]),
        "ack_roles": list(escalation["ack_roles"]),
    }
    return SeverityAssessment(assessment_id=_assessment_id(body), **body)


class SeverityRefusal(RuntimeError):
    """An acknowledgement act the pack does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class SeverityService:
    """Store-facing severity work: assess (compute + persist + put
    the assessment on the incident's evidence chain), read current
    and history, and the acknowledgement queue and act."""

    def __init__(
        self,
        store,
        chain: EvidenceChain,
        pack: dict = SEVERITY_PACK,
        storm_pack: dict = STORM_PACK,
    ):
        self.store = store
        self.chain = chain
        self.pack = pack
        # Round 8: the storm pack whose stated overrides apply
        # to incidents stamped storm. Nothing else about the
        # service changes with the mode.
        self.storm_pack = storm_pack

    def _topology_for(self, incident: Incident) -> TopologyView | None:
        payload = self.store.get_topology_payload(incident.topology_version)
        return TopologyView(payload) if payload is not None else None

    def current(self, incident_id: str) -> SeverityAssessment | None:
        history = self.store.list_severity_assessments(incident_id)
        return history[-1] if history else None

    def history(self, incident_id: str) -> list[SeverityAssessment]:
        return self.store.list_severity_assessments(incident_id)

    def assess(self, incident_id: str, as_of: datetime) -> SeverityAssessment:
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        alarms_by_id = {
            m.alarm_id: self.store.get_alarm(m.alarm_id)
            for m in self.store.list_memberships(incident_id)
        }
        alarms_by_id = {k: v for k, v in alarms_by_id.items() if v is not None}
        prior = self.current(incident_id)
        pack = self.pack
        storm: dict | None = None
        if incident.operating_mode == "storm":
            pack = storm_severity_pack(self.pack, self.storm_pack)
            storm = {
                "pack_version": incident.storm_pack_version,
                "declaration_id": incident.storm_declaration_id,
                "adjustments": severity_adjustments(self.storm_pack),
            }
        candidate = compute_assessment(
            incident,
            alarms_by_id,
            self._topology_for(incident),
            as_of,
            pack=pack,
            supersedes=prior.assessment_id if prior else None,
            storm=storm,
        )
        if prior is not None:
            # Identical recomputation against the same head: a
            # no-op, not a new link. The comparison ignores the
            # identity fields — the candidate's ``supersedes``
            # points at the prior by construction, so the content
            # hash can never match the prior's own.
            link_free = {"assessment_id", "supersedes"}
            if candidate.model_dump(exclude=link_free) == prior.model_dump(
                exclude=link_free
            ):
                return prior
        existing = self.store.get_severity_assessment(candidate.assessment_id)
        if existing is not None:
            return existing  # already recorded (e.g. re-assessment at an older as_of)
        if prior is not None and candidate.as_of < prior.as_of:
            # Round 10 red team: history is insertion-ordered, so
            # appending a computation whose as_of precedes the
            # head's would make a stale band the current one on
            # every surface that reads the head — severity deflated
            # after the fact by re-running the clock backwards.
            # A recomputation supersedes going forward, or it is
            # the identical artefact already on record above.
            raise SeverityRefusal(
                [
                    f"assessment at {candidate.as_of.isoformat()} precedes "
                    f"the current assessment's as_of "
                    f"({prior.as_of.isoformat()}) — a recomputation "
                    "supersedes the head going forward, never rewinds it"
                ]
            )
        self.store.add_severity_assessment(candidate)
        self.chain.append(
            incident_id,
            "severity_assessment",
            candidate.model_dump(mode="json"),
            recorded_on=as_of,
            item_id=f"EV-{candidate.assessment_id}",
        )
        return candidate

    # -- acknowledgement (requirement + queue + act) -------------------
    def pending_acknowledgements(self) -> list[SeverityAssessment]:
        """Assessments whose band requires an acknowledgement that
        no recorded act has satisfied yet, oldest first."""
        acked = {
            a.assessment_id for a in self.store.list_severity_acknowledgements()
        }
        pending: list[SeverityAssessment] = []
        for incident in self.store.list_incidents():
            for assessment in self.store.list_severity_assessments(incident.incident_id):
                if assessment.escalation_required and assessment.assessment_id not in acked:
                    pending.append(assessment)
        return pending

    def acknowledge(
        self,
        assessment_id: str,
        acknowledged_by: str,
        role: str,
        acknowledged_at: datetime,
    ) -> SeverityAcknowledgement:
        assessment = self.store.get_severity_assessment(assessment_id)
        if assessment is None:
            raise KeyError(f"no such assessment: {assessment_id}")
        problems: list[str] = []
        if not acknowledged_by or not acknowledged_by.strip():
            problems.append("an acknowledgement names its operator")
        if not assessment.escalation_required:
            problems.append(
                f"band {assessment.band} does not require acknowledgement"
            )
        elif role not in assessment.ack_roles:
            problems.append(
                f"role {role!r} may not acknowledge a {assessment.band} assessment"
                f" (allowed: {', '.join(assessment.ack_roles)})"
            )
        existing = self.store.list_severity_acknowledgements(assessment.incident_id)
        if any(a.assessment_id == assessment_id for a in existing):
            problems.append(f"assessment {assessment_id} is already acknowledged")
        if problems:
            raise SeverityRefusal(problems)
        body = {
            "assessment_id": assessment_id,
            "incident_id": assessment.incident_id,
            "acknowledged_by": acknowledged_by,
            "role": role,
            "acknowledged_at": acknowledged_at.isoformat(),
        }
        ack = SeverityAcknowledgement(
            ack_id=f"ACK-{hashlib.sha256(canonical_json(body)).hexdigest()[:16]}",
            **body,
        )
        self.store.add_severity_acknowledgement(ack)
        return ack
