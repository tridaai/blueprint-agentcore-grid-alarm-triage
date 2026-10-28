"""The AgentCore Runtime entrypoint.

The agent ships as an AgentCore Runtime app: the
``bedrock-agentcore`` SDK's ``BedrockAgentCoreApp`` serves the
Runtime HTTP contract — ``POST /invocations`` and ``GET /ping`` on
port 8080, in an ARM64 container (see the Dockerfile). One invoke
payload names one piece of triage work:

- ``{"action": "assemble_brief", "incident_id": "INC-..."}`` — run
  the agent step for a stored incident.
- ``{"action": "ingest_and_brief", "alarms": [...]}`` — ingest a
  batch of alarms, correlate, and brief every new incident in the
  same session.
- ``{"action": "assess_severity" | "get_severity", ...}`` — the
  Round 2 severity engine: compute an assessment at an explicit
  ``as_of``, or read the current assessment and its history.
- ``{"action": "acknowledge_severity", ...}`` /
  ``{"action": "acknowledgement_queue"}`` — the escalation
  acknowledgement act and the pending queue.

The Runtime session id (``context.session_id``) maps to one
triage session and is carried into the logs. All domain work
delegates to the same service graph the API and demo use.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from grid_triage.agent import BandMismatchRefusal, CitationRefusal, ModelNotConfigured
from grid_triage.models import Alarm
from grid_triage.services import DuplicateAlarm
from grid_triage.severity import SeverityRefusal
from grid_triage.wiring import Services, build_services

log = logging.getLogger("grid_triage.runtime")

app = BedrockAgentCoreApp()
_services: Services | None = None


def services() -> Services:
    global _services
    if _services is None:
        _services = build_services()
    return _services


def handle_invocation(payload: dict, session_id: str | None = None) -> dict:
    """The invocation logic, callable in-process (the local harness
    and the tests use it without HTTP) and by the Runtime entrypoint
    below."""
    svc = services()
    action = payload.get("action", "assemble_brief")
    now = datetime.now(timezone.utc)
    extra = {"session_id": session_id} if session_id else {}
    if action == "assemble_brief":
        incident_id = payload["incident_id"]
        try:
            brief = svc.assembler.assemble(incident_id, now)
        except (CitationRefusal, BandMismatchRefusal) as exc:
            log.warning("brief refused for %s: %s", incident_id, exc, extra=extra)
            return {"status": "refused", "incident_id": incident_id, "problems": exc.problems}
        except ModelNotConfigured as exc:
            return {"status": "model_not_configured", "detail": str(exc)}
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        return {
            "status": "assembled",
            "incident_id": incident_id,
            "brief": brief.model_dump(mode="json"),
        }
    if action == "assess_severity":
        incident_id = payload["incident_id"]
        as_of = now
        if payload.get("as_of"):
            as_of = datetime.fromisoformat(payload["as_of"])
        try:
            assessment = svc.severity.assess(incident_id, as_of)
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        return {
            "status": "assessed",
            "incident_id": incident_id,
            "assessment": assessment.model_dump(mode="json"),
        }
    if action == "get_severity":
        incident_id = payload["incident_id"]
        if svc.store.get_incident(incident_id) is None:
            return {"status": "no_such_incident", "incident_id": incident_id}
        history = svc.severity.history(incident_id)
        return {
            "status": "ok",
            "incident_id": incident_id,
            "current": history[-1].model_dump(mode="json") if history else None,
            "history": [a.model_dump(mode="json") for a in history],
        }
    if action == "acknowledge_severity":
        try:
            ack = svc.severity.acknowledge(
                payload["assessment_id"],
                payload.get("acknowledged_by", ""),
                payload.get("role", ""),
                now,
            )
        except KeyError:
            return {
                "status": "no_such_assessment",
                "assessment_id": payload.get("assessment_id"),
            }
        except SeverityRefusal as exc:
            return {
                "status": "refused",
                "assessment_id": payload.get("assessment_id"),
                "problems": exc.problems,
            }
        return {"status": "acknowledged", "acknowledgement": ack.model_dump(mode="json")}
    if action == "acknowledgement_queue":
        return {
            "status": "ok",
            "pending": [
                a.model_dump(mode="json") for a in svc.severity.pending_acknowledgements()
            ],
        }
    if action == "ingest_and_brief":
        alarms = [Alarm.model_validate(a) for a in payload.get("alarms", [])]
        try:
            svc.intake.ingest(alarms)
        except DuplicateAlarm as exc:
            return {"status": "duplicate_alarm", "detail": str(exc)}
        created = svc.correlator.correlate_new(svc.topology)
        briefs = []
        for incident in created:
            brief = svc.assembler.assemble(incident.incident_id, now)
            briefs.append(brief.model_dump(mode="json"))
        return {
            "status": "correlated",
            "incidents_created": [i.incident_id for i in created],
            "briefs": briefs,
        }
    return {"status": "unknown_action", "action": action}


@app.entrypoint
def invoke(payload: dict, context) -> dict:
    session_id = getattr(context, "session_id", None)
    return handle_invocation(payload or {}, session_id=session_id)
