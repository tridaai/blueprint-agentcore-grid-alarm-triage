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
- ``{"action": "post_field_update" | "get_field_updates" |
  "declare_topology" | "get_topology_versions" |
  "recorrelate_incident" | "get_incident_revisions", ...}`` —
  the Round 6 field updates (recorded against an incident,
  alarm observations correlated under its pinned topology),
  the topology declaration act, and re-correlation under a
  declared version. Declaring and re-correlating are recorded
  operator acts over data — neither operates anything.
- ``{"action": "record_restoration_signal" |
  "confirm_restoration" | "get_restoration_confirmation" |
  "close_incident_gated" | "complete_work_order" |
  "split_incident" | "get_incident_event_log" |
  "get_incident_contradictions", ...}`` — the Round 7 restoration
  signals and computed confirmations, the gated close (the R4
  ``close_incident`` act remains for the recorded facts; the
  gated action is the one that earns a close), work-order
  completion acts, incident splits and the event log export.
- ``{"action": "declare_storm" | "extend_storm" |
  "stand_down_storm" | "list_storm_declarations" |
  "get_storm_state" | "compute_storm_review" |
  "get_storm_review" | "verify_storm_review", ...}`` — the
  Round 8 storm mode acts: storm mode exists only as these
  named operator acts (declaring operates nothing — it states
  an operating mode, recorded with its reason and scope), the
  state fold read, and the post-storm review.
