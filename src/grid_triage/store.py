"""The store port and its SQL implementations.

One port, two production dialects: SQLite is the local default
(``./data/grid.db``), PostgreSQL is the production store on AWS
(RDS / Aurora PostgreSQL, pgvector available in the compose
profile for later Memory-adjacent workloads). Payloads are stored
as JSON text in both dialects — one contract, no dialect drift.

The alarms table is the append-only ingest log (Law 7): the port
exposes inserts and reads for it, and deliberately no update or
delete. Incidents, memberships, evidence, briefs and the topology
index sit alongside it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from grid_triage.migrations import apply_migrations
from grid_triage.models import (
    Alarm,
    CauseClassification,
    EtrEstimate,
    EtrOutcome,
    EvidenceItem,
    EvidencePack,
    FieldUpdate,
    Incident,
    IncidentBrief,
    IncidentCloseAct,
    IncidentClosure,
    IncidentMembership,
    IncidentRevision,
    IncidentSplit,
    IncidentStatus,
    MemoryRecord,
    Notification,
    PostCloseContradiction,
    RestorationConfirmation,
    RestorationSignal,
    SeverityAcknowledgement,
    SeverityAssessment,
    StormDeclaration,
    StormReview,
    TopologyDeclaration,
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderCompletion,
    WorkOrderDecision,
)


class MemoryImmutableError(RuntimeError):
    """A memory record id was re-presented with different content.

    Memory records are insert-only (Round 3): the port has no
    update and no delete for them, and an insert that would change
    a stored record's facts is refused here, mechanically."""


class Store(Protocol):
    # alarms (append-only ingest log)
    def add_alarm(self, alarm: Alarm) -> int: ...
    def get_alarm(self, alarm_id: str) -> Alarm | None: ...
    def list_alarms(self) -> list[Alarm]: ...
    def list_uncorrelated_alarms(self) -> list[Alarm]: ...
    # incidents
    def add_incident(self, incident: Incident) -> None: ...
    def get_incident(self, incident_id: str) -> Incident | None: ...
    def list_incidents(self) -> list[Incident]: ...
    def add_membership(self, membership: IncidentMembership) -> None: ...
    def list_memberships(self, incident_id: str) -> list[IncidentMembership]: ...
    def membership_for_alarm(self, alarm_id: str) -> IncidentMembership | None: ...
    # evidence / briefs / notifications
    def add_evidence(self, item: EvidenceItem) -> None: ...
    def list_evidence(self, incident_id: str) -> list[EvidenceItem]: ...
    def add_brief(self, brief: IncidentBrief) -> None: ...
    def list_briefs(self, incident_id: str) -> list[IncidentBrief]: ...
    def add_notification(self, notification: Notification) -> None: ...
    def list_notifications(self, incident_id: str) -> list[Notification]: ...
    # severity (Round 2: assessments are append-only, supersede by link)
    def add_severity_assessment(self, assessment: SeverityAssessment) -> None: ...
    def get_severity_assessment(self, assessment_id: str) -> SeverityAssessment | None: ...
    def list_severity_assessments(self, incident_id: str) -> list[SeverityAssessment]: ...
    def add_severity_acknowledgement(self, ack: SeverityAcknowledgement) -> None: ...
    def list_severity_acknowledgements(
        self, incident_id: str | None = None
    ) -> list[SeverityAcknowledgement]: ...
    # cause classification (Round 3: one artefact per incident per pack)
    def add_cause_classification(self, classification: CauseClassification) -> None: ...
    def get_cause_classification(
        self, incident_id: str
    ) -> CauseClassification | None: ...
    # memory records (Round 3: insert-only — there is deliberately
    # no update and no delete on this port)
    def add_memory_record(self, record: MemoryRecord) -> MemoryRecord: ...
    def get_memory_record(self, record_id: str) -> MemoryRecord | None: ...
    def list_memory_records(self) -> list[MemoryRecord]: ...
    # evidence packs (Round 3: versions supersede by link, never edited)
    def add_evidence_pack(self, pack: EvidencePack) -> None: ...
    def get_evidence_pack(self, pack_id: str) -> EvidencePack | None: ...
    def list_evidence_packs(self, incident_id: str) -> list[EvidencePack]: ...
    # ETR (Round 4: estimates append-only, superseding by link;
    # one closure act per incident; outcomes feed the ledger)
    def add_etr_estimate(self, estimate: EtrEstimate) -> None: ...
    def get_etr_estimate(self, estimate_id: str) -> EtrEstimate | None: ...
    def list_etr_estimates(self, incident_id: str) -> list[EtrEstimate]: ...
    def add_incident_closure(self, closure: IncidentClosure) -> None: ...
    def get_incident_closure(self, incident_id: str) -> IncidentClosure | None: ...
    def list_incident_closures(self) -> list[IncidentClosure]: ...
    def add_etr_outcome(self, outcome: EtrOutcome) -> None: ...
    def list_etr_outcomes(
        self, incident_id: str | None = None
    ) -> list[EtrOutcome]: ...
    # work orders (Round 5: drafts are insert-only versions; the
    # assignment and decision acts are their own records — the port
    # exposes no update or delete for any of them)
    def add_work_order(self, order: WorkOrder) -> None: ...
    def get_work_order(self, order_id: str) -> WorkOrder | None: ...
    def list_work_orders(self, incident_id: str) -> list[WorkOrder]: ...
    def list_all_work_orders(self) -> list[WorkOrder]: ...
    def add_work_order_assignment(self, assignment: WorkOrderAssignment) -> None: ...
    def list_work_order_assignments(
        self, order_id: str | None = None
    ) -> list[WorkOrderAssignment]: ...
    def add_work_order_decision(self, decision: WorkOrderDecision) -> None: ...
    def list_work_order_decisions(
        self, order_id: str | None = None
    ) -> list[WorkOrderDecision]: ...
    # field updates, topology declarations, revisions (Round 6:
    # insert-only artefacts and acts — no update, no delete)
    def add_field_update(self, update: FieldUpdate) -> None: ...
    def list_field_updates(self, incident_id: str) -> list[FieldUpdate]: ...
    def add_topology_declaration(self, declaration: TopologyDeclaration) -> None: ...
    def list_topology_declarations(
        self, version: str | None = None
    ) -> list[TopologyDeclaration]: ...
    def add_incident_revision(self, revision: IncidentRevision) -> None: ...
    def get_incident_revision(self, revision_id: str) -> IncidentRevision | None: ...
    def list_incident_revisions(self) -> list[IncidentRevision]: ...
    # restoration, gated close, completions, splits,
    # contradictions (Round 7: signals and confirmations are
    # insert-only artefacts; the close act, completion acts,
    # split records and contradiction records are insert-only
    # acts — the port exposes no update or delete for any of them)
    def add_work_order_completion(
        self, completion: WorkOrderCompletion
    ) -> None: ...
    def list_work_order_completions(
        self, order_id: str | None = None
    ) -> list[WorkOrderCompletion]: ...
    def add_restoration_signal(self, signal: RestorationSignal) -> None: ...
    def list_restoration_signals(
        self, incident_id: str
    ) -> list[RestorationSignal]: ...
    def add_restoration_confirmation(
        self, confirmation: RestorationConfirmation
    ) -> None: ...
    def list_restoration_confirmations(
        self, incident_id: str
    ) -> list[RestorationConfirmation]: ...
    def add_incident_close_act(self, act: IncidentCloseAct) -> None: ...
    def get_incident_close_act(
        self, incident_id: str
    ) -> IncidentCloseAct | None: ...
    def add_incident_split(self, split: IncidentSplit) -> None: ...
    def get_incident_split(self, split_id: str) -> IncidentSplit | None: ...
    def list_incident_splits(self) -> list[IncidentSplit]: ...
    def add_post_close_contradiction(
        self, contradiction: PostCloseContradiction
    ) -> None: ...
    def list_post_close_contradictions(
        self,
    ) -> list[PostCloseContradiction]: ...
    # storm declarations and reviews (Round 8: the declaration
    # acts and the post-storm reviews are insert-only — the port
    # exposes no update and no delete for either)
    def add_storm_declaration(self, declaration: StormDeclaration) -> None: ...
    def list_storm_declarations(self) -> list[StormDeclaration]: ...
    def add_storm_review(self, review: StormReview) -> None: ...
    def get_storm_review(self, review_id: str) -> StormReview | None: ...
    def list_storm_reviews(
        self, declaration_id: str | None = None
    ) -> list[StormReview]: ...
    # topology index
    def load_topology(self, payload: dict, loaded_at: datetime) -> None: ...
    def get_topology_payload(self, version: str) -> dict | None: ...
    def list_topology_versions(self) -> list[str]: ...


