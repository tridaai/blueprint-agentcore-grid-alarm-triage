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


class EtrEstimate(BaseModel):
    """One ETR (estimated time of restoration) computation for one
    incident (Round 4, Law 4).

    Computed in code from stated inputs — the classified cause,
    the anchor device, a versioned repair-rate pack and the crew
    roster — and content-addressed like the severity assessments:
    a recomputation supersedes by link, never edits. When every
    input exists the estimate's ``basis`` is
    ``computed_from_rates`` and its components (crew wait, travel,
    base repair) are recorded line by line. When an input is
    missing the estimate is ``not_computable`` and names the
    input in ``missing_inputs``: the system never emits a guessed
    clock time, so a not-computable estimate carries no restored
    time and no total.
    """

    estimate_id: str
    incident_id: str
    as_of: datetime
    rate_pack_version: str
    roster_version: str
    classification_id: str | None = None
    supersedes: str | None = None
    status: str  # "computed" | "not_computable"
    basis: str | None = None  # "computed_from_rates" when computed
    inputs: dict = Field(default_factory=dict)
    components: list[dict] = Field(default_factory=list)
    total_minutes: int | None = None
    estimated_restored_at: datetime | None = None
    estimated_duration_minutes: float | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    revision: dict | None = None


class IncidentClosure(BaseModel):
    """A named operator's closure act for one incident (Round 4).

    The minimal act the ETR accuracy ledger and the memory port
    rest on: who closed the incident, in what role, when supply
    was actually restored, and the resolution facts as recorded.
    The full restoration-confirmation workflow is Round 8; this
    act is deliberately only the recorded facts. A closure is
    written once — an incident has at most one.
    """

    closure_id: str
    incident_id: str
    closed_by: str
    role: str
    restored_at: datetime
    resolution_summary: str
    recorded_at: datetime


class EtrOutcome(BaseModel):
    """One closed incident's ETR track record (Round 4).

    At closure, the actual duration (restoration minus the anchor
    alarm) is compared with the estimate of record — the latest
    computed estimate whose ``as_of`` does not postdate the
    restoration. The signed error is actual minus estimated:
    positive means restoration ran late against the estimate.
    When no computed estimate exists the outcome still records
    the actual duration with status ``no_computed_estimate`` and
    no error figures: an unestimated incident is a gap in the
    track record, never silently dropped and never back-filled
    with a retro-estimate.
    """

    outcome_id: str
    incident_id: str
    closure_id: str
    estimate_id: str | None = None
    cause_class: str
    status: str  # "compared" | "no_computed_estimate"
    estimated_duration_minutes: float | None = None
    actual_duration_minutes: float
    signed_error_minutes: float | None = None
    absolute_error_minutes: float | None = None
    recorded_at: datetime


class WorkOrder(BaseModel):
    """One work-order draft version for one incident (Round 5,
    Law 5).

    A draft authorises nothing: it states the work the incident
    calls for — scope derived in code from the classification of
    record and the affected devices on the pinned topology,
    safety notes and materials from the versioned work pack, the
    crew qualification the roster's vocabulary names, and the R4
    ETR estimate pinned by id. Content-addressed like the
    severity assessments: the same derived inputs name the same
    draft, drafts are never edited, and a change is a new
    ``version`` whose ``supersedes`` links to the version it
    replaces. ``drafted_by`` is ``"system"`` for the assembler's
    draft or the name of the operator who revised it — the
    approval gate's self-approval rule reads it. What the data
    cannot supply (an unclassified cause states no honest crew
    qualification) is named in ``gaps``, never filled in.
    """

    order_id: str
    incident_id: str
    version: int
    supersedes: str | None = None
    drafted_by: str
    drafted_at: datetime
    work_pack_version: str
    classification_id: str | None = None
    cause_class: str
    estimate_id: str | None = None
    scope_of_work: list[str] = Field(default_factory=list)
    affected_devices: list[dict] = Field(default_factory=list)
    safety_notes: list[dict] = Field(default_factory=list)
    required_qualification: str | None = None
    materials: list[dict] = Field(default_factory=list)
    operator_note: str | None = None
    gaps: list[str] = Field(default_factory=list)