- ``{"action": "get_console_board" | "assemble_handover" |
  "get_handover" | "list_handovers" | "verify_handover" |
  "accept_handover", ...}`` — the Round 9 operations board
  read (the console's computation) and the shift handover
  acts: assembly freezes the room's state into an artefact
  from artefacts of record; acceptance is the incoming
  operator's act bound to its record hash, refused when the
  record has moved. Neither operates anything.

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
from grid_triage.handover import HandoverRefusal
from grid_triage.models import Alarm
from grid_triage.packs import PackRefusal
from grid_triage.restoration import RestorationRefusal
from grid_triage.revisions import RevisionRefusal, TopologyRefusal, UpdateRefusal
from grid_triage.services import DuplicateAlarm
from grid_triage.severity import SeverityRefusal
from grid_triage.storm import StormRefusal
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
    if action == "post_field_update":
        incident_id = payload["incident_id"]
        reported_at = now
        if payload.get("reported_at"):
            reported_at = datetime.fromisoformat(payload["reported_at"])
        alarm = None
        if payload.get("alarm"):
            alarm = Alarm.model_validate(payload["alarm"])
        try:
            update = svc.updates.post_update(
                incident_id,
                payload.get("kind", ""),
                payload.get("reported_by", ""),
                payload.get("role", ""),
                reported_at,
                note=payload.get("note"),
                device_id=payload.get("device_id"),
                finding=payload.get("finding"),
                alarm=alarm,
            )
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        except UpdateRefusal as exc:
            return {
                "status": "refused",
                "incident_id": incident_id,
                "problems": exc.problems,
            }
        return {"status": "recorded", "update": update.model_dump(mode="json")}
    if action == "get_field_updates":
        incident_id = payload["incident_id"]
        if svc.store.get_incident(incident_id) is None:
            return {"status": "no_such_incident", "incident_id": incident_id}
        return {
            "status": "ok",
            "incident_id": incident_id,
            "updates": [
                u.model_dump(mode="json")
                for u in svc.updates.list_updates(incident_id)
            ],
        }
    if action == "declare_topology":
        declared_at = now
        if payload.get("declared_at"):
            declared_at = datetime.fromisoformat(payload["declared_at"])
        try:
            declaration = svc.topology_acts.declare(
                payload.get("version", "v2"),
                payload.get("declared_by", ""),
                payload.get("role", ""),
                declared_at,
                datetime.fromisoformat(payload["effective_at"]),
            )
        except TopologyRefusal as exc:
            return {"status": "refused", "problems": exc.problems}
        return {"status": "declared", "declaration": declaration.model_dump(mode="json")}
    if action == "get_topology_versions":
        return {
            "status": "ok",
            "versions": svc.store.list_topology_versions(),
            "declarations": [
                d.model_dump(mode="json")
                for d in svc.topology_acts.declarations()
            ],
        }
    if action == "recorrelate_incident":
        incident_id = payload["incident_id"]
        as_of = now
        if payload.get("as_of"):
            as_of = datetime.fromisoformat(payload["as_of"])
        try:
            revision = svc.revisions.recorrelate(
                incident_id,
                payload.get("recorrelated_by", ""),
                payload.get("role", ""),
                as_of,
            )
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        except RevisionRefusal as exc:
            return {
                "status": "refused",
                "incident_id": incident_id,
                "problems": exc.problems,
            }
        return {"status": "recorrelated", "revision": revision.model_dump(mode="json")}
    if action == "get_incident_revisions":
        incident_id = payload["incident_id"]
        try:
            chain = svc.revisions.revision_chain(incident_id)
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        return {
            "status": "ok",
            "incident_id": incident_id,
            "revisions": [r.model_dump(mode="json") for r in chain],
        }
    if action == "record_restoration_signal":
        incident_id = payload["incident_id"]
        observed_at = now
        if payload.get("observed_at"):
            observed_at = datetime.fromisoformat(payload["observed_at"])
        try:
            signal = svc.restoration.record_signal(
                incident_id,
                payload.get("source", ""),
                payload.get("recorded_by", ""),
                payload.get("role", ""),
                observed_at,
                payload.get("state", ""),
                device_id=payload.get("device_id"),
                feeder_id=payload.get("feeder_id"),
            )
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        except RestorationRefusal as exc:
            return {
                "status": "refused",
                "incident_id": incident_id,
                "problems": exc.problems,
            }
        return {"status": "recorded", "signal": signal.model_dump(mode="json")}
    if action == "confirm_restoration":
        incident_id = payload["incident_id"]
        as_of = now
        if payload.get("as_of"):
            as_of = datetime.fromisoformat(payload["as_of"])
        try:
            confirmation = svc.restoration.confirm(incident_id, as_of)
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        return {
            "status": "confirmed",
            "confirmation": confirmation.model_dump(mode="json"),
        }
    if action == "get_restoration_confirmation":
        incident_id = payload["incident_id"]
        if svc.store.get_incident(incident_id) is None:
            return {"status": "no_such_incident", "incident_id": incident_id}
        confirmation = svc.restoration.confirmation(incident_id)
        return {
            "status": "ok",
            "incident_id": incident_id,
            "confirmation": (
                confirmation.model_dump(mode="json") if confirmation else None
            ),
            "signals": [
                sig.model_dump(mode="json")
                for sig in svc.restoration.list_signals(incident_id)
            ],
        }
    if action == "close_incident_gated":
        incident_id = payload["incident_id"]
        try:
            act, closure, outcome, record = svc.restoration.close_incident(
                incident_id,
                payload.get("closed_by", ""),
                payload.get("role", ""),
                datetime.fromisoformat(payload["restored_at"]),
                payload.get("resolution_summary", ""),
                waivers=payload.get("waivers"),
                recorded_at=(
                    datetime.fromisoformat(payload["recorded_at"])
                    if payload.get("recorded_at")
                    else None
                ),
            )
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        except RestorationRefusal as exc:
            return {
                "status": "refused",
                "incident_id": incident_id,
                "problems": exc.problems,
            }
        return {
            "status": "closed",
            "close_act": act.model_dump(mode="json"),
            "closure": closure.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
            "memory_record": record.model_dump(mode="json"),
        }
    if action == "complete_work_order":
        order_id = payload["order_id"]
        completed_at = now
        if payload.get("completed_at"):
            completed_at = datetime.fromisoformat(payload["completed_at"])
        try:
            completion = svc.work_orders.complete(
                order_id,
                payload.get("completed_by", ""),
                payload.get("role", ""),
                completed_at,
                note=payload.get("note"),
            )
        except KeyError:
            return {"status": "no_such_work_order", "order_id": order_id}
        except WorkOrderRefusal as exc:
            return {
                "status": "refused",
                "order_id": order_id,
                "problems": exc.problems,
            }
        return {
            "status": "completed",
            "completion": completion.model_dump(mode="json"),
        }
    if action == "split_incident":
        incident_id = payload["incident_id"]
        as_of = now
        if payload.get("as_of"):
            as_of = datetime.fromisoformat(payload["as_of"])
        try:
            split = svc.restoration.split_incident(
                incident_id,
                payload.get("split_by", ""),
                payload.get("role", ""),
                as_of,
            )
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        except RestorationRefusal as exc:
            return {
                "status": "refused",
                "incident_id": incident_id,
                "problems": exc.problems,
            }
        return {"status": "split", "split": split.model_dump(mode="json")}
    if action == "get_incident_event_log":
        incident_id = payload["incident_id"]
        try:
            log = svc.event_log.build_log(incident_id)
        except KeyError:
            return {"status": "no_such_incident", "incident_id": incident_id}
        body = dict(log)
        body["status"] = "ok"
        body["verification_problems"] = svc.event_log.verify_log(
            incident_id, log
        )
        if payload.get("format") == "text":
            body["text"] = svc.event_log.render_text(log)
        return body
    if action == "get_incident_contradictions":
        incident_id = payload["incident_id"]
        if svc.store.get_incident(incident_id) is None:
            return {"status": "no_such_incident", "incident_id": incident_id}
        return {
            "status": "ok",
            "incident_id": incident_id,
            "contradictions": [
                c.model_dump(mode="json")
                for c in svc.restoration.contradictions_for(incident_id)
            ],
        }
    if action == "declare_storm":
        declared_at = now
        if payload.get("declared_at"):
            declared_at = datetime.fromisoformat(payload["declared_at"])
        try:
            declaration = svc.storm.declare(
                payload.get("declared_by", ""),
                payload.get("role", ""),
                payload.get("reason", ""),
                declared_at,
                feeders=payload.get("feeders") or [],
                region=payload.get("region"),
            )
        except StormRefusal as exc:
            return {"status": "refused", "problems": exc.problems}
        return {"status": "declared", "declaration": declaration.model_dump(mode="json")}
    if action == "extend_storm":
        declared_at = now
        if payload.get("declared_at"):
            declared_at = datetime.fromisoformat(payload["declared_at"])
        try:
            act = svc.storm.extend(
                payload.get("declaration_id", ""),
                payload.get("declared_by", ""),
                payload.get("role", ""),
                payload.get("reason", ""),
                declared_at,
                feeders=payload.get("feeders"),
                region=payload.get("region"),
            )
        except StormRefusal as exc:
            return {
                "status": "refused",
                "declaration_id": payload.get("declaration_id"),
                "problems": exc.problems,
            }
        return {"status": "extended", "declaration": act.model_dump(mode="json")}
    if action == "stand_down_storm":
        declared_at = now
        if payload.get("declared_at"):
            declared_at = datetime.fromisoformat(payload["declared_at"])
        try:
            act = svc.storm.stand_down(
                payload.get("declaration_id", ""),
                payload.get("declared_by", ""),
                payload.get("role", ""),
                payload.get("reason", ""),
                declared_at,
            )
        except StormRefusal as exc:
            return {
                "status": "refused",
                "declaration_id": payload.get("declaration_id"),
                "problems": exc.problems,
            }
        return {"status": "stood_down", "declaration": act.model_dump(mode="json")}
    if action == "list_storm_declarations":
        return {
            "status": "ok",
            "declarations": [
                d.model_dump(mode="json") for d in svc.storm.declarations()
            ],
        }
    if action == "get_storm_state":
        at = now
        if payload.get("at"):
            at = datetime.fromisoformat(payload["at"])
        return {"status": "ok", **svc.storm.state_at(payload["feeder_id"], at)}
    if action == "compute_storm_review":
        computed_at = now
        if payload.get("computed_at"):
            computed_at = datetime.fromisoformat(payload["computed_at"])
        try:
            review = svc.storm.compute_review(
                payload.get("declaration_id", ""),
                payload.get("computed_by", ""),
                computed_at,
            )
        except StormRefusal as exc:
            return {
                "status": "refused",
                "declaration_id": payload.get("declaration_id"),
                "problems": exc.problems,
            }
        return {"status": "computed", "review": review.model_dump(mode="json")}
    if action == "get_storm_review":
        review = svc.storm.get_review(payload.get("review_id", ""))
        if review is None:
            return {
                "status": "no_such_review",
                "review_id": payload.get("review_id"),
            }
        return {"status": "ok", "review": review.model_dump(mode="json")}
    if action == "verify_storm_review":
        try:
            problems = svc.storm.verify_review(payload.get("review_id", ""))
        except KeyError:
            return {
                "status": "no_such_review",
                "review_id": payload.get("review_id"),
            }
        return {
            "status": "ok",
            "review_id": payload.get("review_id"),
            "problems": problems,
        }
    if action == "get_console_board":
        body = svc.board.board(now)
        body["handover"] = svc.handover.latest_view()
        return {"status": "ok", "board": body}
    if action == "assemble_handover":
        handed_over_at = now
        if payload.get("handed_over_at"):
            handed_over_at = datetime.fromisoformat(payload["handed_over_at"])
        try:
            handover = svc.handover.assemble(
                payload.get("handed_over_by", ""),
                payload.get("role", ""),
                handed_over_at,
            )
        except HandoverRefusal as exc:
            return {"status": "refused", "problems": exc.problems}
        return {
            "status": "assembled",
            "handover": svc.handover.view(handover),
        }
    if action == "get_handover":
        handover = svc.handover.get(payload.get("handover_id", ""))
        if handover is None:
            return {
                "status": "no_such_handover",
                "handover_id": payload.get("handover_id"),
            }
        return {"status": "ok", "handover": svc.handover.view(handover)}
    if action == "list_handovers":
        return {
            "status": "ok",
            "handovers": [
                svc.handover.view(h) for h in svc.handover.handovers()
            ],
        }
    if action == "verify_handover":
        try:
            problems = svc.handover.verify(payload.get("handover_id", ""))
        except KeyError:
            return {
                "status": "no_such_handover",
                "handover_id": payload.get("handover_id"),
            }
        return {
            "status": "ok",
            "handover_id": payload.get("handover_id"),
            "problems": problems,
        }
    if action == "accept_handover":
        accepted_at = now
        if payload.get("accepted_at"):
            accepted_at = datetime.fromisoformat(payload["accepted_at"])
        try:
            acceptance = svc.handover.accept(
                payload.get("handover_id", ""),
                payload.get("accepted_by", ""),
                payload.get("role", ""),
                accepted_at,
            )
        except KeyError:
            return {
                "status": "no_such_handover",
                "handover_id": payload.get("handover_id"),
            }
        except HandoverRefusal as exc:
            return {
                "status": "refused",
                "handover_id": payload.get("handover_id"),
                "problems": exc.problems,
            }
        return {
            "status": "accepted",
            "acceptance": acceptance.model_dump(mode="json"),
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
