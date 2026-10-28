"""The operations API: the control room's surface over the same
service graph the Runtime entrypoint uses.

This FastAPI app is the local/operations API (ingest, alarms,
incidents, topology versions, brief assembly, chain verification;
Round 3 adds classification, memory records, priors and evidence
pack seal/verify; Round 4 adds ETR estimates, the closure act and
the accuracy ledger; Round 5 adds work-order drafts, crew
assignment acts and the operator approval gate).
The agent itself is invoked through the AgentCore Runtime app
(``grid_triage.runtime_app``); this API exists so operators and
tests can see and drive the triage work without an AWS account.
Every route here is a read or an intake — there is no route that
operates anything (Law 1).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from grid_triage import __version__
from grid_triage.agent import (
    BandMismatchRefusal,
    CitationRefusal,
    EtrBasisRefusal,
    PriorAttributionRefusal,
)
from grid_triage.closure import ClosureRefusal
from grid_triage.models import Alarm
from grid_triage.packs import PackRefusal
from grid_triage.revisions import RevisionRefusal, TopologyRefusal, UpdateRefusal
from grid_triage.services import DuplicateAlarm
from grid_triage.severity import SeverityRefusal
from grid_triage.topology import TopologyView
from grid_triage.wiring import Services, build_services
from grid_triage.work_orders import WorkOrderRefusal

app = FastAPI(title="Grid Alarm Triage Agent — Operations API", version=__version__)
_svc: Services | None = None


def svc() -> Services:
    global _svc
    if _svc is None:
        _svc = build_services()
    return _svc


def override_services(services: Services) -> None:
    """Tests and the demo inject their own graph."""
    global _svc
    _svc = services
    import grid_triage.runtime_app as runtime_app

    runtime_app._services = services


class IngestResponse(BaseModel):
    ingested: int
    incidents_created: list[str]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/readiness")
def readiness() -> dict:
    svc().store.list_alarms()
    return {"status": "ready"}


@app.post("/ingest/feed", response_model=IngestResponse)
def ingest_feed() -> IngestResponse:
    s = svc()
    try:
        count = s.intake.ingest_feed(s.feed.fetch_alarms())
    except DuplicateAlarm as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    created = s.correlator.correlate_new(s.topology)
    return IngestResponse(
        ingested=count, incidents_created=[i.incident_id for i in created]
    )


@app.post("/alarms", response_model=IngestResponse)
def ingest_alarms(alarms: list[Alarm]) -> IngestResponse:
    s = svc()
    try:
        count = s.intake.ingest(alarms)
    except DuplicateAlarm as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    created = s.correlator.correlate_new(s.topology)
    return IngestResponse(
        ingested=count, incidents_created=[i.incident_id for i in created]
    )


@app.get("/alarms")
def list_alarms(uncorrelated: bool = False) -> list[dict]:
    s = svc()
    alarms = s.store.list_uncorrelated_alarms() if uncorrelated else s.store.list_alarms()
    return [a.model_dump(mode="json") for a in alarms]


@app.get("/alarms/{alarm_id}")
def get_alarm(alarm_id: str) -> dict:
    alarm = svc().store.get_alarm(alarm_id)
    if alarm is None:
        raise HTTPException(status_code=404, detail=f"no such alarm: {alarm_id}")
    body = alarm.model_dump(mode="json")
    membership = svc().store.membership_for_alarm(alarm_id)
    body["incident_id"] = membership.incident_id if membership else None
    body["correlation_rule"] = membership.rule_id if membership else None
    return body


@app.get("/incidents")
def list_incidents() -> list[dict]:
    return [i.model_dump(mode="json") for i in svc().store.list_incidents()]


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str) -> dict:
    s = svc()
    incident = s.store.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    body = incident.model_dump(mode="json")
    body["memberships"] = [
        m.model_dump(mode="json") for m in s.store.list_memberships(incident_id)
    ]
    classification = s.cause.current(incident_id)
    body["cause_classification"] = (
        classification.model_dump(mode="json") if classification else None
    )
    return body


@app.post("/incidents/{incident_id}/brief")
def assemble_brief(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    try:
        brief = s.assembler.assemble(incident_id, datetime.now(timezone.utc))
    except (
        CitationRefusal,
        BandMismatchRefusal,
        PriorAttributionRefusal,
        EtrBasisRefusal,
    ) as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return brief.model_dump(mode="json")


# -- Round 3: classification, memory, priors, evidence packs -----------


@app.post("/incidents/{incident_id}/classify")
def classify_incident(incident_id: str) -> dict:
    s = svc()
    try:
        classification = s.cause.classify(incident_id, datetime.now(timezone.utc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return classification.model_dump(mode="json")


@app.get("/incidents/{incident_id}/classification")
def get_classification(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    classification = s.cause.current(incident_id)
    return {
        "incident_id": incident_id,
        "classification": (
            classification.model_dump(mode="json") if classification else None
        ),
    }


@app.get("/incidents/{incident_id}/priors")
def get_priors(incident_id: str) -> dict:
    s = svc()
    try:
        hits = s.memory.retrieve_for_incident(
            incident_id, datetime.now(timezone.utc)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "incident_id": incident_id,
        "priors": [h.model_dump(mode="json") for h in hits],
    }


@app.post("/memory/seed-history")
def seed_history() -> dict:
    records = svc().memory.load_history()
    return {"loaded": [r.record_id for r in records], "count": len(records)}


@app.get("/memory/records")
def list_memory_records() -> list[dict]:
    return [r.model_dump(mode="json") for r in svc().memory_port.list_records()]


@app.get("/memory/records/{record_id}")
def get_memory_record(record_id: str) -> dict:
    record = svc().memory_port.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no such memory record: {record_id}")
    return record.model_dump(mode="json")


class SealRequest(BaseModel):
    sealed_by: str


@app.post("/incidents/{incident_id}/pack/seal")
def seal_pack(incident_id: str, request: SealRequest) -> dict:
    s = svc()
    try:
        pack = s.packs.seal(
            incident_id, request.sealed_by, datetime.now(timezone.utc)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PackRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return pack.model_dump(mode="json")


@app.get("/incidents/{incident_id}/pack")
def get_current_pack(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    pack = s.packs.current(incident_id)
    return {
        "incident_id": incident_id,
        "pack": pack.model_dump(mode="json") if pack else None,
    }


@app.get("/incidents/{incident_id}/packs")
def list_packs(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    return {
        "incident_id": incident_id,
        "packs": [p.model_dump(mode="json") for p in s.packs.history(incident_id)],
    }


@app.get("/packs/{pack_id}")
def get_pack(pack_id: str) -> dict:
    pack = svc().store.get_evidence_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"no such evidence pack: {pack_id}")
    return pack.model_dump(mode="json")


@app.get("/packs/{pack_id}/verify")
def verify_pack(pack_id: str) -> dict:
    s = svc()
    try:
        problems = s.packs.verify_pack(pack_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"pack_id": pack_id, "valid": not problems, "problems": problems}


class AssessRequest(BaseModel):
    as_of: datetime | None = None


@app.get("/incidents/{incident_id}/severity")
def get_severity(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    history = s.severity.history(incident_id)
    acked = {
        a.assessment_id for a in s.store.list_severity_acknowledgements(incident_id)
    }
    return {
        "incident_id": incident_id,
        "current": history[-1].model_dump(mode="json") if history else None,
        "history": [a.model_dump(mode="json") for a in history],
        "acknowledgements": [
            a.model_dump(mode="json")
            for a in s.store.list_severity_acknowledgements(incident_id)
        ],
        "pending_acknowledgement_ids": [
            a.assessment_id
            for a in history
            if a.escalation_required and a.assessment_id not in acked
        ],
    }


@app.post("/incidents/{incident_id}/severity/assess")
def assess_severity(incident_id: str, request: AssessRequest | None = None) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    as_of = (request.as_of if request else None) or datetime.now(timezone.utc)
    assessment = s.severity.assess(incident_id, as_of)
    return assessment.model_dump(mode="json")


class AcknowledgeRequest(BaseModel):
    assessment_id: str
    acknowledged_by: str
    role: str


@app.get("/severity/acknowledgement-queue")
def acknowledgement_queue() -> dict:
    s = svc()
    return {
        "pending": [
            a.model_dump(mode="json") for a in s.severity.pending_acknowledgements()
        ]
    }


@app.post("/severity/acknowledgements")
def acknowledge_severity(request: AcknowledgeRequest) -> dict:
    s = svc()
    try:
        ack = s.severity.acknowledge(
            request.assessment_id,
            request.acknowledged_by,
            request.role,
            datetime.now(timezone.utc),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SeverityRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return ack.model_dump(mode="json")


# -- Round 4: ETR estimates, closure, the accuracy ledger -------------


class EstimateRequest(BaseModel):
    as_of: datetime | None = None


@app.get("/incidents/{incident_id}/etr")
def get_etr(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    history = s.etr.history(incident_id)
    return {
        "incident_id": incident_id,
        "current": history[-1].model_dump(mode="json") if history else None,
        "history": [e.model_dump(mode="json") for e in history],
    }


@app.post("/incidents/{incident_id}/etr/estimate")
def estimate_etr(incident_id: str, request: EstimateRequest | None = None) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    as_of = (request.as_of if request else None) or datetime.now(timezone.utc)
    estimate = s.etr.estimate(incident_id, as_of)
    return estimate.model_dump(mode="json")


@app.get("/roster")
def get_roster() -> dict:
    return svc().roster.payload


class CloseRequest(BaseModel):
    closed_by: str
    role: str
    restored_at: datetime
    resolution_summary: str


@app.post("/incidents/{incident_id}/close")
def close_incident(incident_id: str, request: CloseRequest) -> dict:
    s = svc()
    try:
        closure, outcome, record = s.closure.close_incident(
            incident_id,
            request.closed_by,
            request.role,
            request.restored_at,
            request.resolution_summary,
            datetime.now(timezone.utc),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ClosureRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return {
        "closure": closure.model_dump(mode="json"),
        "outcome": outcome.model_dump(mode="json"),
        "memory_record": record.model_dump(mode="json"),
    }


@app.get("/incidents/{incident_id}/closure")
def get_closure(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    closure = s.closure.closure_for(incident_id)
    outcomes = s.store.list_etr_outcomes(incident_id)
    return {
        "incident_id": incident_id,
        "closure": closure.model_dump(mode="json") if closure else None,
        "outcome": outcomes[0].model_dump(mode="json") if outcomes else None,
    }


@app.get("/etr/accuracy")
def etr_accuracy() -> dict:
    return svc().closure.accuracy_ledger()


# -- Round 5: work-order drafts, assignment acts, the approval gate ---


class DraftWorkOrderRequest(BaseModel):
    drafted_by: str = "system"
    as_of: datetime | None = None


class ReviseWorkOrderRequest(BaseModel):
    revised_by: str
    operator_note: str | None = None
    extra_materials: list[dict] | None = None
    as_of: datetime | None = None


class AssignWorkOrderRequest(BaseModel):
    crew_id: str
    assigned_by: str
    window_start: datetime
    window_end: datetime


class DecideWorkOrderRequest(BaseModel):
    decided_by: str
    role: str
    reason: str | None = None


@app.post("/incidents/{incident_id}/work-orders")
def draft_work_order(incident_id: str, request: DraftWorkOrderRequest) -> dict:
    s = svc()
    as_of = request.as_of or datetime.now(timezone.utc)
    try:
        order = s.work_orders.draft(incident_id, as_of, request.drafted_by)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkOrderRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return s.work_orders.view(order)


@app.get("/incidents/{incident_id}/work-orders")
def list_incident_work_orders(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    orders = s.store.list_work_orders(incident_id)
    return {
        "incident_id": incident_id,
        "current": s.work_orders.view(orders[-1]) if orders else None,
        "orders": [s.work_orders.view(o) for o in orders],
    }


@app.get("/work-orders")
def list_work_orders(state: str | None = None) -> list[dict]:
    s = svc()
    return [s.work_orders.view(o) for o in s.work_orders.list_by_state(state)]


@app.get("/work-orders/{order_id}")
def get_work_order(order_id: str) -> dict:
    s = svc()
    order = s.store.get_work_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"no such work order: {order_id}")
    return s.work_orders.view(order)


@app.post("/work-orders/{order_id}/revise")
def revise_work_order(order_id: str, request: ReviseWorkOrderRequest) -> dict:
    s = svc()
    as_of = request.as_of or datetime.now(timezone.utc)
    try:
        order = s.work_orders.revise(
            order_id,
            request.revised_by,
            as_of,
            operator_note=request.operator_note,
            extra_materials=request.extra_materials,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkOrderRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return s.work_orders.view(order)


@app.post("/work-orders/{order_id}/assign")
def assign_work_order(order_id: str, request: AssignWorkOrderRequest) -> dict:
    s = svc()
    try:
        assignment = s.work_orders.assign(
            order_id,
            request.crew_id,
            request.assigned_by,
            request.window_start,
            request.window_end,
            datetime.now(timezone.utc),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkOrderRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return assignment.model_dump(mode="json")


@app.get("/work-orders/{order_id}/assignments")
def list_work_order_assignments(order_id: str) -> dict:
    s = svc()
    if s.store.get_work_order(order_id) is None:
        raise HTTPException(status_code=404, detail=f"no such work order: {order_id}")
    return {
        "order_id": order_id,
        "assignments": [
            a.model_dump(mode="json")
            for a in s.store.list_work_order_assignments(order_id)
        ],
    }


def _decide(order_id: str, decision: str, request: DecideWorkOrderRequest) -> dict:
    s = svc()
    try:
        act, notifications = s.work_orders.decide(
            order_id,
            decision,
            request.decided_by,
            request.role,
            datetime.now(timezone.utc),
            reason=request.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkOrderRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    order = s.store.get_work_order(order_id)
    return {
        "decision": act.model_dump(mode="json"),
        "notifications": [n.model_dump(mode="json") for n in notifications],
        "order": s.work_orders.view(order) if order else None,
    }


@app.post("/work-orders/{order_id}/approve")
def approve_work_order(order_id: str, request: DecideWorkOrderRequest) -> dict:
    return _decide(order_id, "approved", request)


@app.post("/work-orders/{order_id}/reject")
def reject_work_order(order_id: str, request: DecideWorkOrderRequest) -> dict:
    return _decide(order_id, "rejected", request)


@app.get("/work-orders/{order_id}/decisions")
def list_work_order_decisions(order_id: str) -> dict:
    s = svc()
    if s.store.get_work_order(order_id) is None:
        raise HTTPException(status_code=404, detail=f"no such work order: {order_id}")
    return {
        "order_id": order_id,
        "decisions": [
            d.model_dump(mode="json")
            for d in s.store.list_work_order_decisions(order_id)
        ],
    }


@app.get("/incidents/{incident_id}/chain")
def verify_chain(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    return {"incident_id": incident_id, "chain_valid": s.chain.verify_chain(incident_id)}


# -- Round 6: field updates, topology declarations, revisions --------


class FieldUpdateRequest(BaseModel):
    kind: str
    reported_by: str
    role: str
    reported_at: datetime | None = None
    note: str | None = None
    device_id: str | None = None
    finding: str | None = None
    alarm: Alarm | None = None


@app.post("/incidents/{incident_id}/updates")
def post_field_update(incident_id: str, request: FieldUpdateRequest) -> dict:
    s = svc()
    try:
        update = s.updates.post_update(
            incident_id,
            request.kind,
            request.reported_by,
            request.role,
            request.reported_at or datetime.now(timezone.utc),
            note=request.note,
            device_id=request.device_id,
            finding=request.finding,
            alarm=request.alarm,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UpdateRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return update.model_dump(mode="json")


@app.get("/incidents/{incident_id}/updates")
def list_field_updates(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    return {
        "incident_id": incident_id,
        "updates": [u.model_dump(mode="json") for u in s.updates.list_updates(incident_id)],
    }


class DeclareTopologyRequest(BaseModel):
    version: str = "v2"
    declared_by: str
    role: str
    declared_at: datetime | None = None
    effective_at: datetime


@app.post("/topology/declare")
def declare_topology(request: DeclareTopologyRequest) -> dict:
    s = svc()
    try:
        declaration = s.topology_acts.declare(
            request.version,
            request.declared_by,
            request.role,
            request.declared_at or datetime.now(timezone.utc),
            request.effective_at,
        )
    except TopologyRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return declaration.model_dump(mode="json")


@app.get("/topology/declarations")
def list_topology_declarations() -> dict:
    return {
        "declarations": [
            d.model_dump(mode="json") for d in svc().topology_acts.declarations()
        ]
    }


class RecorrelateRequest(BaseModel):
    recorrelated_by: str
    role: str
    as_of: datetime | None = None


@app.post("/incidents/{incident_id}/recorrelate")
def recorrelate_incident(incident_id: str, request: RecorrelateRequest) -> dict:
    s = svc()
    try:
        revision = s.revisions.recorrelate(
            incident_id,
            request.recorrelated_by,
            request.role,
            request.as_of or datetime.now(timezone.utc),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RevisionRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return revision.model_dump(mode="json")


@app.get("/incidents/{incident_id}/revisions")
def list_incident_revisions(incident_id: str) -> dict:
    s = svc()
    try:
        chain = s.revisions.revision_chain(incident_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "incident_id": incident_id,
        "revisions": [r.model_dump(mode="json") for r in chain],
    }


@app.get("/revisions/{revision_id}")
def get_revision(revision_id: str) -> dict:
    revision = svc().store.get_incident_revision(revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"no such revision: {revision_id}")
    return revision.model_dump(mode="json")


@app.get("/topology/versions")
def topology_versions() -> dict:
    return {"versions": svc().store.list_topology_versions()}


@app.get("/topology/{version}")
def get_topology(version: str) -> dict:
    payload = svc().store.get_topology_payload(version)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"no such topology version: {version}")
    view = TopologyView(payload)
    return {
        "version": version,
        "substations": list(view.substations.values()),
        "feeders": list(view.feeders.values()),
        "devices": svc().store.list_devices(version),
        "critical_loads": view.critical_loads,
    }
