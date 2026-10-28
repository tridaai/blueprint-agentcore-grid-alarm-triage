"""Domain models for the grid alarm triage agent.

Alarms are evidence: append-only, never edited, never deleted
(Law 7). Incidents are correlation outputs — every incident records
the exact alarm ids and the correlation rule ids that formed it
(Law 2). Topology is versioned data (Law 6); an incident pins the
topology version it was correlated against.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Alarm(BaseModel):
    """One raw alarm as delivered by a SCADA/OMS-shaped source."""

    alarm_id: str
    stream_id: str  # the ingest stream / source feed it arrived on
    device_id: str
    feeder_id: str
    kind: str  # e.g. breaker_trip, voltage_sag, comms_poll_failure
    severity: str = "major"  # info | minor | major | critical (source-stated)
    raised_at: datetime
    message: str = ""


class IncidentStatus(str, Enum):
    OPEN = "open"


class Incident(BaseModel):
    incident_id: str
    feeder_id: str
    substation_id: str
    topology_version: str  # pinned at correlation time (Law 6)
    correlation_pack_version: str
    anchor_alarm_id: str
    status: IncidentStatus = IncidentStatus.OPEN
    created_at: datetime


class IncidentMembership(BaseModel):
    """One alarm's membership in one incident, with the rule that
    put it there. This is the correlation audit trail (Law 2)."""

    incident_id: str
    alarm_id: str
    rule_id: str


class Citation(BaseModel):
    evidence_item_id: str
    field: str


class BriefStatement(BaseModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)


class IncidentBrief(BaseModel):
    """The agent step's only write: an assembled, fully cited brief."""

    brief_id: str
    incident_id: str
    model_profile: str
    statements: list[BriefStatement]
    assembled_at: datetime


class EvidenceItem(BaseModel):
    item_id: str
    incident_id: str
    kind: str
    object_key: str
    payload_sha256: str
    prev_hash: str
    item_hash: str
    recorded_on: datetime


class SeverityAssessment(BaseModel):
    """One severity computation for one incident (Round 2, Law 3).

    Content-addressed: ``assessment_id`` is a hash of everything
    below except the id itself, so the same inputs at the same
    ``as_of`` always name the same assessment. Assessments are
    never edited — a recomputation is a new assessment whose
    ``supersedes`` links to the one it replaces. Every input and
    every weight the formula applied is recorded in ``inputs``;
    an input the data cannot supply is named in ``missing_inputs``
    and the band is ``UNRATED`` — never guessed, never zero-filled.
    """

    assessment_id: str
    incident_id: str
    as_of: datetime
    topology_version: str
    pack_version: str
    supersedes: str | None = None
    inputs: dict = Field(default_factory=dict)
    score: int | None = None
    band: str
    missing_inputs: list[str] = Field(default_factory=list)
    escalation_required: bool = False
    ack_roles: list[str] = Field(default_factory=list)


class SeverityAcknowledgement(BaseModel):
    """A named operator's acknowledgement of one assessment whose
    band crossed an escalation threshold (Round 2). A recorded act
    with a role — the requirement, the queue and this act are all
    Round 2 builds; the work-order approval gate itself is Round 6.
    """

    ack_id: str
    assessment_id: str
    incident_id: str
    acknowledged_by: str
    role: str
    acknowledged_at: datetime


class CauseClassification(BaseModel):
    """One root-cause classification for one incident (Round 3).

    The output of a versioned classification pack over the anchor
    alarm's pattern — alarm kinds, devices, messages — computed in
    code, never by the model. ``cause_class`` is one of the pack's
    classes or ``unknown``: a pattern no rule matches stays
    ``unknown`` with ``rule_id`` None, because a forced class is a
    fabricated fact. Content-addressed like the severity
    assessments: the same incident under the same pack version
    always names the same classification, and it is never edited.
    """

    classification_id: str
    incident_id: str
    pack_version: str
    cause_class: str
    rule_id: str | None = None
    inputs: dict = Field(default_factory=dict)
    classified_at: datetime


class MemoryRecord(BaseModel):
    """One closed incident as episodic memory (Round 3).

    The event-memory shape the prior-incident port stores: the
    recorded facts of a finished incident — where it happened, what
    class it was classified, how severe it computed, and the
    resolution as recorded at close. Records are insert-only: a
    record, once written, is never updated and never deleted, so a
    brief that cites one cites facts that cannot drift.
    """

    record_id: str
    incident_id: str
    feeder_id: str
    substation_id: str
    anchor_device_id: str
    device_ids: list[str] = Field(default_factory=list)
    cause_class: str
    cause_rule_id: str | None = None
    severity_band: str
    severity_score: int | None = None
    member_alarm_count: int
    customers_affected: int | None = None
    resolution_summary: str
    opened_at: datetime
    closed_at: datetime
    topology_version: str


class EvidencePack(BaseModel):
    """A sealed evidence pack for one incident (Round 3).

    Everything the incident rests on, assembled at seal time into a
    manifest: each member names an artefact of record and the hash
    of its content. ``manifest_hash`` covers the member list.
    Packs are never edited — when a member is later superseded (a
    new severity assessment, a new brief), resealing produces the
    next ``version`` of the pack, whose ``supersedes`` links to
    this one. Verification recomputes every member hash from its
    source of record plus the incident's evidence chain.
    """

    pack_id: str
    incident_id: str
    version: int
    supersedes: str | None = None
    sealed_by: str
    sealed_at: datetime
    members: list[dict] = Field(default_factory=list)
    manifest_hash: str


class Notification(BaseModel):
    """A recorded notification (Law: notify is a tool shape). In R1
    the notification is recorded against the incident; no real
    channel is connected — see README honest status."""

    notification_id: str
    incident_id: str
    channel: str
    body: str
    created_at: datetime


class ToolShape(str, Enum):
    READ = "read"
    DRAFT = "draft"
    NOTIFY = "notify"


class ToolSpec(BaseModel):
    """One entry in the tool catalogue (Law 1). The catalogue holds
    read, draft and notify shapes only — there is no control-shaped
    tool anywhere in this codebase."""

    name: str
    shape: ToolShape
    description: str