_ALARM_COLS = (
    "alarm_id, stream_id, device_id, feeder_id, kind, severity,"
    " raised_at, message, payload"
)
_INCIDENT_COLS = (
    "incident_id, feeder_id, substation_id, topology_version,"
    " correlation_pack_version, anchor_alarm_id, status, created_at,"
    " operating_mode, storm_pack_version, storm_declaration_id"
)
_EVIDENCE_COLS = (
    "item_id, incident_id, kind, object_key, payload_sha256,"
    " prev_hash, item_hash, recorded_on"
)


def _alarm_from_row(row) -> Alarm:
    return Alarm.model_validate(json.loads(row[8]))


def _incident_from_row(row) -> Incident:
    return Incident(
        incident_id=row[0],
        feeder_id=row[1],
        substation_id=row[2],
        topology_version=row[3],
        correlation_pack_version=row[4],
        anchor_alarm_id=row[5],
        status=IncidentStatus(row[6]),
        created_at=datetime.fromisoformat(row[7]),
        operating_mode=row[8],
        storm_pack_version=row[9],
        storm_declaration_id=row[10],
    )


def _evidence_from_row(row) -> EvidenceItem:
    return EvidenceItem(
        item_id=row[0],
        incident_id=row[1],
        kind=row[2],
        object_key=row[3],
        payload_sha256=row[4],
        prev_hash=row[5],
        item_hash=row[6],
        recorded_on=datetime.fromisoformat(row[7]),
    )


