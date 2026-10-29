"""The prior-incident memory port — Round 3.

In production this port is **Amazon Bedrock AgentCore Memory**:
closed incidents are written as event-memory records and retrieved
by the managed service. In this repo the port is served by a local
double (:class:`LocalMemoryDouble`) over the blueprint's own store
— the port shape, the record shape and the retrieval arithmetic
are the blueprint's; only the storage engine differs. Nothing in
the domain layer knows which implementation it is talking to.

What the port stores is deliberately plain: a closed incident's
**recorded facts** — feeder, devices, root-cause class, computed
severity band, and the resolution summary as recorded at close.
No prose is invented at write time or at read time, and records
are insert-only: the port exposes no update and no delete, and the
store refuses a re-insert that would change a record's facts. A
brief citing a memory record therefore cites facts that cannot
drift after the fact.

Retrieval is deterministic and computed in code — structured keys
plus term overlap over the recorded fields, under stated weights:

- same root-cause class ......... +4
- same feeder ................... +3
- same anchor device ............ +2
- any device in common .......... +1
- shared terms .................. +1 each, capped at +3
  (the incident's descriptor terms — cause class, feeder, device
  ids, anchor kind — against the record's textual fields: its
  cause class and the words of its recorded resolution summary)

A record is a candidate only on a structural basis — same feeder,
same anchor device, or a shared device. Term overlap alone never
qualifies a record: wording is a ranking signal, not a relevance
gate. Hits are ordered by score, then most recently closed, then
record id, so the same store always answers in the same order.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Protocol

from grid_triage.access import ACT_SEED_HISTORY, Principal, guard_act
from grid_triage.cause import CauseService
from grid_triage.evidence import canonical_json
from grid_triage.models import MemoryRecord
from grid_triage.severity import SeverityService
from grid_triage.store import Store

RETRIEVAL_WEIGHTS = {
    "same_cause_class": 4,
    "same_feeder": 3,
    "same_anchor_device": 2,
    "device_overlap": 1,
    "term_overlap_each": 1,
    "term_overlap_cap": 3,
}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9\-]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if t}


def record_terms(record: MemoryRecord) -> set[str]:
    """The textual terms of a record: its cause class plus the
    words of its recorded resolution summary. (The structured
    fields — feeder, devices — score through their own weights,
    not through term overlap.)"""
    terms = {record.cause_class.lower()}
    terms |= _tokens(record.resolution_summary)
    return terms


def incident_terms(
    feeder_id: str,
    anchor_device_id: str,
    anchor_kind: str,
    cause_class: str,
    device_ids,
) -> set[str]:
    """The descriptor terms of the incident asking for priors."""
    terms = {
        cause_class.lower(),
        feeder_id.lower(),
        anchor_device_id.lower(),
        anchor_kind.lower(),
    }
    terms |= {d.lower() for d in device_ids}
    return terms


class RetrievalHit:
    """One retrieved prior: the record, its score, and the named
    reasons it scored — the retrieval is auditable, not a vibe."""

    def __init__(self, record: MemoryRecord, score: int, reasons: dict):
        self.record = record
        self.score = score
        self.reasons = reasons

    def model_dump(self, mode: str = "json") -> dict:
        return {
            "record": self.record.model_dump(mode=mode),
            "score": self.score,
            "reasons": self.reasons,
        }


def score_record(
    record: MemoryRecord,
    *,
    feeder_id: str,
    anchor_device_id: str,
    cause_class: str,
    device_ids: set[str],
    terms: set[str],
    weights: dict = RETRIEVAL_WEIGHTS,
) -> RetrievalHit | None:
    """Score one record against an incident descriptor. None when
    the record has no structural basis for relevance."""
    w = weights
    reasons: dict = {}
    score = 0
    structural = False
    if record.feeder_id == feeder_id:
        structural = True
        score += w["same_feeder"]
        reasons["same_feeder"] = w["same_feeder"]
    if record.anchor_device_id == anchor_device_id:
        structural = True
        score += w["same_anchor_device"]
        reasons["same_anchor_device"] = w["same_anchor_device"]
    if set(record.device_ids) & device_ids:
        structural = True
        score += w["device_overlap"]
        reasons["device_overlap"] = w["device_overlap"]
    if not structural:
        return None
    if record.cause_class == cause_class:
        score += w["same_cause_class"]
        reasons["same_cause_class"] = w["same_cause_class"]
    shared = terms & record_terms(record)
    if shared:
        points = min(w["term_overlap_cap"], len(shared) * w["term_overlap_each"])
        score += points
        reasons["term_overlap"] = points
        reasons["shared_terms"] = sorted(shared)
    return RetrievalHit(record, score, reasons)


class MemoryPort(Protocol):
    """The port shape AgentCore Memory fills in production.

    Writes are inserts of complete records; reads are by id, in
    full, or by retrieval over an incident descriptor. There is no
    update and no delete in the shape — that absence is the
    immutability guarantee, and it is deliberate.
    """

    def put_record(self, record: MemoryRecord) -> MemoryRecord: ...
    def get_record(self, record_id: str) -> MemoryRecord | None: ...
    def list_records(self) -> list[MemoryRecord]: ...


class LocalMemoryDouble:
    """The local stand-in for AgentCore Memory, over the store
    port. Labelled a double everywhere it surfaces: production
    binds the managed service to the same port."""

    implementation = "local-double (AgentCore Memory stand-in)"

    def __init__(self, store: Store):
        self.store = store

    def put_record(self, record: MemoryRecord) -> MemoryRecord:
        return self.store.add_memory_record(record)

    def get_record(self, record_id: str) -> MemoryRecord | None:
        return self.store.get_memory_record(record_id)

    def list_records(self) -> list[MemoryRecord]:
        return self.store.list_memory_records()


def record_id_for(body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"MEM-{digest[:16]}"


class MemoryService:
    """Store-facing memory work: seed the planted history, record a
    closed incident from its stored facts, and retrieve the priors
    for a live incident."""

    def __init__(self, store, memory: MemoryPort, cause: CauseService,
                 severity: SeverityService):
        self.store = store
        self.memory = memory
        self.cause = cause
        self.severity = severity

    def load_history(
        self, principal: Principal | None = None
    ) -> list[MemoryRecord]:
        """Insert the planted historical records (idempotent —
        identical re-inserts are no-ops)."""
        guard_act(self, principal, ACT_SEED_HISTORY)
        from grid_triage.seed import history_records

        return [self.memory.put_record(r) for r in history_records()]

    def record_incident(
        self,
        incident_id: str,
        resolution_summary: str,
        closed_at: datetime,
    ) -> MemoryRecord:
        """Write the memory record for an incident being closed,
        assembled from its stored facts only: the classification of
        record, the current severity assessment, the membership.
        Facts that do not exist yet (no assessment) are computed at
        ``closed_at`` first — a record never carries a guessed
        band."""
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        classification = self.cause.classify(incident_id, closed_at)
        assessment = self.severity.current(incident_id)
        if assessment is None:
            assessment = self.severity.assess(incident_id, closed_at)
        memberships = self.store.list_memberships(incident_id)
        alarms = {
            m.alarm_id: self.store.get_alarm(m.alarm_id) for m in memberships
        }
        anchor = alarms.get(incident.anchor_alarm_id)
        body = {
            "incident_id": incident.incident_id,
            "feeder_id": incident.feeder_id,
            "substation_id": incident.substation_id,
            "anchor_device_id": anchor.device_id if anchor else "",
            "device_ids": sorted(
                {a.device_id for a in alarms.values() if a is not None}
            ),
            "cause_class": classification.cause_class,
            "cause_rule_id": classification.rule_id,
            "severity_band": assessment.band,
            "severity_score": assessment.score,
            "member_alarm_count": len(memberships),
            "customers_affected": assessment.inputs.get("customers_affected"),
            "resolution_summary": resolution_summary,
            "opened_at": incident.created_at.isoformat(),
            "closed_at": closed_at.isoformat(),
            "topology_version": incident.topology_version,
        }
        return self.memory.put_record(
            MemoryRecord(record_id=record_id_for(body), **body)
        )

    def retrieve_for_incident(
        self, incident_id: str, at: datetime, limit: int | None = None
    ) -> list[RetrievalHit]:
        """The priors for a live incident, best first. The incident
        is classified first (its class drives the weighting); the
        incident's own record, if one was ever written, is not a
        prior and is excluded."""
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        classification = self.cause.classify(incident_id, at)
        memberships = self.store.list_memberships(incident_id)
        alarms = {
            m.alarm_id: self.store.get_alarm(m.alarm_id) for m in memberships
        }
        anchor = alarms.get(incident.anchor_alarm_id)
        device_ids = {a.device_id for a in alarms.values() if a is not None}
        terms = incident_terms(
            incident.feeder_id,
            anchor.device_id if anchor else "",
            anchor.kind if anchor else "",
            classification.cause_class,
            device_ids,
        )
        hits: list[RetrievalHit] = []
        for record in self.memory.list_records():
            if record.incident_id == incident_id:
                continue
            hit = score_record(
                record,
                feeder_id=incident.feeder_id,
                anchor_device_id=anchor.device_id if anchor else "",
                cause_class=classification.cause_class,
                device_ids=device_ids,
                terms=terms,
            )
            if hit is not None:
                hits.append(hit)
        hits.sort(
            key=lambda h: (
                -h.score,
                -h.record.closed_at.timestamp(),
                h.record.record_id,
            )
        )
        return hits[:limit] if limit is not None else hits
