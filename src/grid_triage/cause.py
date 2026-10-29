"""The root-cause classification pack — Round 3.

Correlation says which alarms belong together; severity says how
much they matter. This pack says what probably *caused* the
incident — as a class, computed in code from the anchor alarm's
pattern, under a versioned rule pack:

- ``lightning`` — a surge/lightning-class alarm is among the
  members: the event announced its own cause.
- ``vegetation_contact`` — a protection anchor whose recloser
  locked out after unsuccessful reclose attempts, with fault
  indications downstream: the crew kept reclosing into a
  persistent line fault, and vegetation contact is the leading
  cause of exactly that signature.
- ``equipment_failure`` — a protection anchor with transformer
  thermal alarms among the members and no recloser lockout: the
  plant itself failed, not the line.

Rules are evaluated in pack order; the first match wins and its
rule id is recorded. A pattern no rule matches is classified
``unknown`` with no rule id — a first-class outcome, never forced
into the nearest class, because downstream consumers (prior
retrieval weighting, reporting) treat the class as a recorded
fact, and a fabricated class would contaminate all of them.

The classification is a pure function of the incident's stored
alarms. The service records it once per incident per pack version
as a content-addressed artefact, and appends it to the incident's
evidence chain — the same discipline as the severity assessments.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from grid_triage.access import ACT_CLASSIFY, Principal, guard_act
from grid_triage.evidence import EvidenceChain, canonical_json
from grid_triage.models import Alarm, CauseClassification, Incident

UNKNOWN = "unknown"

CAUSE_PACK = {
    "version": "2026.10.1",
    # The closed class vocabulary. `unknown` is not in the rule
    # list: it is what a non-matching pattern is called.
    "classes": ["vegetation_contact", "equipment_failure", "lightning", "unknown"],
    "protection_kinds": ["breaker_trip", "relay_protection", "recloser_lockout"],
    "rules": [
        {
            "rule_id": "surge_signature",
            "cause_class": "lightning",
            "any_member_kind_in": ["lightning_strike", "surge_arrester_operation"],
        },
        {
            "rule_id": "lockout_with_downstream_fault",
            "cause_class": "vegetation_contact",
            "anchor_kind_in": ["breaker_trip", "relay_protection", "recloser_lockout"],
            "requires_member_kind": "recloser_lockout",
            "any_member_kind_in": [
                "phase_loss",
                "current_fault_indication",
                "voltage_sag",
            ],
        },
        {
            "rule_id": "thermal_without_lockout",
            "cause_class": "equipment_failure",
            "anchor_kind_in": ["breaker_trip", "relay_protection"],
            "any_member_kind_in": ["transformer_temp_high"],
            "forbids_member_kind": "recloser_lockout",
        },
    ],
}


def _rule_matches(rule: dict, anchor: Alarm, members: list[Alarm]) -> bool:
    kinds = {a.kind for a in members}
    if "anchor_kind_in" in rule and anchor.kind not in rule["anchor_kind_in"]:
        return False
    if "requires_member_kind" in rule and rule["requires_member_kind"] not in kinds:
        return False
    if "forbids_member_kind" in rule and rule["forbids_member_kind"] in kinds:
        return False
    if "any_member_kind_in" in rule and not (kinds & set(rule["any_member_kind_in"])):
        return False
    return True


def _classification_id(body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"CAUSE-{digest[:16]}"


def classify_cause(
    incident: Incident,
    alarms_by_id: dict[str, Alarm],
    classified_at: datetime,
    pack: dict = CAUSE_PACK,
) -> CauseClassification:
    """Pure classification. ``alarms_by_id`` is the incident's
    member alarms keyed by id."""
    anchor = alarms_by_id.get(incident.anchor_alarm_id)
    members = [alarms_by_id[k] for k in sorted(alarms_by_id)]
    inputs = {
        "anchor_alarm_id": incident.anchor_alarm_id,
        "anchor_kind": anchor.kind if anchor else None,
        "anchor_device_id": anchor.device_id if anchor else None,
        "member_kinds": sorted({a.kind for a in members}),
        "member_alarm_count": len(members),
    }
    cause_class = UNKNOWN
    rule_id: str | None = None
    if anchor is not None:
        for rule in pack["rules"]:
            if _rule_matches(rule, anchor, members):
                cause_class = rule["cause_class"]
                rule_id = rule["rule_id"]
                break
    body = {
        "incident_id": incident.incident_id,
        "pack_version": pack["version"],
        "cause_class": cause_class,
        "rule_id": rule_id,
        "inputs": inputs,
    }
    return CauseClassification(
        classification_id=_classification_id(body),
        classified_at=classified_at,
        **body,
    )


class CauseService:
    """Store-facing classification: compute + persist + chain, once
    per incident per pack version. Re-classifying an incident whose
    stored classification was computed under the same pack version
    returns the stored artefact — classifications are never edited
    and never recomputed into a different answer in place."""

    def __init__(self, store, chain: EvidenceChain, pack: dict = CAUSE_PACK):
        self.store = store
        self.chain = chain
        self.pack = pack

    def current(self, incident_id: str) -> CauseClassification | None:
        return self.store.get_cause_classification(incident_id)

    def classify(
        self,
        incident_id: str,
        classified_at: datetime,
        principal: Principal | None = None,
    ) -> CauseClassification:
        guard_act(
            self, principal, ACT_CLASSIFY, target=incident_id, at=classified_at
        )
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        existing = self.store.get_cause_classification(incident_id)
        if existing is not None and existing.pack_version == self.pack["version"]:
            return existing
        alarms_by_id = {
            m.alarm_id: self.store.get_alarm(m.alarm_id)
            for m in self.store.list_memberships(incident_id)
        }
        alarms_by_id = {k: v for k, v in alarms_by_id.items() if v is not None}
        classification = classify_cause(
            incident, alarms_by_id, classified_at, pack=self.pack
        )
        if existing is not None and (
            existing.classification_id == classification.classification_id
        ):
            return existing
        self.store.add_cause_classification(classification)
        self.chain.append(
            incident_id,
            "cause_classification",
            classification.model_dump(mode="json"),
            recorded_on=classified_at,
            item_id=f"EV-{classification.classification_id}",
        )
        return classification
