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
- ``{"action": "classify_cause" | "get_classification", ...}`` —
  the Round 3 root-cause classification pack.
- ``{"action": "seed_history" | "list_memory_records" |
  "get_memory_record" | "get_priors", ...}`` — the Round 3 memory
  port (local double) and prior retrieval.
- ``{"action": "seal_pack" | "get_pack" | "list_packs" |
  "verify_pack", ...}`` — the Round 3 evidence pack acts.
- ``{"action": "estimate_etr" | "get_etr", ...}`` — the Round 4
  ETR engine: compute an estimate at an explicit ``as_of``, or
  read the current estimate and its history.
- ``{"action": "close_incident" | "get_closure" |
  "etr_accuracy" | "get_roster", ...}`` — the Round 4 closure
  act, the ETR accuracy ledger, and the crew roster artefact.
- ``{"action": "draft_work_order" | "get_work_order" |
  "list_work_orders" | "revise_work_order" |
  "assign_work_order" | "approve_work_order" |
  "reject_work_order", ...}`` — the Round 5 work-order drafts
  (draft shape), their reads, and the recorded operator acts:
  crew assignment and the approval gate. No action here operates
  anything — approval drafts notifications, it never sends.

The Runtime session id (``context.session_id``) maps to one
triage session and is carried into the logs. All domain work
delegates to the same service graph the API and demo use.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from grid_triage.agent import (
    BandMismatchRefusal,
    CitationRefusal,
    EtrBasisRefusal,
    ModelNotConfigured,
    PriorAttributionRefusal,
)
from grid_triage.closure import ClosureRefusal
from grid_triage.models import Alarm
from grid_triage.packs import PackRefusal
from grid_triage.services import DuplicateAlarm
from grid_triage.severity import SeverityRefusal
from grid_triage.wiring import Services, build_services
from grid_triage.work_orders import WorkOrderRefusal

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
        except (
            CitationRefusal,
            BandMismatchRefusal,
            PriorAttributionRefusal,
            EtrBasisRefusal,
        ) as exc:
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
    if action == "classify_cause":
        incident_id = payload["incident_id"]
        try:
            classification = svc.cause.classify(incident_id, now)
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        return {
            "status": "classified",
            "incident_id": incident_id,
            "classification": classification.model_dump(mode="json"),
        }
    if action == "get_classification":
        incident_id = payload["incident_id"]
        if svc.store.get_incident(incident_id) is None:
            return {"status": "no_such_incident", "incident_id": incident_id}
        classification = svc.cause.current(incident_id)
        return {
            "status": "ok",
            "incident_id": incident_id,
            "classification": (
                classification.model_dump(mode="json") if classification else None
            ),
        }
    if action == "seed_history":
        records = svc.memory.load_history()
        return {
            "status": "ok",
            "loaded": [r.record_id for r in records],
            "count": len(records),
        }
    if action == "list_memory_records":
        return {
            "status": "ok",
            "records": [
                r.model_dump(mode="json") for r in svc.memory_port.list_records()
            ],
        }
    if action == "get_memory_record":
        record = svc.memory_port.get_record(payload["record_id"])
        if record is None:
            return {"status": "no_such_record", "record_id": payload["record_id"]}
        return {"status": "ok", "record": record.model_dump(mode="json")}
    if action == "get_priors":
        incident_id = payload["incident_id"]
        try:
            hits = svc.memory.retrieve_for_incident(incident_id, now)
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        return {
            "status": "ok",
            "incident_id": incident_id,
            "priors": [h.model_dump(mode="json") for h in hits],
        }
    if action == "seal_pack":
        incident_id = payload["incident_id"]
        sealed_at = now
        if payload.get("sealed_at"):
            sealed_at = datetime.fromisoformat(payload["sealed_at"])
        try:
            pack = svc.packs.seal(
                incident_id, payload.get("sealed_by", ""), sealed_at
            )
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        except PackRefusal as exc:
            return {
                "status": "refused",
                "incident_id": incident_id,
                "problems": exc.problems,
            }
        return {"status": "sealed", "pack": pack.model_dump(mode="json")}
    if action == "get_pack":
        pack = svc.store.get_evidence_pack(payload["pack_id"])
        if pack is None:
            return {"status": "no_such_pack", "pack_id": payload["pack_id"]}
        return {"status": "ok", "pack": pack.model_dump(mode="json")}
    if action == "list_packs":
        incident_id = payload["incident_id"]
        if svc.store.get_incident(incident_id) is None:
            return {"status": "no_such_incident", "incident_id": incident_id}
        return {
            "status": "ok",
            "incident_id": incident_id,
            "packs": [
                p.model_dump(mode="json") for p in svc.packs.history(incident_id)
            ],
        }
    if action == "verify_pack":
        try:
            problems = svc.packs.verify_pack(payload["pack_id"])
        except KeyError:
            return {"status": "no_such_pack", "pack_id": payload["pack_id"]}
        return {
            "status": "ok",
            "pack_id": payload["pack_id"],
            "valid": not problems,
            "problems": problems,
        }
    if action == "estimate_etr":
        incident_id = payload["incident_id"]
        as_of = now
        if payload.get("as_of"):
            as_of = datetime.fromisoformat(payload["as_of"])
        try:
            estimate = svc.etr.estimate(incident_id, as_of)
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        return {
            "status": "estimated",
            "incident_id": incident_id,
            "estimate": estimate.model_dump(mode="json"),
        }
    if action == "get_etr":
        incident_id = payload["incident_id"]
        if svc.store.get_incident(incident_id) is None:
            return {"status": "no_such_incident", "incident_id": incident_id}
        history = svc.etr.history(incident_id)
        return {
            "status": "ok",
            "incident_id": incident_id,
            "current": history[-1].model_dump(mode="json") if history else None,
            "history": [e.model_dump(mode="json") for e in history],
        }
    if action == "close_incident":
        incident_id = payload["incident_id"]
        restored_at = payload.get("restored_at")
        try:
            closure, outcome, record = svc.closure.close_incident(
                incident_id,
                payload.get("closed_by", ""),
                payload.get("role", ""),
                datetime.fromisoformat(restored_at) if restored_at else now,
                payload.get("resolution_summary", ""),
                now,
            )
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        except ClosureRefusal as exc:
            return {
                "status": "refused",
                "incident_id": incident_id,
                "problems": exc.problems,
            }
        return {
            "status": "closed",
            "incident_id": incident_id,
            "closure": closure.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
            "memory_record": record.model_dump(mode="json"),
        }
    if action == "get_closure":
        incident_id = payload["incident_id"]
        if svc.store.get_incident(incident_id) is None:
            return {"status": "no_such_incident", "incident_id": incident_id}
        closure = svc.closure.closure_for(incident_id)
        outcomes = svc.store.list_etr_outcomes(incident_id)
        return {
            "status": "ok",
            "incident_id": incident_id,
            "closure": closure.model_dump(mode="json") if closure else None,
            "outcome": outcomes[0].model_dump(mode="json") if outcomes else None,
        }
    if action == "etr_accuracy":
        return {"status": "ok", "ledger": svc.closure.accuracy_ledger()}
    if action == "get_roster":
        return {"status": "ok", "roster": svc.roster.payload}
    if action == "draft_work_order":
        incident_id = payload["incident_id"]
        as_of = now
        if payload.get("as_of"):
            as_of = datetime.fromisoformat(payload["as_of"])
        try:
            order = svc.work_orders.draft(
                incident_id, as_of, payload.get("drafted_by", "system")
            )
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        except WorkOrderRefusal as exc:
            return {
                "status": "refused",
                "incident_id": incident_id,
                "problems": exc.problems,
            }
        return {
            "status": "drafted",
            "incident_id": incident_id,
            "order": svc.work_orders.view(order),
        }
    if action == "get_work_order":
        incident_id = payload["incident_id"]
        if svc.store.get_incident(incident_id) is None:
            return {"status": "no_such_incident", "incident_id": incident_id}
        orders = svc.store.list_work_orders(incident_id)
        return {
            "status": "ok",
            "incident_id": incident_id,
            "current": (
                svc.work_orders.view(orders[-1]) if orders else None
            ),
            "orders": [svc.work_orders.view(o) for o in orders],
        }
    if action == "list_work_orders":
        return {
            "status": "ok",
            "orders": [
                svc.work_orders.view(o)
                for o in svc.work_orders.list_by_state(payload.get("state"))
            ],
        }
    if action == "revise_work_order":
        as_of = now
        if payload.get("as_of"):
            as_of = datetime.fromisoformat(payload["as_of"])
        try:
            order = svc.work_orders.revise(
                payload["order_id"],
                payload.get("revised_by", ""),
                as_of,
                operator_note=payload.get("operator_note"),
                extra_materials=payload.get("extra_materials"),
            )
        except KeyError:
            return {
                "status": "no_such_work_order",
                "order_id": payload.get("order_id"),
            }
        except WorkOrderRefusal as exc:
            return {
                "status": "refused",
                "order_id": payload.get("order_id"),
                "problems": exc.problems,
            }
        return {"status": "revised", "order": svc.work_orders.view(order)}
    if action == "assign_work_order":
        try:
            assignment = svc.work_orders.assign(
                payload["order_id"],
                payload.get("crew_id", ""),
                payload.get("assigned_by", ""),
                datetime.fromisoformat(payload["window_start"]),
                datetime.fromisoformat(payload["window_end"]),
                now,
            )
        except KeyError:
            return {
                "status": "no_such_work_order",
                "order_id": payload.get("order_id"),
            }
        except WorkOrderRefusal as exc:
            return {
                "status": "refused",
                "order_id": payload.get("order_id"),
                "problems": exc.problems,
            }
        return {
            "status": "assigned",
            "assignment": assignment.model_dump(mode="json"),
        }
    if action in ("approve_work_order", "reject_work_order"):
        decision = "approved" if action == "approve_work_order" else "rejected"
        try:
            act, notifications = svc.work_orders.decide(
                payload["order_id"],
                decision,
                payload.get("decided_by", ""),
                payload.get("role", ""),
                now,
                reason=payload.get("reason"),
            )
        except KeyError:
            return {
                "status": "no_such_work_order",
                "order_id": payload.get("order_id"),
            }
        except WorkOrderRefusal as exc:
            return {
                "status": "refused",
                "order_id": payload.get("order_id"),
                "problems": exc.problems,
            }
        return {
            "status": decision,
            "decision": act.model_dump(mode="json"),
            "notifications": [n.model_dump(mode="json") for n in notifications],
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
