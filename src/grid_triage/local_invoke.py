"""Local harness: invoke the agent in-process, no HTTP, no AWS.

``grid-agent-invoke`` ingests the reference feed into a throwaway
SQLite store (or the configured one), correlates, and runs the
Runtime invocation handler for the flood incident — the offline
development loop for the exact handler AgentCore Runtime calls in
production.
"""

from __future__ import annotations

import json
import sys

import grid_triage.runtime_app as runtime_app
from grid_triage.runtime_app import handle_invocation
from grid_triage.wiring import build_services


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    incident_id = argv[0] if argv else "INC-ALM-FLD-001"
    svc = build_services()
    runtime_app._services = svc  # the harness drives the same graph
    svc.intake.ingest_feed(svc.feed.fetch_alarms())
    svc.correlator.correlate_new(svc.topology)
    result = handle_invocation({"action": "assemble_brief", "incident_id": incident_id})
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "assembled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
