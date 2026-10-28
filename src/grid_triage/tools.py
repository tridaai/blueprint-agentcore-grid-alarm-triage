"""The tool catalogue — Law 1: no control, ever.

Every tool the agent can be given lives in this registry, and every
entry is one of three shapes:

- ``read`` — read alarms, incidents and topology.
- ``draft`` — produce a draft artefact (the incident brief in R1;
  work orders arrive as drafts in R5).
- ``notify`` — record/send a notification.

There is deliberately no shape for operating anything: no
switching, no reclosing, no shedding, no setpoints. In production
the same catalogue is exposed through AgentCore Gateway as
read-only MCP targets, and the AgentCore Policy pack (Round 11)
denies control-shaped calls at the gateway as a second, platform
layer. The tests scan this registry mechanically: a control-shaped
tool cannot be added without failing the gate.
"""

from __future__ import annotations

from grid_triage.models import ToolShape, ToolSpec

TOOL_REGISTRY: list[ToolSpec] = [
    ToolSpec(
        name="list_alarms",
        shape=ToolShape.READ,
        description="List ingested alarms, optionally only the uncorrelated ones.",
    ),
    ToolSpec(
        name="get_alarm",
        shape=ToolShape.READ,
        description="Read one alarm by id from the append-only ingest log.",
    ),
    ToolSpec(
        name="get_incident",
        shape=ToolShape.READ,
        description="Read one incident with its alarm memberships and rules.",
    ),
    ToolSpec(
        name="get_topology_slice",
        shape=ToolShape.READ,
        description="Read the feeders, devices and critical loads of one topology version.",
    ),
    ToolSpec(
        name="get_severity_assessment",
        shape=ToolShape.READ,
        description="Read an incident's current severity assessment and its history (Round 2).",
    ),
    ToolSpec(
        name="get_etr_estimate",
        shape=ToolShape.READ,
        description="Read an incident's current ETR estimate and its history, with the estimate's stated basis (Round 4).",
    ),
    ToolSpec(
        name="list_escalation_queue",
        shape=ToolShape.READ,
        description="List severity assessments still awaiting a required operator acknowledgement (Round 2).",
    ),
    ToolSpec(
        name="assemble_incident_brief",
        shape=ToolShape.DRAFT,
        description="Draft the incident brief from correlated alarms, every statement cited.",
    ),
    ToolSpec(
        name="notify_operator",
        shape=ToolShape.NOTIFY,
        description="Record an operator notification against an incident.",
    ),
]


def tool_names() -> list[str]:
    return [t.name for t in TOOL_REGISTRY]
