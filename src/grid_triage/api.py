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

Round 11 adds principals. A request may carry a caller as
`X-Principal-Id` / `X-Principal-Role` headers; the access pack
(`grid_triage.access`) then judges the call — act routes pass
the principal into the services, which record a denial before
refusing; read routes honour existence hiding (an incident a
role may not read answers byte-identically to a non-existent
one) and list routes filter. A request with no principal
headers is the trusted local path, as before. The pack itself
is readable at /access/pack; the denial log at /access/denials
(a guarded surface: auditor and duty_manager only).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from grid_triage import __version__
from grid_triage.access import (
    SURFACE_ALARMS,
    SURFACE_CONSOLE,
    SURFACE_DENIAL_LOG,
    SURFACE_ETR,
    SURFACE_FIELD_UPDATES,
    SURFACE_INCIDENT,
    SURFACE_INCIDENT_REVIEW,
    SURFACE_MEMORY,
    SURFACE_MODEL_LEDGER,
    SURFACE_OVERSIGHT,
    SURFACE_PACKS,
    SURFACE_QUARANTINE,
    SURFACE_RESTORATION,
    SURFACE_SEVERITY,
    SURFACE_STORM,
    SURFACE_TOPOLOGY,
    SURFACE_WORK_ORDERS,
    AccessDeniedError,
    Principal,
    access_pack_fingerprint,
)
from grid_triage.agent import (
    BandMismatchRefusal,
    CitationRefusal,
    EtrBasisRefusal,
    PriorAttributionRefusal,
)
from grid_triage.closure import ClosureRefusal
from grid_triage.etr import EtrRefusal
from grid_triage.handover import HandoverRefusal
from grid_triage.models import Alarm
from grid_triage.oversight import OversightRefusal
from grid_triage.packs import PackRefusal
from grid_triage.quarantine import QuarantineRefusal
from grid_triage.restoration import RestorationRefusal
from grid_triage.review import ReviewRefusal
from grid_triage.revisions import RevisionRefusal, TopologyRefusal, UpdateRefusal
from grid_triage.services import DuplicateAlarm
from grid_triage.severity import SeverityRefusal
from grid_triage.storm import StormRefusal
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


# -- Round 11: principals --------------------------------------------
# A request carries a principal as X-Principal-Id /
# X-Principal-Role headers. No headers: the trusted local path
# (None). A partial or malformed pair is authenticated — and
# refused, recorded — exactly as the pack judges it: 401.


def _principal(
    request: Request, *, surface: str, target: str = ""
) -> Principal | None:
    principal_id = request.headers.get("x-principal-id")
    role = request.headers.get("x-principal-role")
    if principal_id is None and role is None:
        return None
    try:
        return svc().access.authenticate(
            {"id": principal_id, "role": role},
            surface=surface,
            target=target,
        )
    except AccessDeniedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _forbidden(exc: AccessDeniedError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc))


def _guard_incident(
    request: Request,
    incident_id: str,
    surface: str,
    not_found_detail: str | None = None,
) -> Principal | None:
    """Existence hiding for incident-scoped reads: a carried
    principal whose role may not read incidents (or this
    surface) gets the route's own not-found response — the same
    status and body a non-existent incident earns on this
    route, byte for byte. Returns the principal for the
    caller's own use."""
    principal = _principal(request, surface=surface, target=incident_id)
    if principal is not None:
        try:
            svc().access.require_incident_read(
                principal, surface, incident_id=incident_id
            )
        except AccessDeniedError as exc:
            raise HTTPException(
                status_code=404,
                detail=not_found_detail
                or f"no such incident: {incident_id}",
            ) from exc
    return principal


def _guard_surface(
    request: Request,
    surface: str,
    not_found_detail: str | None = None,
    target: str = "",
) -> Principal | None:
    """A read of a surface (or of a single resource on it). With
    `not_found_detail`, an unreadable surface answers as the
    resource's own not-found — the resource is hidden, not the
    surface refused. Without it, the refusal is a plain 403."""
    principal = _principal(request, surface=surface, target=target)
    if principal is not None:
        try:
            svc().access.require_read(principal, surface, target=target)
        except AccessDeniedError as exc:
            if not_found_detail is not None:
                raise HTTPException(
                    status_code=404, detail=not_found_detail
                ) from exc
            raise _forbidden(exc) from exc
    return principal


