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
        name="get_work_order",
        shape=ToolShape.READ,
        description="Read an incident's current work-order draft with its derived state, assignment and decision (Round 5).",
    ),
    ToolSpec(
        name="list_work_orders",
        shape=ToolShape.READ,
        description="List work-order versions and their derived states, optionally filtered by state (Round 5).",
    ),
    ToolSpec(
        name="get_topology_versions",
        shape=ToolShape.READ,
        description="List the topology versions the store holds and the declaration acts recorded for them (Round 6).",
    ),
    ToolSpec(
        name="get_field_updates",
        shape=ToolShape.READ,
        description="Read an incident's field updates — crew notes, damage findings, alarm observations with their pinned-topology correlation outcomes (Round 6).",
    ),
    ToolSpec(
        name="get_incident_revisions",
        shape=ToolShape.READ,
        description="Read an incident's revision chain: the re-correlation records linking it to its revisions under newer topology versions, with their diffs (Round 6).",
    ),
    ToolSpec(
        name="get_restoration_confirmation",
        shape=ToolShape.READ,
        description="Read an incident's restoration confirmation: the evidence classes present or absent, restored vs outstanding devices and customers, computed from the recorded signals (Round 7).",
    ),
    ToolSpec(
        name="get_incident_event_log",
        shape=ToolShape.READ,
        description="Read an incident's event log: the full artefact chain in timestamp order, each line carrying its artefact id, with the verifier's verdict (Round 7).",
    ),
    ToolSpec(
        name="get_storm_state",
        shape=ToolShape.READ,
        description="Read the operating state for a feeder at a moment: normal, or storm mode with the declaration act that put it there and the scope in force (Round 8). Storm mode is declared by named operator acts only — this tool reads the fold, it cannot change it.",
    ),
    ToolSpec(
        name="get_storm_review",
        shape=ToolShape.READ,
        description="Read a storm window's post-storm review: the incidents the declaration stamped, their severity bands, closures and storm-only ETR accuracy, assembled from artefacts after stand-down (Round 8).",
    ),
    ToolSpec(
        name="get_incident_review",
        shape=ToolShape.READ,
        description="Read an incident's post-incident review: the standing review artefact re-performing the incident's whole timeline from its artefacts of record — foundation, severity and ETR history, work orders, the field record, restoration and closure, lineage — with its completeness graded on its face, absences named with their reasons (Round 15).",
    ),
    ToolSpec(
        name="get_console_board",
        shape=ToolShape.READ,
        description="Read the operations board: open incidents by severity with their mode stamps, the feeder board, the ETR board with basis labels, the work-order queue, the storm banner and the latest shift handover — assembled only from artefacts of record (Round 9).",
    ),
    ToolSpec(
        name="get_shift_handover",
        shape=ToolShape.READ,
        description="Read a shift handover artefact and its acceptance: the sections it froze from the record, the record hash the acceptance binds, and its staleness against the live record, named by the verifier (Round 9).",
    ),
    ToolSpec(
        name="assemble_incident_brief",
        shape=ToolShape.DRAFT,
        description="Draft the incident brief from correlated alarms, every statement cited.",
    ),
    ToolSpec(
        name="draft_work_order",
        shape=ToolShape.DRAFT,
        description="Draft a work order for an incident from the classification, topology and ETR of record — a draft only; a named operator's act approves it (Round 5).",
    ),
    ToolSpec(
        name="notify_operator",
        shape=ToolShape.NOTIFY,
        description="Record an operator notification against an incident.",
    ),
]


def tool_names() -> list[str]:
    return [t.name for t in TOOL_REGISTRY]