class WorkOrderAssignment(BaseModel):
    """A named operator's assignment of one crew to one work-order
    version (Round 5). A recorded act, not an edit: the draft it
    names stays byte-identical. Qualification and double-booking
    are checked in code at assignment time; ``supersedes`` links
    to the assignment on the same order this one replaces, so the
    current assignment is always the latest act."""

    assignment_id: str
    order_id: str
    incident_id: str
    crew_id: str
    assigned_by: str
    assigned_at: datetime
    window_start: datetime
    window_end: datetime
    supersedes: str | None = None


class WorkOrderDecision(BaseModel):
    """A named operator's approval or rejection of one exact
    work-order version (Round 5, Law 5). The act pins the
    ``order_id`` it decided: approval of a version is not approval
    of its successors. An approval also drafts the work order's
    notifications — their ids are recorded here, and the
    notifications themselves are drafts, never sent."""

    decision_id: str
    order_id: str
    incident_id: str
    decision: str  # "approved" | "rejected"
    decided_by: str
    role: str
    reason: str | None = None
    decided_at: datetime
    notification_ids: list[str] = Field(default_factory=list)


class FieldUpdate(BaseModel):
    """One field update posted against one incident (Round 6).

    Field crews and dispatchers report back against an incident:
    a new alarm observation, a crew note, a damage finding. The
    update kinds are a versioned pack's vocabulary, and updates
    are insert-only artefacts on the incident's evidence chain.
    An update carrying a new alarm is correlated under the
    incident's **pinned** topology version — never silently under
    a newer one — and the outcome is recorded on the update
    itself in ``correlation``: ``joined`` with the rule that
    placed it, or a named non-join (``unresolvable_under_pinned_
    topology`` when the alarm's device does not exist in the
    pinned version, ``no_join`` when the pack does not place it
    in this incident).
    """

    update_id: str
    incident_id: str
    pack_version: str
    kind: str  # alarm_observation | crew_note | damage_finding
    reported_by: str
    role: str
    reported_at: datetime
    note: str | None = None
    device_id: str | None = None
    finding: str | None = None
    alarm_id: str | None = None
    correlation: dict = Field(default_factory=dict)


class TopologyDeclaration(BaseModel):
    """A named operator's act declaring a topology version
    effective (Round 6, Law 6). A topology change is data (the
    version payload) plus this act: who declared it, in what
    role, and from when it is effective. Re-correlation under
    the version is impossible before its effective time and
    impossible at all without this act — no silent migration.
    The ``change_set`` is carried verbatim from the version
    payload, so the act states what changed."""

    declaration_id: str
    version: str
    pack_version: str
    declared_by: str
    role: str
    declared_at: datetime
    effective_at: datetime
    change_set: list[dict] = Field(default_factory=list)


class IncidentRevision(BaseModel):
    """One re-correlation of one incident under a newer topology
    version (Round 6, Law 6). The revision is a new incident row
    (``revised_incident_id``) correlated under the new version;
    this record links it to the revision it supersedes and
    states the diff: alarms and devices added and removed,
    the topology change set applied, the severity recomputation
    (band change and the formula inputs that moved), and the
    ETR estimates on both sides, linked by id. The prior
    incident's own artefacts are never edited — supersession
    lives here, on the link."""

    revision_id: str
    prior_incident_id: str
    revised_incident_id: str
    revision_number: int
    base_topology_version: str
    target_topology_version: str
    declaration_id: str
    recorrelated_by: str
    role: str
    recorrelated_at: datetime
    diff: dict = Field(default_factory=dict)
    severity_change: dict = Field(default_factory=dict)
    prior_classification_id: str | None = None
    revised_classification_id: str | None = None
    prior_estimate_id: str | None = None
    revised_estimate_id: str | None = None


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
