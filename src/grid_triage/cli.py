"""Operator CLI: read-only views over the store (Law 1 — the CLI
has no verb that operates anything either)."""

from __future__ import annotations

import argparse
import json

from grid_triage.wiring import build_services


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grid-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("incidents")
    sub.add_parser("uncorrelated")
    p_inc = sub.add_parser("incident")
    p_inc.add_argument("incident_id")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