class SqlStore:
    """SQLite and PostgreSQL behind one implementation.

    ``dialect`` is ``"sqlite"`` or ``"postgres"``. For PostgreSQL
    pass a psycopg connection factory via ``pg_conn`` — connections
    use ``?``-style placeholders translated here, and rows are read
    by position in a fixed column order (the JSON-text contract
    every dialect shares).
    """

    def __init__(self, dialect: str, sqlite_path: str | None = None, pg_conn=None):
        self.dialect = dialect
        self._pg_conn = pg_conn
        self._sqlite_path = sqlite_path
        if dialect == "sqlite":
            Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(sqlite_path)
            try:
                apply_migrations(conn, "sqlite")
            finally:
                conn.close()
        elif dialect == "postgres":
            conn = self._connect()
            try:
                apply_migrations(conn, "postgres")
            finally:
                conn.close()
        else:
            raise ValueError(f"unknown dialect: {dialect}")

    def _connect(self):
        if self.dialect == "sqlite":
            return sqlite3.connect(self._sqlite_path)
        return self._pg_conn()

    def _run(self, sql: str, params: tuple = (), fetch: str | None = None):
        conn = self._connect()
        try:
            cur = (
                conn.execute(sql, params)
                if self.dialect == "sqlite"
                else self._pg_execute(conn, sql, params)
            )
            result = None
            if fetch == "one":
                row = cur.fetchone()
                result = tuple(row) if row is not None else None
            elif fetch == "all":
                result = [tuple(r) for r in cur.fetchall()]
            conn.commit()
            return result
        finally:
            conn.close()

    def _pg_execute(self, conn, sql: str, params: tuple):
        cur = conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    # -- alarms (append-only) --------------------------------------
    def add_alarm(self, alarm: Alarm) -> int:
        row = self._run("SELECT COALESCE(MAX(ingest_seq), 0) FROM alarms", fetch="one")
        seq = int(row[0]) + 1
        self._run(
            f"INSERT INTO alarms ({_ALARM_COLS}, ingest_seq) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                alarm.alarm_id,
                alarm.stream_id,
                alarm.device_id,
                alarm.feeder_id,
                alarm.kind,
                alarm.severity,
                alarm.raised_at.isoformat(),
                alarm.message,
                alarm.model_dump_json(),
                seq,
            ),
        )
        return seq

    def get_alarm(self, alarm_id: str) -> Alarm | None:
        row = self._run(
            f"SELECT {_ALARM_COLS} FROM alarms WHERE alarm_id = ?", (alarm_id,), fetch="one"
        )
        return _alarm_from_row(row) if row else None

    def list_alarms(self) -> list[Alarm]:
        rows = self._run(
            f"SELECT {_ALARM_COLS} FROM alarms ORDER BY ingest_seq", fetch="all"
        )
        return [_alarm_from_row(r) for r in rows]

    def list_uncorrelated_alarms(self) -> list[Alarm]:
        rows = self._run(
            f"SELECT {_ALARM_COLS} FROM alarms a WHERE NOT EXISTS"
            " (SELECT 1 FROM incident_alarms m WHERE m.alarm_id = a.alarm_id)"
            " ORDER BY ingest_seq",
            fetch="all",
        )
        return [_alarm_from_row(r) for r in rows]

    # -- incidents ---------------------------------------------------
    def add_incident(self, incident: Incident) -> None:
        self._run(
            f"INSERT INTO incidents ({_INCIDENT_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                incident.incident_id,
                incident.feeder_id,
                incident.substation_id,
                incident.topology_version,
                incident.correlation_pack_version,
                incident.anchor_alarm_id,
                incident.status.value,
                incident.created_at.isoformat(),
                incident.operating_mode,
                incident.storm_pack_version,
                incident.storm_declaration_id,
            ),
        )

    def get_incident(self, incident_id: str) -> Incident | None:
        row = self._run(
            f"SELECT {_INCIDENT_COLS} FROM incidents WHERE incident_id = ?",
            (incident_id,),
            fetch="one",
        )
        return _incident_from_row(row) if row else None

    def list_incidents(self) -> list[Incident]:
        rows = self._run(
            f"SELECT {_INCIDENT_COLS} FROM incidents ORDER BY incident_id", fetch="all"
        )
        return [_incident_from_row(r) for r in rows]

    def add_membership(self, membership: IncidentMembership) -> None:
        self._run(
            "INSERT INTO incident_alarms (incident_id, alarm_id, rule_id) VALUES (?,?,?)",
            (membership.incident_id, membership.alarm_id, membership.rule_id),
        )

    def list_memberships(self, incident_id: str) -> list[IncidentMembership]:
        rows = self._run(
            "SELECT incident_id, alarm_id, rule_id FROM incident_alarms"
            " WHERE incident_id = ? ORDER BY alarm_id",
            (incident_id,),
            fetch="all",
        )
        return [
            IncidentMembership(incident_id=r[0], alarm_id=r[1], rule_id=r[2])
            for r in rows
        ]

    def membership_for_alarm(self, alarm_id: str) -> IncidentMembership | None:
        row = self._run(
            "SELECT incident_id, alarm_id, rule_id FROM incident_alarms WHERE alarm_id = ?",
            (alarm_id,),
            fetch="one",
        )
        if row is None:
            return None
        return IncidentMembership(incident_id=row[0], alarm_id=row[1], rule_id=row[2])

    # -- evidence / briefs / notifications ---------------------------
    def add_evidence(self, item: EvidenceItem) -> None:
        self._run(
            f"INSERT INTO evidence_items ({_EVIDENCE_COLS}) VALUES (?,?,?,?,?,?,?,?)",
            (
                item.item_id,
                item.incident_id,
                item.kind,
                item.object_key,
                item.payload_sha256,
                item.prev_hash,
                item.item_hash,
                item.recorded_on.isoformat(),
            ),
        )

    def list_evidence(self, incident_id: str) -> list[EvidenceItem]:
        rows = self._run(
            f"SELECT {_EVIDENCE_COLS} FROM evidence_items WHERE incident_id = ?"
            " ORDER BY rowid" if self.dialect == "sqlite" else
            f"SELECT {_EVIDENCE_COLS} FROM evidence_items WHERE incident_id = ?"
            " ORDER BY seq",
            (incident_id,),
            fetch="all",
        )
        return [_evidence_from_row(r) for r in rows]

    def add_brief(self, brief: IncidentBrief) -> None:
        self._run(
            "INSERT INTO incident_briefs (brief_id, incident_id, model_profile, payload)"
            " VALUES (?,?,?,?)",
            (brief.brief_id, brief.incident_id, brief.model_profile, brief.model_dump_json()),
        )

    def list_briefs(self, incident_id: str) -> list[IncidentBrief]:
        rows = self._run(
            "SELECT payload FROM incident_briefs WHERE incident_id = ? ORDER BY brief_id",
            (incident_id,),
            fetch="all",
        )
        return [IncidentBrief.model_validate(json.loads(r[0])) for r in rows]

    def add_notification(self, notification: Notification) -> None:
        self._run(
            "INSERT INTO notifications (notification_id, incident_id, channel, body, created_at)"
            " VALUES (?,?,?,?,?)",
            (
                notification.notification_id,
                notification.incident_id,
                notification.channel,
                notification.body,
                notification.created_at.isoformat(),
            ),
        )

    def list_notifications(self, incident_id: str) -> list[Notification]:
        rows = self._run(
            "SELECT notification_id, incident_id, channel, body, created_at"
            " FROM notifications WHERE incident_id = ? ORDER BY notification_id",
            (incident_id,),
            fetch="all",
        )
        return [
            Notification(
                notification_id=r[0],
                incident_id=r[1],
                channel=r[2],
                body=r[3],
                created_at=datetime.fromisoformat(r[4]),
            )
            for r in rows
        ]

    # -- severity (Round 2) --------------------------------------------
    def add_severity_assessment(self, assessment: SeverityAssessment) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(assessed_seq), 0) FROM severity_assessments",
            fetch="one",
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO severity_assessments (assessment_id, incident_id, assessed_seq, payload)"
            " VALUES (?,?,?,?)",
            (
                assessment.assessment_id,
                assessment.incident_id,
                seq,
                assessment.model_dump_json(),
            ),
        )

    def get_severity_assessment(self, assessment_id: str) -> SeverityAssessment | None:
        row = self._run(
            "SELECT payload FROM severity_assessments WHERE assessment_id = ?",
            (assessment_id,),
            fetch="one",
        )
        return SeverityAssessment.model_validate(json.loads(row[0])) if row else None

    def list_severity_assessments(self, incident_id: str) -> list[SeverityAssessment]:
        rows = self._run(
            "SELECT payload FROM severity_assessments WHERE incident_id = ?"
            " ORDER BY assessed_seq",
            (incident_id,),
            fetch="all",
        )
        return [SeverityAssessment.model_validate(json.loads(r[0])) for r in rows]

    def add_severity_acknowledgement(self, ack: SeverityAcknowledgement) -> None:
        self._run(
            "INSERT INTO severity_acknowledgements"
            " (ack_id, assessment_id, incident_id, acknowledged_by, role, acknowledged_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                ack.ack_id,
                ack.assessment_id,
                ack.incident_id,
                ack.acknowledged_by,
                ack.role,
                ack.acknowledged_at.isoformat(),
            ),
        )

    def list_severity_acknowledgements(
        self, incident_id: str | None = None
    ) -> list[SeverityAcknowledgement]:
        if incident_id is None:
            rows = self._run(
                "SELECT ack_id, assessment_id, incident_id, acknowledged_by, role,"
                " acknowledged_at FROM severity_acknowledgements ORDER BY ack_id",
                fetch="all",
            )
        else:
            rows = self._run(
                "SELECT ack_id, assessment_id, incident_id, acknowledged_by, role,"
                " acknowledged_at FROM severity_acknowledgements"
                " WHERE incident_id = ? ORDER BY ack_id",
                (incident_id,),
                fetch="all",
            )
        return [
            SeverityAcknowledgement(
                ack_id=r[0],
                assessment_id=r[1],
                incident_id=r[2],
                acknowledged_by=r[3],
                role=r[4],
                acknowledged_at=datetime.fromisoformat(r[5]),
            )
            for r in rows
        ]

    # -- cause classification (Round 3) --------------------------------
    def add_cause_classification(self, classification: CauseClassification) -> None:
        self._run(
            "INSERT INTO cause_classifications (classification_id, incident_id, payload)"
            " VALUES (?,?,?)",
            (
                classification.classification_id,
                classification.incident_id,
                classification.model_dump_json(),
            ),
        )

    def get_cause_classification(
        self, incident_id: str
    ) -> CauseClassification | None:
        row = self._run(
            "SELECT payload FROM cause_classifications WHERE incident_id = ?",
            (incident_id,),
            fetch="one",
        )
        return CauseClassification.model_validate(json.loads(row[0])) if row else None

    # -- memory records (Round 3: insert-only) ---------------------------
    def add_memory_record(self, record: MemoryRecord) -> MemoryRecord:
        existing = self.get_memory_record(record.record_id)
        if existing is not None:
            if existing.model_dump(mode="json") != record.model_dump(mode="json"):
                raise MemoryImmutableError(
                    f"memory record {record.record_id} already exists with "
                    "different facts — memory records are never rewritten"
                )
            return existing  # identical re-insert: a no-op, not an edit
        self._run(
            "INSERT INTO memory_records (record_id, incident_id, feeder_id, payload)"
            " VALUES (?,?,?,?)",
            (
                record.record_id,
                record.incident_id,
                record.feeder_id,
                record.model_dump_json(),
            ),
        )
        return record

    def get_memory_record(self, record_id: str) -> MemoryRecord | None:
        row = self._run(
            "SELECT payload FROM memory_records WHERE record_id = ?",
            (record_id,),
            fetch="one",
        )
        return MemoryRecord.model_validate(json.loads(row[0])) if row else None

    def list_memory_records(self) -> list[MemoryRecord]:
        rows = self._run(
            "SELECT payload FROM memory_records ORDER BY record_id", fetch="all"
        )
        return [MemoryRecord.model_validate(json.loads(r[0])) for r in rows]

    # -- evidence packs (Round 3) ----------------------------------------
    def add_evidence_pack(self, pack: EvidencePack) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(sealed_seq), 0) FROM evidence_packs", fetch="one"
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO evidence_packs (pack_id, incident_id, version, sealed_seq, payload)"
            " VALUES (?,?,?,?,?)",
            (
                pack.pack_id,
                pack.incident_id,
                pack.version,
                seq,
                pack.model_dump_json(),
            ),
        )

    def get_evidence_pack(self, pack_id: str) -> EvidencePack | None:
        row = self._run(
            "SELECT payload FROM evidence_packs WHERE pack_id = ?",
            (pack_id,),
            fetch="one",
        )
        return EvidencePack.model_validate(json.loads(row[0])) if row else None

    def list_evidence_packs(self, incident_id: str) -> list[EvidencePack]:
        rows = self._run(
            "SELECT payload FROM evidence_packs WHERE incident_id = ?"
            " ORDER BY sealed_seq",
            (incident_id,),
            fetch="all",
        )
        return [EvidencePack.model_validate(json.loads(r[0])) for r in rows]

    # -- ETR estimates, closures, outcomes (Round 4) -------------------
    def add_etr_estimate(self, estimate: EtrEstimate) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(estimated_seq), 0) FROM etr_estimates", fetch="one"
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO etr_estimates (estimate_id, incident_id, estimated_seq, payload)"
            " VALUES (?,?,?,?)",
            (
                estimate.estimate_id,
                estimate.incident_id,
                seq,
                estimate.model_dump_json(),
            ),
        )

    def get_etr_estimate(self, estimate_id: str) -> EtrEstimate | None:
        row = self._run(
            "SELECT payload FROM etr_estimates WHERE estimate_id = ?",
            (estimate_id,),
            fetch="one",
        )
        return EtrEstimate.model_validate(json.loads(row[0])) if row else None

    def list_etr_estimates(self, incident_id: str) -> list[EtrEstimate]:
        rows = self._run(
            "SELECT payload FROM etr_estimates WHERE incident_id = ?"
            " ORDER BY estimated_seq",
            (incident_id,),
            fetch="all",
        )
        return [EtrEstimate.model_validate(json.loads(r[0])) for r in rows]

    def add_incident_closure(self, closure: IncidentClosure) -> None:
        self._run(
            "INSERT INTO incident_closures (closure_id, incident_id, payload)"
            " VALUES (?,?,?)",
            (
                closure.closure_id,
                closure.incident_id,
                closure.model_dump_json(),
            ),
        )

    def get_incident_closure(self, incident_id: str) -> IncidentClosure | None:
        row = self._run(
            "SELECT payload FROM incident_closures WHERE incident_id = ?",
            (incident_id,),
            fetch="one",
        )
        return IncidentClosure.model_validate(json.loads(row[0])) if row else None

    def list_incident_closures(self) -> list[IncidentClosure]:
        rows = self._run(
            "SELECT payload FROM incident_closures ORDER BY incident_id", fetch="all"
        )
        return [IncidentClosure.model_validate(json.loads(r[0])) for r in rows]

    def add_etr_outcome(self, outcome: EtrOutcome) -> None:
        self._run(
            "INSERT INTO etr_outcomes (outcome_id, incident_id, closure_id, payload)"
            " VALUES (?,?,?,?)",
            (
                outcome.outcome_id,
                outcome.incident_id,
                outcome.closure_id,
                outcome.model_dump_json(),
            ),
        )

    def list_etr_outcomes(
        self, incident_id: str | None = None
    ) -> list[EtrOutcome]:
        if incident_id is None:
            rows = self._run(
                "SELECT payload FROM etr_outcomes ORDER BY outcome_id", fetch="all"
            )
        else:
            rows = self._run(
                "SELECT payload FROM etr_outcomes WHERE incident_id = ?"
                " ORDER BY outcome_id",
                (incident_id,),
                fetch="all",
            )
        return [EtrOutcome.model_validate(json.loads(r[0])) for r in rows]

    # -- work orders (Round 5) ---------------------------------------
    def add_work_order(self, order: WorkOrder) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(drafted_seq), 0) FROM work_orders", fetch="one"
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO work_orders (order_id, incident_id, version, drafted_seq, payload)"
            " VALUES (?,?,?,?,?)",
            (
                order.order_id,
                order.incident_id,
                order.version,
                seq,
                order.model_dump_json(),
            ),
        )

    def get_work_order(self, order_id: str) -> WorkOrder | None:
        row = self._run(
            "SELECT payload FROM work_orders WHERE order_id = ?",
            (order_id,),
            fetch="one",
        )
        return WorkOrder.model_validate(json.loads(row[0])) if row else None

    def list_work_orders(self, incident_id: str) -> list[WorkOrder]:
        rows = self._run(
            "SELECT payload FROM work_orders WHERE incident_id = ?"
            " ORDER BY drafted_seq",
            (incident_id,),
            fetch="all",
        )
        return [WorkOrder.model_validate(json.loads(r[0])) for r in rows]

    def list_all_work_orders(self) -> list[WorkOrder]:
        rows = self._run(
            "SELECT payload FROM work_orders ORDER BY drafted_seq", fetch="all"
        )
        return [WorkOrder.model_validate(json.loads(r[0])) for r in rows]

    def add_work_order_assignment(self, assignment: WorkOrderAssignment) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(act_seq), 0) FROM work_order_assignments",
            fetch="one",
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO work_order_assignments"
            " (assignment_id, order_id, crew_id, act_seq, payload)"
            " VALUES (?,?,?,?,?)",
            (
                assignment.assignment_id,
                assignment.order_id,
                assignment.crew_id,
                seq,
                assignment.model_dump_json(),
            ),
        )

    def list_work_order_assignments(
        self, order_id: str | None = None
    ) -> list[WorkOrderAssignment]:
        if order_id is None:
            rows = self._run(
                "SELECT payload FROM work_order_assignments ORDER BY act_seq",
                fetch="all",
            )
        else:
            rows = self._run(
                "SELECT payload FROM work_order_assignments WHERE order_id = ?"
                " ORDER BY act_seq",
                (order_id,),
                fetch="all",
            )
        return [WorkOrderAssignment.model_validate(json.loads(r[0])) for r in rows]

    def add_work_order_decision(self, decision: WorkOrderDecision) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(act_seq), 0) FROM work_order_decisions",
            fetch="one",
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO work_order_decisions (decision_id, order_id, act_seq, payload)"
            " VALUES (?,?,?,?)",
            (
                decision.decision_id,
                decision.order_id,
                seq,
                decision.model_dump_json(),
            ),
        )

    def list_work_order_decisions(
        self, order_id: str | None = None
    ) -> list[WorkOrderDecision]:
        if order_id is None:
            rows = self._run(
                "SELECT payload FROM work_order_decisions ORDER BY act_seq",
                fetch="all",
            )
        else:
            rows = self._run(
                "SELECT payload FROM work_order_decisions WHERE order_id = ?"
                " ORDER BY act_seq",
                (order_id,),
                fetch="all",
            )
        return [WorkOrderDecision.model_validate(json.loads(r[0])) for r in rows]

    # -- field updates, declarations, revisions (Round 6) ------------
    def add_field_update(self, update: FieldUpdate) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(act_seq), 0) FROM field_updates", fetch="one"
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO field_updates (update_id, incident_id, kind, act_seq, payload)"
            " VALUES (?,?,?,?,?)",
            (update.update_id, update.incident_id, update.kind, seq, update.model_dump_json()),
        )

    def list_field_updates(self, incident_id: str) -> list[FieldUpdate]:
        rows = self._run(
            "SELECT payload FROM field_updates WHERE incident_id = ?"
            " ORDER BY act_seq",
            (incident_id,),
            fetch="all",
        )
        return [FieldUpdate.model_validate(json.loads(r[0])) for r in rows]

    def add_topology_declaration(self, declaration: TopologyDeclaration) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(act_seq), 0) FROM topology_declarations",
            fetch="one",
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO topology_declarations (declaration_id, version, act_seq, payload)"
            " VALUES (?,?,?,?)",
            (
                declaration.declaration_id,
                declaration.version,
                seq,
                declaration.model_dump_json(),
            ),
        )

    def list_topology_declarations(
        self, version: str | None = None
    ) -> list[TopologyDeclaration]:
        if version is None:
            rows = self._run(
                "SELECT payload FROM topology_declarations ORDER BY act_seq",
                fetch="all",
            )
        else:
            rows = self._run(
                "SELECT payload FROM topology_declarations WHERE version = ?"
                " ORDER BY act_seq",
                (version,),
                fetch="all",
            )
        return [TopologyDeclaration.model_validate(json.loads(r[0])) for r in rows]

    def add_incident_revision(self, revision: IncidentRevision) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(act_seq), 0) FROM incident_revisions", fetch="one"
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO incident_revisions"
            " (revision_id, prior_incident_id, revised_incident_id, act_seq, payload)"
            " VALUES (?,?,?,?,?)",
            (
                revision.revision_id,
                revision.prior_incident_id,
                revision.revised_incident_id,
                seq,
                revision.model_dump_json(),
            ),
        )

    def get_incident_revision(self, revision_id: str) -> IncidentRevision | None:
        row = self._run(
            "SELECT payload FROM incident_revisions WHERE revision_id = ?",
            (revision_id,),
            fetch="one",
        )
        return IncidentRevision.model_validate(json.loads(row[0])) if row else None

    def list_incident_revisions(self) -> list[IncidentRevision]:
        rows = self._run(
            "SELECT payload FROM incident_revisions ORDER BY act_seq", fetch="all"
        )
        return [IncidentRevision.model_validate(json.loads(r[0])) for r in rows]

    # -- restoration, gated close, splits, contradictions (Round 7) --
    def _next_seq(self, table: str) -> int:
        row = self._run(
            f"SELECT COALESCE(MAX(act_seq), 0) FROM {table}", fetch="one"
        )
        return int(row[0]) + 1

    def add_work_order_completion(self, completion: WorkOrderCompletion) -> None:
        self._run(
            "INSERT INTO work_order_completions"
            " (completion_id, order_id, incident_id, act_seq, payload)"
            " VALUES (?,?,?,?,?)",
            (
                completion.completion_id,
                completion.order_id,
                completion.incident_id,
                self._next_seq("work_order_completions"),
                completion.model_dump_json(),
            ),
        )

    def list_work_order_completions(
        self, order_id: str | None = None
    ) -> list[WorkOrderCompletion]:
        if order_id is None:
            rows = self._run(
                "SELECT payload FROM work_order_completions ORDER BY act_seq",
                fetch="all",
            )
        else:
            rows = self._run(
                "SELECT payload FROM work_order_completions"
                " WHERE order_id = ? ORDER BY act_seq",
                (order_id,),
                fetch="all",
            )
        return [WorkOrderCompletion.model_validate(json.loads(r[0])) for r in rows]

    def add_restoration_signal(self, signal: RestorationSignal) -> None:
        self._run(
            "INSERT INTO restoration_signals"
            " (signal_id, incident_id, source, act_seq, payload)"
            " VALUES (?,?,?,?,?)",
            (
                signal.signal_id,
                signal.incident_id,
                signal.source,
                self._next_seq("restoration_signals"),
                signal.model_dump_json(),
            ),
        )

    def list_restoration_signals(
        self, incident_id: str
    ) -> list[RestorationSignal]:
        rows = self._run(
            "SELECT payload FROM restoration_signals WHERE incident_id = ?"
            " ORDER BY act_seq",
            (incident_id,),
            fetch="all",
        )
        return [RestorationSignal.model_validate(json.loads(r[0])) for r in rows]

    def add_restoration_confirmation(
        self, confirmation: RestorationConfirmation
    ) -> None:
        self._run(
            "INSERT INTO restoration_confirmations"
            " (confirmation_id, incident_id, act_seq, payload)"
            " VALUES (?,?,?,?)",
            (
                confirmation.confirmation_id,
                confirmation.incident_id,
                self._next_seq("restoration_confirmations"),
                confirmation.model_dump_json(),
            ),
        )

    def list_restoration_confirmations(
        self, incident_id: str
    ) -> list[RestorationConfirmation]:
        rows = self._run(
            "SELECT payload FROM restoration_confirmations"
            " WHERE incident_id = ? ORDER BY act_seq",
            (incident_id,),
            fetch="all",
        )
        return [
            RestorationConfirmation.model_validate(json.loads(r[0])) for r in rows
        ]

    def add_incident_close_act(self, act: IncidentCloseAct) -> None:
        self._run(
            "INSERT INTO incident_close_acts"
            " (act_id, incident_id, act_seq, payload) VALUES (?,?,?,?)",
            (
                act.act_id,
                act.incident_id,
                self._next_seq("incident_close_acts"),
                act.model_dump_json(),
            ),
        )

    def get_incident_close_act(
        self, incident_id: str
    ) -> IncidentCloseAct | None:
        row = self._run(
            "SELECT payload FROM incident_close_acts WHERE incident_id = ?",
            (incident_id,),
            fetch="one",
        )
        return IncidentCloseAct.model_validate(json.loads(row[0])) if row else None

    def add_incident_split(self, split: IncidentSplit) -> None:
        self._run(
            "INSERT INTO incident_splits"
            " (split_id, incident_id, restored_incident_id,"
            " continuing_incident_id, act_seq, payload)"
            " VALUES (?,?,?,?,?,?)",
            (
                split.split_id,
                split.incident_id,
                split.restored_incident_id,
                split.continuing_incident_id,
                self._next_seq("incident_splits"),
                split.model_dump_json(),
            ),
        )

    def get_incident_split(self, split_id: str) -> IncidentSplit | None:
        row = self._run(
            "SELECT payload FROM incident_splits WHERE split_id = ?",
            (split_id,),
            fetch="one",
        )
        return IncidentSplit.model_validate(json.loads(row[0])) if row else None

    def list_incident_splits(self) -> list[IncidentSplit]:
        rows = self._run(
            "SELECT payload FROM incident_splits ORDER BY act_seq", fetch="all"
        )
        return [IncidentSplit.model_validate(json.loads(r[0])) for r in rows]

    def add_post_close_contradiction(
        self, contradiction: PostCloseContradiction
    ) -> None:
        self._run(
            "INSERT INTO post_close_contradictions"
            " (contradiction_id, closed_incident_id, fresh_incident_id,"
            " act_seq, payload) VALUES (?,?,?,?,?)",
            (
                contradiction.contradiction_id,
                contradiction.closed_incident_id,
                contradiction.fresh_incident_id,
                self._next_seq("post_close_contradictions"),
                contradiction.model_dump_json(),
            ),
        )

    def list_post_close_contradictions(
        self,
    ) -> list[PostCloseContradiction]:
        rows = self._run(
            "SELECT payload FROM post_close_contradictions ORDER BY act_seq",
            fetch="all",
        )
        return [
            PostCloseContradiction.model_validate(json.loads(r[0])) for r in rows
        ]

    # -- topology index ------------------------------------------------
    def load_topology(self, payload: dict, loaded_at: datetime) -> None:
        version = payload["version"]
        existing = self._run(
            "SELECT version FROM topology_versions WHERE version = ?",
            (version,),
            fetch="one",
        )
        if existing:
            return  # a version is immutable once loaded (Law 6)
        self._run(
            "INSERT INTO topology_versions (version, payload, loaded_at) VALUES (?,?,?)",
            (version, json.dumps(payload, sort_keys=True), loaded_at.isoformat()),
        )
        for s in payload["substations"]:
            self._run(
                "INSERT INTO topology_substations (version, substation_id, name) VALUES (?,?,?)",
                (version, s["substation_id"], s["name"]),
            )
        for f in payload["feeders"]:
            self._run(
                "INSERT INTO topology_feeders (version, feeder_id, substation_id, name) VALUES (?,?,?,?)",
                (version, f["feeder_id"], f["substation_id"], f["name"]),
            )
        for d in payload["devices"]:
            self._run(
                "INSERT INTO topology_devices (version, device_id, feeder_id, parent_device_id, kind, name, customers)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    version,
                    d["device_id"],
                    d["feeder_id"],
                    d.get("parent_device_id"),
                    d["kind"],
                    d["name"],
                    d.get("customers"),
                ),
            )
        for load in payload.get("critical_loads", []):
            self._run(
                "INSERT INTO topology_critical_loads (version, load_id, feeder_id, kind, name, device_id)"
                " VALUES (?,?,?,?,?,?)",
                (
                    version,
                    load["load_id"],
                    load["feeder_id"],
                    load["kind"],
                    load["name"],
                    load.get("device_id"),
                ),
            )

    def get_topology_payload(self, version: str) -> dict | None:
        row = self._run(
            "SELECT payload FROM topology_versions WHERE version = ?",
            (version,),
            fetch="one",
        )
        return json.loads(row[0]) if row else None

    def list_topology_versions(self) -> list[str]:
        rows = self._run(
            "SELECT version FROM topology_versions ORDER BY version", fetch="all"
        )
        return [r[0] for r in rows]

    def list_devices(self, version: str) -> list[dict]:
        rows = self._run(
            "SELECT device_id, feeder_id, parent_device_id, kind, name, customers"
            " FROM topology_devices WHERE version = ? ORDER BY device_id",
            (version,),
            fetch="all",
        )
        return [
            {
                "device_id": r[0],
                "feeder_id": r[1],
                "parent_device_id": r[2],
                "kind": r[3],
                "name": r[4],
                "customers": r[5],
            }
            for r in rows
        ]

    # -- storm declarations and reviews (Round 8) --------------------
    def add_storm_declaration(self, declaration: StormDeclaration) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(act_seq), 0) FROM storm_declarations",
            fetch="one",
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO storm_declarations"
            " (declaration_id, kind, opens_declaration_id, act_seq, payload)"
            " VALUES (?,?,?,?,?)",
            (
                declaration.declaration_id,
                declaration.kind,
                declaration.opens_declaration_id,
                seq,
                declaration.model_dump_json(),
            ),
        )

    def list_storm_declarations(self) -> list[StormDeclaration]:
        rows = self._run(
            "SELECT payload FROM storm_declarations ORDER BY act_seq",
            fetch="all",
        )
        return [StormDeclaration.model_validate(json.loads(r[0])) for r in rows]

    def add_storm_review(self, review: StormReview) -> None:
        row = self._run(
            "SELECT COALESCE(MAX(act_seq), 0) FROM storm_reviews", fetch="one"
        )
        seq = int(row[0]) + 1
        self._run(
            "INSERT INTO storm_reviews"
            " (review_id, declaration_id, act_seq, payload)"
            " VALUES (?,?,?,?)",
            (
                review.review_id,
                review.declaration_id,
                seq,
                review.model_dump_json(),
            ),
        )

    def get_storm_review(self, review_id: str) -> StormReview | None:
        row = self._run(
            "SELECT payload FROM storm_reviews WHERE review_id = ?",
            (review_id,),
            fetch="one",
        )
        return StormReview.model_validate(json.loads(row[0])) if row else None

    def list_storm_reviews(
        self, declaration_id: str | None = None
    ) -> list[StormReview]:
        if declaration_id is None:
            rows = self._run(
                "SELECT payload FROM storm_reviews ORDER BY act_seq",
                fetch="all",
            )
        else:
            rows = self._run(
                "SELECT payload FROM storm_reviews WHERE declaration_id = ?"
                " ORDER BY act_seq",
                (declaration_id,),
                fetch="all",
            )
        return [StormReview.model_validate(json.loads(r[0])) for r in rows]
