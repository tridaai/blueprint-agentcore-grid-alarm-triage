"""Operator CLI: read-only views over the store, plus the recorded
human acts — the Round 2 acknowledgement, the Round 3 pack seal.
None of them is an operation on the grid (Law 1 — the CLI has no
verb that operates anything)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from grid_triage.packs import PackRefusal
from grid_triage.severity import SeverityRefusal
from grid_triage.wiring import build_services


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