def _list_visible(request: Request, surface: str) -> bool:
    """List surfaces filter, never refuse: a carried principal
    without the read sees an empty list; no principal (or a
    readable one) sees the list."""
    principal = _principal(request, surface=surface)
    if principal is None:
        return True
    return svc().access.pack.can_read(principal.role, surface)


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
def ingest_feed(request: Request) -> IngestResponse:
    s = svc()
    principal = _principal(request, surface=SURFACE_ALARMS)
    try:
        count = s.intake.ingest_feed(
            s.feed.fetch_alarms(), principal=principal
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except DuplicateAlarm as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    created = s.correlator.correlate_new(s.topology)
    return IngestResponse(
        ingested=count, incidents_created=[i.incident_id for i in created]
    )


@app.post("/alarms", response_model=IngestResponse)
def ingest_alarms(alarms: list[Alarm], request: Request) -> IngestResponse:
    s = svc()
    principal = _principal(request, surface=SURFACE_ALARMS)
    try:
        count = s.intake.ingest(alarms, principal=principal)
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except DuplicateAlarm as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    created = s.correlator.correlate_new(s.topology)
    return IngestResponse(
        ingested=count, incidents_created=[i.incident_id for i in created]
    )


@app.get("/alarms")
def list_alarms(request: Request, uncorrelated: bool = False) -> list[dict]:
    s = svc()
    if not _list_visible(request, SURFACE_ALARMS):
        return []
    alarms = s.store.list_uncorrelated_alarms() if uncorrelated else s.store.list_alarms()
    return [a.model_dump(mode="json") for a in alarms]


@app.get("/alarms/{alarm_id}")
def get_alarm(alarm_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_ALARMS,
        not_found_detail=f"no such alarm: {alarm_id}",
        target=alarm_id,
    )
    alarm = svc().store.get_alarm(alarm_id)
    if alarm is None:
        raise HTTPException(status_code=404, detail=f"no such alarm: {alarm_id}")
    body = alarm.model_dump(mode="json")
    membership = svc().store.membership_for_alarm(alarm_id)
    body["incident_id"] = membership.incident_id if membership else None
    body["correlation_rule"] = membership.rule_id if membership else None
    return body


@app.get("/incidents")
def list_incidents(request: Request) -> list[dict]:
    if not _list_visible(request, SURFACE_INCIDENT):
        return []
    return [i.model_dump(mode="json") for i in svc().store.list_incidents()]


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_INCIDENT)
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
def assemble_brief(incident_id: str, request: Request) -> dict:
    s = svc()
    principal = _principal(
        request, surface=SURFACE_INCIDENT, target=incident_id
    )
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    try:
        brief = s.assembler.assemble(
            incident_id, datetime.now(timezone.utc), principal=principal
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
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
def classify_incident(incident_id: str, request: Request) -> dict:
    s = svc()
    principal = _principal(
        request, surface=SURFACE_INCIDENT, target=incident_id
    )
    try:
        classification = s.cause.classify(
            incident_id, datetime.now(timezone.utc), principal=principal
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return classification.model_dump(mode="json")


@app.get("/incidents/{incident_id}/classification")
def get_classification(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_INCIDENT)
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
def get_priors(incident_id: str, request: Request) -> dict:
    _guard_incident(
        request,
        incident_id,
        SURFACE_MEMORY,
        not_found_detail=str(KeyError(f"no such incident: {incident_id}")),
    )
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
def seed_history(request: Request) -> dict:
    principal = _principal(request, surface=SURFACE_MEMORY)
    try:
        records = svc().memory.load_history(principal=principal)
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    return {"loaded": [r.record_id for r in records], "count": len(records)}


@app.get("/memory/records")
def list_memory_records(request: Request) -> list[dict]:
    if not _list_visible(request, SURFACE_MEMORY):
        return []
    return [r.model_dump(mode="json") for r in svc().memory_port.list_records()]


@app.get("/memory/records/{record_id}")
def get_memory_record(record_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_MEMORY,
        not_found_detail=f"no such memory record: {record_id}",
        target=record_id,
    )
    record = svc().memory_port.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no such memory record: {record_id}")
    return record.model_dump(mode="json")


class SealRequest(BaseModel):
    sealed_by: str


@app.post("/incidents/{incident_id}/pack/seal")
def seal_pack(incident_id: str, request: SealRequest, http_request: Request) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_PACKS, target=incident_id
    )
    try:
        pack = s.packs.seal(
            incident_id,
            request.sealed_by,
            datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PackRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return pack.model_dump(mode="json")


@app.get("/incidents/{incident_id}/pack")
def get_current_pack(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_PACKS)
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    pack = s.packs.current(incident_id)
    return {
        "incident_id": incident_id,
        "pack": pack.model_dump(mode="json") if pack else None,
    }


@app.get("/incidents/{incident_id}/packs")
def list_packs(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_PACKS)
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    return {
        "incident_id": incident_id,
        "packs": [p.model_dump(mode="json") for p in s.packs.history(incident_id)],
    }


@app.get("/packs/{pack_id}")
def get_pack(pack_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_PACKS,
        not_found_detail=f"no such evidence pack: {pack_id}",
        target=pack_id,
    )
    pack = svc().store.get_evidence_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"no such evidence pack: {pack_id}")
    return pack.model_dump(mode="json")


@app.get("/packs/{pack_id}/verify")
def verify_pack(pack_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_PACKS,
        not_found_detail=str(
            KeyError(f"no such evidence pack: {pack_id}")
        ),
        target=pack_id,
    )
    s = svc()
    try:
        problems = s.packs.verify_pack(pack_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"pack_id": pack_id, "valid": not problems, "problems": problems}


class AssessRequest(BaseModel):
    as_of: datetime | None = None


@app.get("/incidents/{incident_id}/severity")
def get_severity(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_SEVERITY)
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
def assess_severity(
    incident_id: str,
    http_request: Request,
    request: AssessRequest | None = None,
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_SEVERITY, target=incident_id
    )
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    as_of = (request.as_of if request else None) or datetime.now(timezone.utc)
    try:
        assessment = s.severity.assess(incident_id, as_of, principal=principal)
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except SeverityRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return assessment.model_dump(mode="json")


class AcknowledgeRequest(BaseModel):
    assessment_id: str
    acknowledged_by: str
    role: str


@app.get("/severity/acknowledgement-queue")
def acknowledgement_queue(request: Request) -> dict:
    _guard_surface(request, SURFACE_SEVERITY)
    s = svc()
    return {
        "pending": [
            a.model_dump(mode="json") for a in s.severity.pending_acknowledgements()
        ]
    }


@app.post("/severity/acknowledgements")
def acknowledge_severity(
    request: AcknowledgeRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request,
        surface=SURFACE_SEVERITY,
        target=request.assessment_id,
    )
    try:
        ack = s.severity.acknowledge(
            request.assessment_id,
            request.acknowledged_by,
            request.role,
            datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SeverityRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return ack.model_dump(mode="json")


# -- Round 4: ETR estimates, closure, the accuracy ledger -------------


class EstimateRequest(BaseModel):
    as_of: datetime | None = None


@app.get("/incidents/{incident_id}/etr")
def get_etr(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_ETR)
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
def estimate_etr(
    incident_id: str,
    http_request: Request,
    request: EstimateRequest | None = None,
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_ETR, target=incident_id
    )
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    as_of = (request.as_of if request else None) or datetime.now(timezone.utc)
    try:
        estimate = s.etr.estimate(incident_id, as_of, principal=principal)
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except EtrRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return estimate.model_dump(mode="json")


@app.get("/roster")
def get_roster(request: Request) -> dict:
    _guard_surface(request, SURFACE_TOPOLOGY)
    return svc().roster.payload


class CloseRequest(BaseModel):
    closed_by: str
    role: str
    restored_at: datetime
    resolution_summary: str


@app.post("/incidents/{incident_id}/close")
def close_incident(
    incident_id: str, request: CloseRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_INCIDENT, target=incident_id
    )
    try:
        closure, outcome, record = s.closure.close_incident(
            incident_id,
            request.closed_by,
            request.role,
            request.restored_at,
            request.resolution_summary,
            datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
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
def get_closure(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_INCIDENT)
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
def etr_accuracy(request: Request) -> dict:
    _guard_surface(request, SURFACE_ETR)
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
def draft_work_order(
    incident_id: str, request: DraftWorkOrderRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_WORK_ORDERS, target=incident_id
    )
    as_of = request.as_of or datetime.now(timezone.utc)
    try:
        order = s.work_orders.draft(
            incident_id, as_of, request.drafted_by, principal=principal
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkOrderRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return s.work_orders.view(order)


@app.get("/incidents/{incident_id}/work-orders")
def list_incident_work_orders(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_WORK_ORDERS)
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
def list_work_orders(request: Request, state: str | None = None) -> list[dict]:
    s = svc()
    if not _list_visible(request, SURFACE_WORK_ORDERS):
        return []
    return [s.work_orders.view(o) for o in s.work_orders.list_by_state(state)]


@app.get("/work-orders/{order_id}")
def get_work_order(order_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_WORK_ORDERS,
        not_found_detail=f"no such work order: {order_id}",
        target=order_id,
    )
    s = svc()
    order = s.store.get_work_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"no such work order: {order_id}")
    return s.work_orders.view(order)


@app.post("/work-orders/{order_id}/revise")
def revise_work_order(
    order_id: str, request: ReviseWorkOrderRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_WORK_ORDERS, target=order_id
    )
    as_of = request.as_of or datetime.now(timezone.utc)
    try:
        order = s.work_orders.revise(
            order_id,
            request.revised_by,
            as_of,
            operator_note=request.operator_note,
            extra_materials=request.extra_materials,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkOrderRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return s.work_orders.view(order)


@app.post("/work-orders/{order_id}/assign")
def assign_work_order(
    order_id: str, request: AssignWorkOrderRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_WORK_ORDERS, target=order_id
    )
    try:
        assignment = s.work_orders.assign(
            order_id,
            request.crew_id,
            request.assigned_by,
            request.window_start,
            request.window_end,
            datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkOrderRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return assignment.model_dump(mode="json")


@app.get("/work-orders/{order_id}/assignments")
def list_work_order_assignments(order_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_WORK_ORDERS,
        not_found_detail=f"no such work order: {order_id}",
        target=order_id,
    )
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


def _decide(
    order_id: str,
    decision: str,
    request: DecideWorkOrderRequest,
    http_request: Request,
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_WORK_ORDERS, target=order_id
    )
    try:
        act, notifications = s.work_orders.decide(
            order_id,
            decision,
            request.decided_by,
            request.role,
            datetime.now(timezone.utc),
            reason=request.reason,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
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
def approve_work_order(
    order_id: str, request: DecideWorkOrderRequest, http_request: Request
) -> dict:
    return _decide(order_id, "approved", request, http_request)


@app.post("/work-orders/{order_id}/reject")
def reject_work_order(
    order_id: str, request: DecideWorkOrderRequest, http_request: Request
) -> dict:
    return _decide(order_id, "rejected", request, http_request)


@app.get("/work-orders/{order_id}/decisions")
def list_work_order_decisions(order_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_WORK_ORDERS,
        not_found_detail=f"no such work order: {order_id}",
        target=order_id,
    )
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
def verify_chain(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_INCIDENT)
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
def post_field_update(
    incident_id: str, request: FieldUpdateRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_FIELD_UPDATES, target=incident_id
    )
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
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UpdateRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return update.model_dump(mode="json")


@app.get("/incidents/{incident_id}/updates")
def list_field_updates(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_FIELD_UPDATES)
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
def declare_topology(
    request: DeclareTopologyRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_TOPOLOGY, target=request.version
    )
    try:
        declaration = s.topology_acts.declare(
            request.version,
            request.declared_by,
            request.role,
            request.declared_at or datetime.now(timezone.utc),
            request.effective_at,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except TopologyRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return declaration.model_dump(mode="json")


@app.get("/topology/declarations")
def list_topology_declarations(request: Request) -> dict:
    if not _list_visible(request, SURFACE_TOPOLOGY):
        return {"declarations": []}
    return {
        "declarations": [
            d.model_dump(mode="json") for d in svc().topology_acts.declarations()
        ]
    }


@app.get("/topology/verify")
def verify_topology_declarations(request: Request) -> dict:
    # Round 17: re-perform every standing declaration. A
    # caller the surface hides from reads the standing exactly
    # as it reads the declarations themselves — as absent.
    if not _list_visible(request, SURFACE_TOPOLOGY):
        return {"problems": []}
    return {"problems": svc().topology_acts.verify_declarations()}


class ProposeTopologyRequest(BaseModel):
    version: str = "v2"
    proposed_by: str
    role: str
    proposed_at: datetime | None = None
    effective_at: datetime


@app.post("/topology/propose")
def propose_topology(
    request: ProposeTopologyRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_TOPOLOGY, target=request.version
    )
    try:
        proposal = s.topology_acts.propose(
            request.version,
            request.proposed_by,
            request.role,
            request.proposed_at or datetime.now(timezone.utc),
            request.effective_at,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except TopologyRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return {
        **proposal.model_dump(mode="json"),
        "state": s.topology_acts.proposal_state(proposal),
    }


@app.get("/topology/proposals")
def list_topology_proposals(request: Request) -> dict:
    if not _list_visible(request, SURFACE_TOPOLOGY):
        return {"proposals": []}
    s = svc()
    return {
        "proposals": [
            {
                **p.model_dump(mode="json"),
                "state": s.topology_acts.proposal_state(p),
            }
            for p in s.topology_acts.proposals()
        ]
    }


@app.get("/topology/proposals/{proposal_id}")
def get_topology_proposal(proposal_id: str, request: Request) -> dict:
    if not _list_visible(request, SURFACE_TOPOLOGY):
        raise HTTPException(
            status_code=404, detail=f"no such topology proposal: {proposal_id}"
        )
    s = svc()
    proposal = s.topology_acts.proposal_for(proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=404, detail=f"no such topology proposal: {proposal_id}"
        )
    return {
        **proposal.model_dump(mode="json"),
        "state": s.topology_acts.proposal_state(proposal),
        "decisions": [
            d.model_dump(mode="json")
            for d in s.topology_acts.decisions_for(proposal_id)
        ],
        "problems": s.topology_acts.verify_proposal(proposal_id),
    }


class DecideTopologyRequest(BaseModel):
    decided_by: str
    role: str
    decision: str
    reason: str | None = None
    decided_at: datetime | None = None


@app.post("/topology/proposals/{proposal_id}/decide")
def decide_topology_proposal(
    proposal_id: str, request: DecideTopologyRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_TOPOLOGY, target=proposal_id
    )
    try:
        record, declaration = s.topology_acts.decide(
            proposal_id,
            request.decided_by,
            request.role,
            request.decided_at or datetime.now(timezone.utc),
            request.decision,
            reason=request.reason,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TopologyRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return {
        "decision": record.model_dump(mode="json"),
        "declaration": (
            declaration.model_dump(mode="json") if declaration is not None else None
        ),
    }


class RecorrelateRequest(BaseModel):
    recorrelated_by: str
    role: str
    as_of: datetime | None = None


@app.post("/incidents/{incident_id}/recorrelate")
def recorrelate_incident(
    incident_id: str, request: RecorrelateRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_INCIDENT, target=incident_id
    )
    try:
        revision = s.revisions.recorrelate(
            incident_id,
            request.recorrelated_by,
            request.role,
            request.as_of or datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RevisionRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return revision.model_dump(mode="json")


@app.get("/incidents/{incident_id}/revisions")
def list_incident_revisions(incident_id: str, request: Request) -> dict:
    _guard_incident(
        request,
        incident_id,
        SURFACE_INCIDENT,
        not_found_detail=str(KeyError(f"no such incident: {incident_id}")),
    )
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
def get_revision(revision_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_INCIDENT,
        not_found_detail=f"no such revision: {revision_id}",
        target=revision_id,
    )
    revision = svc().store.get_incident_revision(revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"no such revision: {revision_id}")
    return revision.model_dump(mode="json")


# -- Round 7: restoration, the gated close, splits, event log ------


class RecordSignalRequest(BaseModel):
    source: str
    recorded_by: str
    role: str
    state: str
    observed_at: datetime | None = None
    device_id: str | None = None
    feeder_id: str | None = None


@app.post("/incidents/{incident_id}/restoration/signals")
def record_restoration_signal(
    incident_id: str, request: RecordSignalRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_RESTORATION, target=incident_id
    )
    try:
        signal = s.restoration.record_signal(
            incident_id,
            request.source,
            request.recorded_by,
            request.role,
            request.observed_at or datetime.now(timezone.utc),
            request.state,
            device_id=request.device_id,
            feeder_id=request.feeder_id,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RestorationRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return signal.model_dump(mode="json")


@app.get("/incidents/{incident_id}/restoration/signals")
def list_restoration_signals(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_RESTORATION)
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    return {
        "incident_id": incident_id,
        "signals": [
            sig.model_dump(mode="json")
            for sig in s.restoration.list_signals(incident_id)
        ],
    }


class ConfirmRestorationRequest(BaseModel):
    as_of: datetime | None = None


@app.post("/incidents/{incident_id}/restoration/confirm")
def confirm_restoration(
    incident_id: str, request: ConfirmRestorationRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_RESTORATION, target=incident_id
    )
    try:
        confirmation = s.restoration.confirm(
            incident_id,
            request.as_of or datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return confirmation.model_dump(mode="json")


@app.get("/incidents/{incident_id}/restoration/confirmation")
def get_restoration_confirmation(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_RESTORATION)
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    confirmation = s.restoration.confirmation(incident_id)
    return {
        "incident_id": incident_id,
        "confirmation": (
            confirmation.model_dump(mode="json") if confirmation else None
        ),
    }


class GatedCloseRequest(BaseModel):
    closed_by: str
    role: str
    restored_at: datetime
    resolution_summary: str
    waivers: dict[str, str] | None = None
    recorded_at: datetime | None = None


@app.post("/incidents/{incident_id}/close-gated")
def gated_close_incident(
    incident_id: str, request: GatedCloseRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_RESTORATION, target=incident_id
    )
    try:
        act, closure, outcome, record = s.restoration.close_incident(
            incident_id,
            request.closed_by,
            request.role,
            request.restored_at,
            request.resolution_summary,
            waivers=request.waivers,
            recorded_at=request.recorded_at,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RestorationRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return {
        "close_act": act.model_dump(mode="json"),
        "closure": closure.model_dump(mode="json"),
        "outcome": outcome.model_dump(mode="json"),
        "memory_record": record.model_dump(mode="json"),
    }


@app.get("/incidents/{incident_id}/close-act")
def get_close_act(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_RESTORATION)
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    act = s.restoration.close_act(incident_id)
    return {
        "incident_id": incident_id,
        "close_act": act.model_dump(mode="json") if act else None,
    }


class CompleteWorkOrderRequest(BaseModel):
    completed_by: str
    role: str
    completed_at: datetime | None = None
    note: str | None = None


@app.post("/work-orders/{order_id}/complete")
def complete_work_order(
    order_id: str, request: CompleteWorkOrderRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_WORK_ORDERS, target=order_id
    )
    try:
        completion = s.work_orders.complete(
            order_id,
            request.completed_by,
            request.role,
            request.completed_at or datetime.now(timezone.utc),
            note=request.note,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkOrderRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return completion.model_dump(mode="json")


class SplitIncidentRequest(BaseModel):
    split_by: str
    role: str
    as_of: datetime | None = None


@app.post("/incidents/{incident_id}/split")
def split_incident(
    incident_id: str, request: SplitIncidentRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_RESTORATION, target=incident_id
    )
    try:
        split = s.restoration.split_incident(
            incident_id,
            request.split_by,
            request.role,
            request.as_of or datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RestorationRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return split.model_dump(mode="json")


@app.get("/incidents/{incident_id}/splits")
def list_incident_splits(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_RESTORATION)
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    return {
        "incident_id": incident_id,
        "splits": [
            sp.model_dump(mode="json") for sp in s.restoration.splits_for(incident_id)
        ],
    }


@app.get("/incidents/{incident_id}/contradictions")
def list_incident_contradictions(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_RESTORATION)
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    return {
        "incident_id": incident_id,
        "contradictions": [
            c.model_dump(mode="json")
            for c in s.restoration.contradictions_for(incident_id)
        ],
    }


@app.get("/incidents/{incident_id}/event-log")
def get_incident_event_log(
    incident_id: str, request: Request, format: str = "json"
) -> dict:
    _guard_incident(
        request,
        incident_id,
        SURFACE_INCIDENT,
        not_found_detail=str(KeyError(f"no such incident: {incident_id}")),
    )
    s = svc()
    try:
        log = s.event_log.build_log(incident_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    problems = s.event_log.verify_log(incident_id, log)
    body = dict(log)
    body["verification_problems"] = problems
    if format == "text":
        body["text"] = s.event_log.render_text(log)
    return body


# -- Round 8: storm mode — declarations, the state fold, reviews ---


class StormDeclareRequest(BaseModel):
    declared_by: str
    role: str
    reason: str
    declared_at: datetime | None = None
    feeders: list[str] = []
    region: str | None = None


@app.post("/storm/declare")
def declare_storm(
    request: StormDeclareRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(http_request, surface=SURFACE_STORM)
    try:
        declaration = s.storm.declare(
            request.declared_by,
            request.role,
            request.reason,
            request.declared_at or datetime.now(timezone.utc),
            feeders=request.feeders,
            region=request.region,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except StormRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return declaration.model_dump(mode="json")


class StormExtendRequest(BaseModel):
    declaration_id: str
    declared_by: str
    role: str
    reason: str
    declared_at: datetime | None = None
    feeders: list[str] | None = None
    region: str | None = None


@app.post("/storm/extend")
def extend_storm(request: StormExtendRequest, http_request: Request) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_STORM, target=request.declaration_id
    )
    try:
        act = s.storm.extend(
            request.declaration_id,
            request.declared_by,
            request.role,
            request.reason,
            request.declared_at or datetime.now(timezone.utc),
            feeders=request.feeders,
            region=request.region,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except StormRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return act.model_dump(mode="json")


class StormStandDownRequest(BaseModel):
    declaration_id: str
    declared_by: str
    role: str
    reason: str
    declared_at: datetime | None = None


@app.post("/storm/stand-down")
def stand_down_storm(
    request: StormStandDownRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_STORM, target=request.declaration_id
    )
    try:
        act = s.storm.stand_down(
            request.declaration_id,
            request.declared_by,
            request.role,
            request.reason,
            request.declared_at or datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except StormRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return act.model_dump(mode="json")


@app.get("/storm/declarations")
def list_storm_declarations(request: Request) -> dict:
    if not _list_visible(request, SURFACE_STORM):
        return {"declarations": []}
    return {
        "declarations": [
            d.model_dump(mode="json") for d in svc().storm.declarations()
        ]
    }


@app.get("/storm/state")
def get_storm_state(
    feeder_id: str, request: Request, at: str | None = None
) -> dict:
    _guard_surface(request, SURFACE_STORM, target=feeder_id)
    moment = datetime.now(timezone.utc)
    if at:
        try:
            moment = datetime.fromisoformat(at)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"not an ISO timestamp: {at!r}"
            ) from exc
    return svc().storm.state_at(feeder_id, moment)


class StormReviewRequest(BaseModel):
    declaration_id: str
    computed_by: str
    computed_at: datetime | None = None


@app.post("/storm/reviews")
def compute_storm_review(
    request: StormReviewRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_STORM, target=request.declaration_id
    )
    try:
        review = s.storm.compute_review(
            request.declaration_id,
            request.computed_by,
            request.computed_at or datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except StormRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return review.model_dump(mode="json")


@app.get("/storm/declarations/{declaration_id}/review")
def get_storm_review_for_declaration(
    declaration_id: str, request: Request
) -> dict:
    _guard_surface(
        request,
        SURFACE_STORM,
        not_found_detail=f"no such storm declaration: {declaration_id}",
        target=declaration_id,
    )
    s = svc()
    if s.storm.get_declaration(declaration_id) is None:
        raise HTTPException(
            status_code=404, detail=f"no such storm declaration: {declaration_id}"
        )
    review = s.storm.review_for(declaration_id)
    return {
        "declaration_id": declaration_id,
        "review": review.model_dump(mode="json") if review else None,
        "reviews": [
            r.model_dump(mode="json") for r in s.storm.reviews(declaration_id)
        ],
    }


@app.get("/storm/reviews/{review_id}")
def get_storm_review(review_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_STORM,
        not_found_detail=f"no such storm review: {review_id}",
        target=review_id,
    )
    review = svc().storm.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"no such storm review: {review_id}")
    return review.model_dump(mode="json")


@app.get("/storm/reviews/{review_id}/verify")
def verify_storm_review(review_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_STORM,
        not_found_detail=str(KeyError(f"no such storm review: {review_id}")),
        target=review_id,
    )
    s = svc()
    try:
        problems = s.storm.verify_review(review_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"review_id": review_id, "problems": problems}


# -- Round 9: the operator console and the shift handover -------


@app.get("/console", response_class=HTMLResponse)
def console_page() -> str:
    """The single-file operator console. Static by construction:
    it renders only what /console/data returns, and it carries
    no mutation surface of any kind (Law 1)."""
    return (
        Path(__file__).resolve().parent / "static" / "console.html"
    ).read_text(encoding="utf-8")


@app.get("/console/data")
def console_data(request: Request) -> dict:
    """The board payload: the operations board's computation at
    this moment, plus the latest handover's view (its
    acceptance and its staleness, computed live)."""
    _guard_surface(request, SURFACE_CONSOLE)
    s = svc()
    body = s.board.board(datetime.now(timezone.utc))
    body["handover"] = s.handover.latest_view()
    return body


class AssembleHandoverRequest(BaseModel):
    handed_over_by: str
    role: str
    handed_over_at: datetime | None = None


@app.post("/handovers")
def assemble_handover(
    request: AssembleHandoverRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(http_request, surface=SURFACE_CONSOLE)
    try:
        handover = s.handover.assemble(
            request.handed_over_by,
            request.role,
            request.handed_over_at or datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except HandoverRefusal as exc:
        raise HTTPException(
            status_code=422, detail={"problems": exc.problems}
        ) from exc
    return s.handover.view(handover)


@app.get("/handovers")
def list_handovers(request: Request) -> dict:
    if not _list_visible(request, SURFACE_CONSOLE):
        return {"handovers": []}
    s = svc()
    return {
        "handovers": [s.handover.view(h) for h in s.handover.handovers()]
    }


@app.get("/handovers/{handover_id}")
def get_handover(handover_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_CONSOLE,
        not_found_detail=f"no such shift handover: {handover_id}",
        target=handover_id,
    )
    s = svc()
    handover = s.handover.get(handover_id)
    if handover is None:
        raise HTTPException(
            status_code=404, detail=f"no such shift handover: {handover_id}"
        )
    return s.handover.view(handover)


@app.get("/handovers/{handover_id}/verify")
def verify_handover(handover_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_CONSOLE,
        not_found_detail=str(
            KeyError(f"no such shift handover: {handover_id}")
        ),
        target=handover_id,
    )
    s = svc()
    try:
        problems = s.handover.verify(handover_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"handover_id": handover_id, "problems": problems}


class AcceptHandoverRequest(BaseModel):
    accepted_by: str
    role: str
    accepted_at: datetime | None = None


@app.post("/handovers/{handover_id}/accept")
def accept_handover(
    handover_id: str, request: AcceptHandoverRequest, http_request: Request
) -> dict:
    s = svc()
    principal = _principal(
        http_request, surface=SURFACE_CONSOLE, target=handover_id
    )
    try:
        acceptance = s.handover.accept(
            handover_id,
            request.accepted_by,
            request.role,
            request.accepted_at or datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HandoverRefusal as exc:
        raise HTTPException(
            status_code=422, detail={"problems": exc.problems}
        ) from exc
    return acceptance.model_dump(mode="json")


# -- Round 12: operations oversight reporting --------------------


class GenerateReportRequest(BaseModel):
    generated_by: str
    role: str
    window_start: datetime
    window_end: datetime
    generated_at: datetime | None = None


@app.post("/oversight/reports")
def generate_report(
    request: GenerateReportRequest, http_request: Request
) -> dict:
    """The generation act. A carried principal is judged by the
    access pack (the generate_report act, the named human, the
    role) before the service's own pack discipline runs."""
    s = svc()
    principal = _principal(http_request, surface=SURFACE_OVERSIGHT)
    try:
        report = s.oversight.generate(
            request.generated_by,
            request.role,
            request.window_start,
            request.window_end,
            request.generated_at or datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except OversightRefusal as exc:
        raise HTTPException(
            status_code=422, detail={"problems": exc.problems}
        ) from exc
    return s.oversight.view(report)


@app.get("/oversight/reports")
def list_reports(request: Request) -> dict:
    if not _list_visible(request, SURFACE_OVERSIGHT):
        return {"reports": []}
    s = svc()
    return {"reports": [s.oversight.view(r) for r in s.oversight.reports()]}


@app.get("/oversight/reports/{report_id}")
def get_report(report_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_OVERSIGHT,
        not_found_detail=f"no such oversight report: {report_id}",
        target=report_id,
    )
    s = svc()
    report = s.oversight.get(report_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"no such oversight report: {report_id}"
        )
    return s.oversight.view(report)


@app.get("/oversight/reports/{report_id}/verify")
def verify_report(report_id: str, request: Request) -> dict:
    _guard_surface(
        request,
        SURFACE_OVERSIGHT,
        not_found_detail=f"no such oversight report: {report_id}",
        target=report_id,
    )
    s = svc()
    try:
        problems = s.oversight.verify_report(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"report_id": report_id, "problems": problems}


@app.get("/incidents/{incident_id}/model-ledger")
def get_model_ledger(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_MODEL_LEDGER)
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(
            status_code=404, detail=f"no such incident: {incident_id}"
        )
    body = s.model_gateway.ledger_view(incident_id)
    body["incident_id"] = incident_id
    return body


# -- Round 15: the post-incident review ------------------------


class StrikeReviewRequest(BaseModel):
    struck_by: str
    role: str
    struck_at: datetime | None = None


@app.post("/incidents/{incident_id}/review")
def strike_review(
    incident_id: str, request: StrikeReviewRequest, http_request: Request
) -> dict:
    """The strike act. Existence is judged first, as on every
    incident-scoped surface: a carried principal who may not
    read incidents is answered exactly as a non-existent
    incident is. Then the service judges the act itself (the
    strike_incident_review act, the named human, the role)."""
    principal = _guard_incident(
        http_request,
        incident_id,
        SURFACE_INCIDENT_REVIEW,
        not_found_detail=f"no such incident: {incident_id}",
    )
    s = svc()
    try:
        review = s.reviews.strike(
            incident_id,
            request.struck_by,
            request.role,
            request.struck_at or datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewRefusal as exc:
        raise HTTPException(
            status_code=422, detail={"problems": exc.problems}
        ) from exc
    return s.reviews.view(review)


@app.get("/incidents/{incident_id}/review")
def get_incident_review_head(incident_id: str, request: Request) -> dict:
    _guard_incident(
        request,
        incident_id,
        SURFACE_INCIDENT_REVIEW,
        not_found_detail=f"no such incident: {incident_id}",
    )
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(
            status_code=404, detail=f"no such incident: {incident_id}"
        )
    review = s.reviews.head_for_incident(incident_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail=f"no incident review struck for incident: {incident_id}",
        )
    return s.reviews.view(review)


@app.get("/incidents/{incident_id}/reviews")
def list_incident_reviews(incident_id: str, request: Request) -> dict:
    _guard_incident(
        request,
        incident_id,
        SURFACE_INCIDENT_REVIEW,
        not_found_detail=f"no such incident: {incident_id}",
    )
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(
            status_code=404, detail=f"no such incident: {incident_id}"
        )
    return {
        "reviews": [
            s.reviews.view(r) for r in s.reviews.reviews(incident_id)
        ]
    }


def _guard_review(request: Request, review_id: str):
    """Existence hiding for a single review, keyed on the
    incident it reviews: a carried principal who may not read
    that incident is answered with the review's own not-found,
    byte for byte as a non-existent review earns. Returns the
    review (or None) so the caller answers its own 404."""
    s = svc()
    review = s.reviews.get(review_id)
    principal = _principal(
        request, surface=SURFACE_INCIDENT_REVIEW, target=review_id
    )
    if principal is not None and review is not None:
        try:
            s.access.require_incident_read(
                principal,
                SURFACE_INCIDENT_REVIEW,
                incident_id=review.incident_id,
            )
        except AccessDeniedError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"no such incident review: {review_id}",
            ) from exc
    return review


@app.get("/reviews/{review_id}")
def get_review(review_id: str, request: Request) -> dict:
    review = _guard_review(request, review_id)
    if review is None:
        raise HTTPException(
            status_code=404, detail=f"no such incident review: {review_id}"
        )
    return svc().reviews.view(review)


@app.get("/reviews/{review_id}/verify")
def verify_review(review_id: str, request: Request) -> dict:
    review = _guard_review(request, review_id)
    if review is None:
        raise HTTPException(
            status_code=404, detail=f"no such incident review: {review_id}"
        )
    s = svc()
    problems = s.reviews.verify_review(review_id)
    return {"review_id": review_id, "problems": problems}


# -- Round 14: the injection quarantine -----------------------


@app.get("/incidents/{incident_id}/quarantine")
def get_incident_quarantine(incident_id: str, request: Request) -> dict:
    _guard_incident(request, incident_id, SURFACE_QUARANTINE)
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(
            status_code=404, detail=f"no such incident: {incident_id}"
        )
    return s.quarantine.incident_view(incident_id)


@app.get("/quarantine/{hold_id}")
def get_quarantine_hold(hold_id: str, request: Request) -> dict:
    principal = _principal(
        request, surface=SURFACE_QUARANTINE, target=hold_id
    )
    s = svc()
    try:
        return s.quarantine.view(hold_id, principal=principal)
    except AccessDeniedError as exc:
        if exc.hidden:
            raise HTTPException(
                status_code=404, detail=f"no such quarantine hold: {hold_id}"
            ) from exc
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"no such quarantine hold: {hold_id}"
        ) from exc


class ReleaseQuarantineRequest(BaseModel):
    released_by: str
    role: str
    released_at: datetime | None = None


@app.post("/quarantine/{hold_id}/release")
def release_quarantine_hold(
    hold_id: str, request: ReleaseQuarantineRequest, http_request: Request
) -> dict:
    principal = _principal(
        http_request, surface=SURFACE_QUARANTINE, target=hold_id
    )
    s = svc()
    try:
        release = s.quarantine.release(
            hold_id,
            request.released_by,
            request.role,
            request.released_at or datetime.now(timezone.utc),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise _forbidden(exc) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"no such quarantine hold: {hold_id}"
        ) from exc
    except QuarantineRefusal as exc:
        raise HTTPException(
            status_code=422, detail={"problems": exc.problems}
        ) from exc
    return release.model_dump(mode="json")


@app.get("/topology/versions")
def topology_versions(request: Request) -> dict:
    _guard_surface(request, SURFACE_TOPOLOGY)
    return {"versions": svc().store.list_topology_versions()}


@app.get("/topology/{version}")
def get_topology(version: str, request: Request) -> dict:
    _guard_surface(request, SURFACE_TOPOLOGY, target=version)
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


# -- Round 11: the access pack and the denial log ----------------


@app.get("/access/pack")
def get_access_pack() -> dict:
    """The access pack as data, with its content fingerprint.
    Public by design: the pack is code, published in this repo —
    knowing the rules is not a way around them."""
    s = svc()
    return {
        "pack": s.access.pack.model_dump(mode="json"),
        "fingerprint": access_pack_fingerprint(s.access.pack),
    }


@app.get("/access/denials")
def list_access_denials(request: Request) -> dict:
    """The denial log: every recorded refusal, in append order.
    A guarded surface — a carried principal must hold the
    denial-log read (auditor, duty_manager); the principal-less
    local path reads it as before."""
    s = svc()
    _guard_surface(request, SURFACE_DENIAL_LOG)
    return {
        "denials": [d.model_dump(mode="json") for d in s.access.denials()]
    }
