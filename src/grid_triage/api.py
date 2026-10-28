"""The operations API: the control room's surface over the same
service graph the Runtime entrypoint uses.

This FastAPI app is the local/operations API (ingest, alarms,
incidents, topology versions, brief assembly, chain verification).
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
from grid_triage.agent import CitationRefusal
from grid_triage.models import Alarm
from grid_triage.services import DuplicateAlarm
from grid_triage.topology import TopologyView
from grid_triage.wiring import Services, build_services

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
    return body


@app.post("/incidents/{incident_id}/brief")
def assemble_brief(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    try:
        brief = s.assembler.assemble(incident_id, datetime.now(timezone.utc))
    except CitationRefusal as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    return brief.model_dump(mode="json")


@app.get("/incidents/{incident_id}/chain")
def verify_chain(incident_id: str) -> dict:
    s = svc()
    if s.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"no such incident: {incident_id}")
    return {"incident_id": incident_id, "chain_valid": s.chain.verify_chain(incident_id)}


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
