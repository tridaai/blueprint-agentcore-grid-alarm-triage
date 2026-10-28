"""Operator CLI: read-only views over the store, plus the Round 2
acknowledgement act — a recorded human act, not an operation on
the grid (Law 1 — the CLI has no verb that operates anything)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
