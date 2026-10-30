"""Operator CLI: read-only views over the store, plus the recorded
human acts — the Round 2 acknowledgement, the Round 3 pack seal,
the Round 4 incident closure, the Round 5 work-order assignment
and approval/rejection acts. None of them is an operation on
the grid (Law 1 — the CLI has no verb that operates anything)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from grid_triage.access import access_pack_fingerprint
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
from grid_triage.severity import SeverityRefusal
from grid_triage.storm import StormRefusal
from grid_triage.wiring import build_services
from grid_triage.work_orders import WorkOrderRefusal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grid-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("incidents")
    sub.add_parser("uncorrelated")
    p_inc = sub.add_parser("incident")
    p_inc.add_argument("incident_id")
    p_sev = sub.add_parser("severity")
    p_sev.add_argument("incident_id")
    p_assess = sub.add_parser("assess")
    p_assess.add_argument("incident_id")
    p_assess.add_argument("--as-of", default=None, help="ISO timestamp; default: now")
    sub.add_parser("ack-queue")
    p_ack = sub.add_parser("acknowledge")
    p_ack.add_argument("assessment_id")
    p_ack.add_argument("--by", required=True, help="the acknowledging operator's name")
    p_ack.add_argument("--role", required=True, help="the acknowledging operator's role")
    p_classify = sub.add_parser("classify")
    p_classify.add_argument("incident_id")
    p_classification = sub.add_parser("classification")
    p_classification.add_argument("incident_id")
    p_priors = sub.add_parser("priors")
    p_priors.add_argument("incident_id")
    sub.add_parser("load-history")
    sub.add_parser("memory")
    p_record = sub.add_parser("memory-record")
    p_record.add_argument("record_id")
    p_seal = sub.add_parser("seal-pack")
    p_seal.add_argument("incident_id")
    p_seal.add_argument("--by", required=True, help="the operator sealing the pack")
    p_pack = sub.add_parser("pack")
    p_pack.add_argument("incident_id")
    p_packs = sub.add_parser("packs")
    p_packs.add_argument("incident_id")
    p_verify = sub.add_parser("verify-pack")
    p_verify.add_argument("pack_id")
    p_etr = sub.add_parser("etr")
    p_etr.add_argument("incident_id")
    p_estimate = sub.add_parser("estimate")
    p_estimate.add_argument("incident_id")
    p_estimate.add_argument("--as-of", default=None, help="ISO timestamp; default: now")
    p_close = sub.add_parser("close")
    p_close.add_argument("incident_id")
    p_close.add_argument("--by", required=True, help="the closing operator's name")
    p_close.add_argument("--role", required=True, help="the closing operator's role")
    p_close.add_argument("--restored-at", required=True, help="ISO timestamp supply was restored")
    p_close.add_argument("--summary", required=True, help="the resolution facts, as recorded")
    p_closure = sub.add_parser("closure")
    p_closure.add_argument("incident_id")
    sub.add_parser("etr-accuracy")
    sub.add_parser("roster")
    p_draft = sub.add_parser("work-order")
    p_draft.add_argument("incident_id")
    p_draft.add_argument("--by", default="system", help="the drafter (default: system)")
    p_draft.add_argument("--as-of", default=None, help="ISO timestamp; default: now")
    p_orders = sub.add_parser("work-orders")
    p_orders.add_argument("--incident", default=None, help="only this incident")
    p_orders.add_argument("--state", default=None, help="draft | approved | rejected")
    p_revise = sub.add_parser("revise-work-order")
    p_revise.add_argument("order_id")
    p_revise.add_argument("--by", required=True, help="the revising operator's name")
    p_revise.add_argument("--note", default=None, help="an operator note for the new version")
    p_assign = sub.add_parser("assign-work-order")
    p_assign.add_argument("order_id")
    p_assign.add_argument("--crew", required=True, help="the crew id from the roster")
    p_assign.add_argument("--by", required=True, help="the assigning operator's name")
    p_assign.add_argument("--from", dest="window_start", required=True, help="work window start, ISO")
    p_assign.add_argument("--to", dest="window_end", required=True, help="work window end, ISO")
    p_approve = sub.add_parser("approve-work-order")
    p_approve.add_argument("order_id")
    p_approve.add_argument("--by", required=True, help="the approving operator's name")
    p_approve.add_argument("--role", required=True, help="the approving operator's role")
    p_reject = sub.add_parser("reject-work-order")
    p_reject.add_argument("order_id")
    p_reject.add_argument("--by", required=True, help="the rejecting operator's name")
    p_reject.add_argument("--role", required=True, help="the rejecting operator's role")
    p_reject.add_argument("--reason", required=True, help="why the draft is rejected")
    sub.add_parser("topology-versions")
    p_decl = sub.add_parser("declare-topology")
    p_decl.add_argument("--version", default="v2", help="the topology version to declare")
    p_decl.add_argument("--by", required=True, help="the declaring operator's name")
    p_decl.add_argument("--role", required=True, help="the declaring operator's role")
    p_decl.add_argument("--effective-at", required=True, help="ISO timestamp the version takes effect")
    p_decl.add_argument("--declared-at", default=None, help="ISO timestamp; default: now")
    p_upd = sub.add_parser("field-update")
    p_upd.add_argument("incident_id")
    p_upd.add_argument("--kind", required=True, help="alarm_observation | crew_note | damage_finding")
    p_upd.add_argument("--by", required=True, help="the reporter's name")
    p_upd.add_argument("--role", required=True, help="the reporter's role")
    p_upd.add_argument("--note", default=None)
    p_upd.add_argument("--device", default=None)
    p_upd.add_argument("--finding", default=None)
    p_upd.add_argument("--alarm-json", default=None, help="the carried alarm, as a JSON object")
    p_upd.add_argument("--reported-at", default=None, help="ISO timestamp; default: now")
    p_updates = sub.add_parser("updates")
    p_updates.add_argument("incident_id")
    p_quar = sub.add_parser("quarantine")
    p_quar.add_argument("incident_id")
    p_qrel = sub.add_parser("quarantine-release")
    p_qrel.add_argument("hold_id")
    p_qrel.add_argument("--by", required=True, help="the releasing reviewer's name")
    p_qrel.add_argument("--role", required=True, help="the releasing reviewer's role")
    p_qrel.add_argument("--released-at", default=None, help="ISO timestamp; default: now")
    p_recorr = sub.add_parser("recorrelate")
    p_recorr.add_argument("incident_id")
    p_recorr.add_argument("--by", required=True, help="the re-correlating operator's name")
    p_recorr.add_argument("--role", required=True, help="the re-correlating operator's role")
    p_recorr.add_argument("--as-of", default=None, help="ISO timestamp; default: now")
    p_revisions = sub.add_parser("revisions")
    p_revisions.add_argument("incident_id")
    p_sig = sub.add_parser("restoration-signal")
    p_sig.add_argument("incident_id")
    p_sig.add_argument("--source", required=True, help="meter | oms | topology")
    p_sig.add_argument("--state", required=True, help="restored | still_out | supplied | not_supplied")
    p_sig.add_argument("--by", required=True, help="who records the signal")
    p_sig.add_argument("--role", required=True, help="the recorder's role")
    p_sig.add_argument("--device", default=None)
    p_sig.add_argument("--feeder", default=None)
    p_sig.add_argument("--observed-at", default=None, help="ISO timestamp; default: now")
    p_signals = sub.add_parser("restoration-signals")
    p_signals.add_argument("incident_id")
    p_confirm = sub.add_parser("confirm-restoration")
    p_confirm.add_argument("incident_id")
    p_confirm.add_argument("--as-of", default=None, help="ISO timestamp; default: now")
    p_conf = sub.add_parser("restoration-confirmation")
    p_conf.add_argument("incident_id")
    p_gclose = sub.add_parser("close-gated")
    p_gclose.add_argument("incident_id")
    p_gclose.add_argument("--by", required=True, help="the closing operator's name")
    p_gclose.add_argument("--role", required=True, help="the closing operator's role")
    p_gclose.add_argument("--restored-at", required=True, help="ISO timestamp supply was restored")
    p_gclose.add_argument("--summary", required=True, help="the resolution facts")
    p_gclose.add_argument("--waive", action="append", default=None, help="ORDER_ID=reason for a work order the closer waives (repeatable)")
    p_cact = sub.add_parser("close-act")
    p_cact.add_argument("incident_id")
    p_complete = sub.add_parser("complete-work-order")
    p_complete.add_argument("order_id")
    p_complete.add_argument("--by", required=True, help="who records the completion")
    p_complete.add_argument("--role", required=True, help="the recorder's role")
    p_complete.add_argument("--note", default=None)
    p_complete.add_argument("--completed-at", default=None, help="ISO timestamp; default: now")
    p_split = sub.add_parser("split-incident")
    p_split.add_argument("incident_id")
    p_split.add_argument("--by", required=True, help="the splitting operator's name")
    p_split.add_argument("--role", required=True, help="the splitting operator's role")
    p_split.add_argument("--as-of", default=None, help="ISO timestamp; default: now")
    p_splits = sub.add_parser("splits")
    p_splits.add_argument("incident_id")
    p_contra = sub.add_parser("contradictions")
    p_contra.add_argument("incident_id")
    p_log = sub.add_parser("event-log")
    p_log.add_argument("incident_id")
    p_log.add_argument("--format", default="json", choices=["json", "text"])
    p_sdec = sub.add_parser("storm-declare")
    p_sdec.add_argument("--by", required=True, help="the declaring operator's name")
    p_sdec.add_argument("--role", required=True, help="the declaring operator's role")
    p_sdec.add_argument("--reason", required=True, help="the stated reason for the declaration")
    p_sdec.add_argument("--feeder", action="append", default=None, help="a feeder in scope (repeatable; none = the whole territory)")
    p_sdec.add_argument("--region", default=None, help="a region label for the scope")
    p_sdec.add_argument("--declared-at", default=None, help="ISO timestamp; default: now")
    p_sext = sub.add_parser("storm-extend")
    p_sext.add_argument("declaration_id")
    p_sext.add_argument("--by", required=True, help="the extending operator's name")
    p_sext.add_argument("--role", required=True, help="the extending operator's role")
    p_sext.add_argument("--reason", required=True, help="the stated reason for the extension")
    p_sext.add_argument("--feeder", action="append", default=None, help="the scope's feeders, restated in full (repeatable)")
    p_sext.add_argument("--region", default=None)
    p_sext.add_argument("--declared-at", default=None, help="ISO timestamp; default: now")
    p_sdown = sub.add_parser("storm-stand-down")
    p_sdown.add_argument("declaration_id")
    p_sdown.add_argument("--by", required=True, help="the standing-down operator's name")
    p_sdown.add_argument("--role", required=True, help="the standing-down operator's role")
    p_sdown.add_argument("--reason", required=True, help="the stated reason for standing down")
    p_sdown.add_argument("--declared-at", default=None, help="ISO timestamp; default: now")
    p_slist = sub.add_parser("storm-declarations")
    p_sstate = sub.add_parser("storm-state")
    p_sstate.add_argument("feeder_id")
    p_sstate.add_argument("--at", default=None, help="ISO timestamp; default: now")
    p_srev = sub.add_parser("storm-review")
    p_srev.add_argument("declaration_id")
    p_srev.add_argument("--by", required=True, help="who computes the review")
    p_srev.add_argument("--at", default=None, help="ISO timestamp; default: now")
    p_srevv = sub.add_parser("storm-review-verify")
    p_srevv.add_argument("review_id")
    sub.add_parser("console-board")
    p_ho = sub.add_parser("handover")
    p_ho.add_argument("--by", required=True, help="the operator handing over")
    p_ho.add_argument("--role", required=True, help="the handing-over operator's role")
    p_ho.add_argument("--at", default=None, help="ISO timestamp; default: now")
    sub.add_parser("handovers")
    p_hov = sub.add_parser("handover-verify")
    p_hov.add_argument("handover_id")
    p_hoa = sub.add_parser("handover-accept")
    p_hoa.add_argument("handover_id")
    p_hoa.add_argument("--by", required=True, help="the incoming operator accepting")
    p_hoa.add_argument("--role", required=True, help="the accepting operator's role")
    p_hoa.add_argument("--at", default=None, help="ISO timestamp; default: now")
    # Round 12: operations oversight reporting.
    p_ov = sub.add_parser("oversight-report")
    p_ov.add_argument("--by", required=True, help="the operator generating the report")
    p_ov.add_argument("--role", required=True, help="the generating operator's role")
    p_ov.add_argument("--from", dest="window_from", required=True,
                      help="window start, ISO timestamp (inclusive)")
    p_ov.add_argument("--to", dest="window_to", required=True,
                      help="window end, ISO timestamp (exclusive)")
    p_ov.add_argument("--at", default=None, help="ISO timestamp; default: now")
    sub.add_parser("oversight-reports")
    p_ovv = sub.add_parser("oversight-verify")
    p_ovv.add_argument("report_id")
    # Round 15: the post-incident review.
    p_rev = sub.add_parser("incident-review")
    p_rev.add_argument("incident_id")
    p_rev.add_argument("--by", required=True, help="the operator striking the review")
    p_rev.add_argument("--role", required=True, help="the striking operator's role")
    p_rev.add_argument("--at", default=None, help="ISO timestamp; default: now")
    p_revl = sub.add_parser("incident-reviews")
    p_revl.add_argument("incident_id")
    p_revv = sub.add_parser("review-verify")
    p_revv.add_argument("review_id")
    # Round 11: the access layer's own reads. The CLI is the
    # trusted local path — it carries no principal; these two
    # views exist so the room can inspect the rules and the
    # refusal record locally.
    sub.add_parser("access-pack")
    sub.add_parser("access-denials")
    args = parser.parse_args(argv)

    svc = build_services()
    if args.command == "incidents":
        print(json.dumps([i.model_dump(mode="json") for i in svc.store.list_incidents()], indent=2))
    elif args.command == "uncorrelated":
        print(json.dumps([a.model_dump(mode="json") for a in svc.store.list_uncorrelated_alarms()], indent=2))
    elif args.command == "incident":
        incident = svc.store.get_incident(args.incident_id)
        if incident is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        body = incident.model_dump(mode="json")
        body["memberships"] = [m.model_dump(mode="json") for m in svc.store.list_memberships(args.incident_id)]
        print(json.dumps(body, indent=2))
    elif args.command == "severity":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        history = svc.severity.history(args.incident_id)
        print(json.dumps({
            "incident_id": args.incident_id,
            "current": history[-1].model_dump(mode="json") if history else None,
            "history": [a.model_dump(mode="json") for a in history],
        }, indent=2))
    elif args.command == "assess":
        as_of = (
            datetime.fromisoformat(args.as_of)
            if args.as_of
            else datetime.now(timezone.utc)
        )
        try:
            assessment = svc.severity.assess(args.incident_id, as_of)
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except SeverityRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(assessment.model_dump(mode="json"), indent=2))
    elif args.command == "ack-queue":
        print(json.dumps(
            [a.model_dump(mode="json") for a in svc.severity.pending_acknowledgements()],
            indent=2,
        ))
    elif args.command == "acknowledge":
        try:
            ack = svc.severity.acknowledge(
                args.assessment_id,
                args.by,
                args.role,
                datetime.now(timezone.utc),
            )
        except KeyError:
            print(f"no such assessment: {args.assessment_id}")
            return 1
        except SeverityRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(ack.model_dump(mode="json"), indent=2))
    elif args.command == "classify":
        try:
            classification = svc.cause.classify(
                args.incident_id, datetime.now(timezone.utc)
            )
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps(classification.model_dump(mode="json"), indent=2))
    elif args.command == "classification":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        classification = svc.cause.current(args.incident_id)
        print(json.dumps(
            classification.model_dump(mode="json") if classification else None,
            indent=2,
        ))
    elif args.command == "priors":
        try:
            hits = svc.memory.retrieve_for_incident(
                args.incident_id, datetime.now(timezone.utc)
            )
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps([h.model_dump(mode="json") for h in hits], indent=2))
    elif args.command == "load-history":
        records = svc.memory.load_history()
        print(json.dumps({"loaded": [r.record_id for r in records]}, indent=2))
    elif args.command == "memory":
        print(json.dumps(
            [r.model_dump(mode="json") for r in svc.memory_port.list_records()],
            indent=2,
        ))
    elif args.command == "memory-record":
        record = svc.memory_port.get_record(args.record_id)
        if record is None:
            print(f"no such memory record: {args.record_id}")
            return 1
        print(json.dumps(record.model_dump(mode="json"), indent=2))
    elif args.command == "seal-pack":
        try:
            pack = svc.packs.seal(
                args.incident_id, args.by, datetime.now(timezone.utc)
            )
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except PackRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(pack.model_dump(mode="json"), indent=2))
    elif args.command == "pack":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        pack = svc.packs.current(args.incident_id)
        print(json.dumps(
            pack.model_dump(mode="json") if pack else None, indent=2
        ))
    elif args.command == "packs":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps(
            [p.model_dump(mode="json") for p in svc.packs.history(args.incident_id)],
            indent=2,
        ))
    elif args.command == "verify-pack":
        try:
            problems = svc.packs.verify_pack(args.pack_id)
        except KeyError:
            print(f"no such evidence pack: {args.pack_id}")
            return 1
        print(json.dumps(
            {"pack_id": args.pack_id, "valid": not problems, "problems": problems},
            indent=2,
        ))
        return 0 if not problems else 1
    elif args.command == "etr":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        history = svc.etr.history(args.incident_id)
        print(json.dumps({
            "incident_id": args.incident_id,
            "current": history[-1].model_dump(mode="json") if history else None,
            "history": [e.model_dump(mode="json") for e in history],
        }, indent=2))
    elif args.command == "estimate":
        as_of = (
            datetime.fromisoformat(args.as_of)
            if args.as_of
            else datetime.now(timezone.utc)
        )
        try:
            estimate = svc.etr.estimate(args.incident_id, as_of)
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except EtrRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(estimate.model_dump(mode="json"), indent=2))
    elif args.command == "close":
        try:
            closure, outcome, record = svc.closure.close_incident(
                args.incident_id,
                args.by,
                args.role,
                datetime.fromisoformat(args.restored_at),
                args.summary,
                datetime.now(timezone.utc),
            )
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except ClosureRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps({
            "closure": closure.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
            "memory_record": record.model_dump(mode="json"),
        }, indent=2))
    elif args.command == "closure":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        closure = svc.closure.closure_for(args.incident_id)
        outcomes = svc.store.list_etr_outcomes(args.incident_id)
        print(json.dumps({
            "incident_id": args.incident_id,
            "closure": closure.model_dump(mode="json") if closure else None,
            "outcome": outcomes[0].model_dump(mode="json") if outcomes else None,
        }, indent=2))
    elif args.command == "etr-accuracy":
        print(json.dumps(svc.closure.accuracy_ledger(), indent=2))
    elif args.command == "roster":
        print(json.dumps(svc.roster.payload, indent=2))
    elif args.command == "work-order":
        as_of = (
            datetime.fromisoformat(args.as_of)
            if args.as_of
            else datetime.now(timezone.utc)
        )
        try:
            order = svc.work_orders.draft(args.incident_id, as_of, args.by)
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except WorkOrderRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(svc.work_orders.view(order), indent=2))
    elif args.command == "work-orders":
        if args.incident is not None:
            if svc.store.get_incident(args.incident) is None:
                print(f"no such incident: {args.incident}")
                return 1
            orders = svc.store.list_work_orders(args.incident)
            if args.state is not None:
                orders = [
                    o for o in orders if svc.work_orders.state_of(o) == args.state
                ]
        else:
            orders = svc.work_orders.list_by_state(args.state)
        print(json.dumps(
            [svc.work_orders.view(o) for o in orders], indent=2
        ))
    elif args.command == "revise-work-order":
        try:
            order = svc.work_orders.revise(
                args.order_id,
                args.by,
                datetime.now(timezone.utc),
                operator_note=args.note,
            )
        except KeyError:
            print(f"no such work order: {args.order_id}")
            return 1
        except WorkOrderRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(svc.work_orders.view(order), indent=2))
    elif args.command == "assign-work-order":
        try:
            assignment = svc.work_orders.assign(
                args.order_id,
                args.crew,
                args.by,
                datetime.fromisoformat(args.window_start),
                datetime.fromisoformat(args.window_end),
                datetime.now(timezone.utc),
            )
        except KeyError:
            print(f"no such work order: {args.order_id}")
            return 1
        except WorkOrderRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(assignment.model_dump(mode="json"), indent=2))
    elif args.command in ("approve-work-order", "reject-work-order"):
        decision = (
            "approved" if args.command == "approve-work-order" else "rejected"
        )
        try:
            act, notifications = svc.work_orders.decide(
                args.order_id,
                decision,
                args.by,
                args.role,
                datetime.now(timezone.utc),
                reason=getattr(args, "reason", None),
            )
        except KeyError:
            print(f"no such work order: {args.order_id}")
            return 1
        except WorkOrderRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps({
            "decision": act.model_dump(mode="json"),
            "notifications": [n.model_dump(mode="json") for n in notifications],
        }, indent=2))
    elif args.command == "topology-versions":
        print(json.dumps({
            "versions": svc.store.list_topology_versions(),
            "declarations": [
                d.model_dump(mode="json") for d in svc.topology_acts.declarations()
            ],
        }, indent=2))
    elif args.command == "declare-topology":
        declared_at = (
            datetime.fromisoformat(args.declared_at)
            if args.declared_at
            else datetime.now(timezone.utc)
        )
        try:
            declaration = svc.topology_acts.declare(
                args.version,
                args.by,
                args.role,
                declared_at,
                datetime.fromisoformat(args.effective_at),
            )
        except TopologyRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(declaration.model_dump(mode="json"), indent=2))
    elif args.command == "field-update":
        reported_at = (
            datetime.fromisoformat(args.reported_at)
            if args.reported_at
            else datetime.now(timezone.utc)
        )
        alarm = (
            Alarm.model_validate(json.loads(args.alarm_json))
            if args.alarm_json
            else None
        )
        try:
            update = svc.updates.post_update(
                args.incident_id,
                args.kind,
                args.by,
                args.role,
                reported_at,
                note=args.note,
                device_id=args.device,
                finding=args.finding,
                alarm=alarm,
            )
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except UpdateRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(update.model_dump(mode="json"), indent=2))
    elif args.command == "updates":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps(
            [u.model_dump(mode="json") for u in svc.updates.list_updates(args.incident_id)],
            indent=2,
        ))
    elif args.command == "quarantine":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps(svc.quarantine.incident_view(args.incident_id), indent=2))
    elif args.command == "quarantine-release":
        released_at = (
            datetime.fromisoformat(args.released_at)
            if args.released_at
            else datetime.now(timezone.utc)
        )
        try:
            release = svc.quarantine.release(
                args.hold_id, args.by, args.role, released_at
            )
        except KeyError:
            print(f"no such quarantine hold: {args.hold_id}")
            return 1
        except QuarantineRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(release.model_dump(mode="json"), indent=2))
    elif args.command == "recorrelate":
        as_of = (
            datetime.fromisoformat(args.as_of)
            if args.as_of
            else datetime.now(timezone.utc)
        )
        try:
            revision = svc.revisions.recorrelate(
                args.incident_id, args.by, args.role, as_of
            )
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except RevisionRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(revision.model_dump(mode="json"), indent=2))
    elif args.command == "revisions":
        try:
            chain = svc.revisions.revision_chain(args.incident_id)
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps(
            [r.model_dump(mode="json") for r in chain], indent=2
        ))
    elif args.command == "restoration-signal":
        observed_at = (
            datetime.fromisoformat(args.observed_at)
            if args.observed_at
            else datetime.now(timezone.utc)
        )
        try:
            signal = svc.restoration.record_signal(
                args.incident_id, args.source, args.by, args.role,
                observed_at, args.state,
                device_id=args.device, feeder_id=args.feeder,
            )
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except RestorationRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(signal.model_dump(mode="json"), indent=2))
    elif args.command == "restoration-signals":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps(
            [s.model_dump(mode="json") for s in svc.restoration.list_signals(args.incident_id)],
            indent=2,
        ))
    elif args.command == "confirm-restoration":
        as_of = (
            datetime.fromisoformat(args.as_of)
            if args.as_of
            else datetime.now(timezone.utc)
        )
        try:
            confirmation = svc.restoration.confirm(args.incident_id, as_of)
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps(confirmation.model_dump(mode="json"), indent=2))
    elif args.command == "restoration-confirmation":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        confirmation = svc.restoration.confirmation(args.incident_id)
        print(json.dumps(
            confirmation.model_dump(mode="json") if confirmation else None,
            indent=2,
        ))
    elif args.command == "close-gated":
        waivers: dict[str, str] = {}
        for entry in args.waive or []:
            order_id, _, reason = entry.partition("=")
            waivers[order_id.strip()] = reason.strip()
        try:
            act, closure, outcome, record = svc.restoration.close_incident(
                args.incident_id,
                args.by,
                args.role,
                datetime.fromisoformat(args.restored_at),
                args.summary,
                waivers=waivers,
                recorded_at=datetime.now(timezone.utc),
            )
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except RestorationRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps({
            "close_act": act.model_dump(mode="json"),
            "closure": closure.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
            "memory_record": record.model_dump(mode="json"),
        }, indent=2))
    elif args.command == "close-act":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        act = svc.restoration.close_act(args.incident_id)
        print(json.dumps(
            act.model_dump(mode="json") if act else None, indent=2
        ))
    elif args.command == "complete-work-order":
        completed_at = (
            datetime.fromisoformat(args.completed_at)
            if args.completed_at
            else datetime.now(timezone.utc)
        )
        try:
            completion = svc.work_orders.complete(
                args.order_id, args.by, args.role, completed_at,
                note=args.note,
            )
        except KeyError:
            print(f"no such work order: {args.order_id}")
            return 1
        except WorkOrderRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(completion.model_dump(mode="json"), indent=2))
    elif args.command == "split-incident":
        as_of = (
            datetime.fromisoformat(args.as_of)
            if args.as_of
            else datetime.now(timezone.utc)
        )
        try:
            split = svc.restoration.split_incident(
                args.incident_id, args.by, args.role, as_of
            )
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except RestorationRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(split.model_dump(mode="json"), indent=2))
    elif args.command == "splits":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps(
            [s.model_dump(mode="json") for s in svc.restoration.splits_for(args.incident_id)],
            indent=2,
        ))
    elif args.command == "contradictions":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps(
            [c.model_dump(mode="json") for c in svc.restoration.contradictions_for(args.incident_id)],
            indent=2,
        ))
    elif args.command == "event-log":
        try:
            log = svc.event_log.build_log(args.incident_id)
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        problems = svc.event_log.verify_log(args.incident_id, log)
        if args.format == "text":
            print(svc.event_log.render_text(log), end="")
            if problems:
                print("verification problems: " + "; ".join(problems))
                return 1
        else:
            body = dict(log)
            body["verification_problems"] = problems
            print(json.dumps(body, indent=2))
            if problems:
                return 1
    elif args.command == "storm-declare":
        declared_at = (
            datetime.fromisoformat(args.declared_at)
            if args.declared_at
            else datetime.now(timezone.utc)
        )
        try:
            declaration = svc.storm.declare(
                args.by,
                args.role,
                args.reason,
                declared_at,
                feeders=args.feeder or [],
                region=args.region,
            )
        except StormRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(declaration.model_dump(mode="json"), indent=2))
    elif args.command == "storm-extend":
        declared_at = (
            datetime.fromisoformat(args.declared_at)
            if args.declared_at
            else datetime.now(timezone.utc)
        )
        try:
            act = svc.storm.extend(
                args.declaration_id,
                args.by,
                args.role,
                args.reason,
                declared_at,
                feeders=args.feeder,
                region=args.region,
            )
        except StormRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(act.model_dump(mode="json"), indent=2))
    elif args.command == "storm-stand-down":
        declared_at = (
            datetime.fromisoformat(args.declared_at)
            if args.declared_at
            else datetime.now(timezone.utc)
        )
        try:
            act = svc.storm.stand_down(
                args.declaration_id,
                args.by,
                args.role,
                args.reason,
                declared_at,
            )
        except StormRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(act.model_dump(mode="json"), indent=2))
    elif args.command == "storm-declarations":
        print(json.dumps(
            [d.model_dump(mode="json") for d in svc.storm.declarations()],
            indent=2,
        ))
    elif args.command == "storm-state":
        at = (
            datetime.fromisoformat(args.at)
            if args.at
            else datetime.now(timezone.utc)
        )
        print(json.dumps(svc.storm.state_at(args.feeder_id, at), indent=2))
    elif args.command == "storm-review":
        computed_at = (
            datetime.fromisoformat(args.at)
            if args.at
            else datetime.now(timezone.utc)
        )
        try:
            review = svc.storm.compute_review(
                args.declaration_id, args.by, computed_at
            )
        except StormRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(review.model_dump(mode="json"), indent=2))
    elif args.command == "storm-review-verify":
        try:
            problems = svc.storm.verify_review(args.review_id)
        except KeyError:
            print(f"no such storm review: {args.review_id}")
            return 1
        print(json.dumps(
            {"review_id": args.review_id, "problems": problems}, indent=2
        ))
        if problems:
            return 1
    elif args.command == "console-board":
        body = svc.board.board(datetime.now(timezone.utc))
        body["handover"] = svc.handover.latest_view()
        print(json.dumps(body, indent=2))
    elif args.command == "handover":
        handed_over_at = (
            datetime.fromisoformat(args.at)
            if args.at
            else datetime.now(timezone.utc)
        )
        try:
            handover = svc.handover.assemble(args.by, args.role, handed_over_at)
        except HandoverRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(svc.handover.view(handover), indent=2))
    elif args.command == "handovers":
        print(json.dumps(
            [svc.handover.view(h) for h in svc.handover.handovers()],
            indent=2,
        ))
    elif args.command == "handover-verify":
        try:
            problems = svc.handover.verify(args.handover_id)
        except KeyError:
            print(f"no such shift handover: {args.handover_id}")
            return 1
        print(json.dumps(
            {"handover_id": args.handover_id, "problems": problems}, indent=2
        ))
        if problems:
            return 1
    elif args.command == "handover-accept":
        accepted_at = (
            datetime.fromisoformat(args.at)
            if args.at
            else datetime.now(timezone.utc)
        )
        try:
            acceptance = svc.handover.accept(
                args.handover_id, args.by, args.role, accepted_at
            )
        except KeyError:
            print(f"no such shift handover: {args.handover_id}")
            return 1
        except HandoverRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(acceptance.model_dump(mode="json"), indent=2))
    elif args.command == "oversight-report":
        generated_at = (
            datetime.fromisoformat(args.at)
            if args.at
            else datetime.now(timezone.utc)
        )
        try:
            report = svc.oversight.generate(
                args.by,
                args.role,
                datetime.fromisoformat(args.window_from),
                datetime.fromisoformat(args.window_to),
                generated_at,
            )
        except OversightRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(svc.oversight.view(report), indent=2))
    elif args.command == "oversight-reports":
        print(json.dumps(
            [svc.oversight.view(r) for r in svc.oversight.reports()],
            indent=2,
        ))
    elif args.command == "oversight-verify":
        try:
            problems = svc.oversight.verify_report(args.report_id)
        except KeyError:
            print(f"no such oversight report: {args.report_id}")
            return 1
        print(json.dumps(
            {"report_id": args.report_id, "problems": problems}, indent=2
        ))
        if problems:
            return 1
    elif args.command == "incident-review":
        struck_at = (
            datetime.fromisoformat(args.at)
            if args.at
            else datetime.now(timezone.utc)
        )
        try:
            review = svc.reviews.strike(
                args.incident_id, args.by, args.role, struck_at
            )
        except KeyError:
            print(f"no such incident: {args.incident_id}")
            return 1
        except ReviewRefusal as exc:
            print("refused: " + "; ".join(exc.problems))
            return 1
        print(json.dumps(svc.reviews.view(review), indent=2))
    elif args.command == "incident-reviews":
        if svc.store.get_incident(args.incident_id) is None:
            print(f"no such incident: {args.incident_id}")
            return 1
        print(json.dumps(
            [svc.reviews.view(r) for r in svc.reviews.reviews(args.incident_id)],
            indent=2,
        ))
    elif args.command == "review-verify":
        try:
            problems = svc.reviews.verify_review(args.review_id)
        except KeyError:
            print(f"no such incident review: {args.review_id}")
            return 1
        print(json.dumps(
            {"review_id": args.review_id, "problems": problems}, indent=2
        ))
        if problems:
            return 1
    elif args.command == "access-pack":
        print(json.dumps({
            "pack": svc.access.pack.model_dump(mode="json"),
            "fingerprint": access_pack_fingerprint(svc.access.pack),
        }, indent=2))
    elif args.command == "access-denials":
        print(json.dumps(
            [d.model_dump(mode="json") for d in svc.access.denials()],
            indent=2,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
