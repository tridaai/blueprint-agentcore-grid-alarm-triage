"""The AgentCore Policy pack — Law 1 at the platform layer (Round 18).

Since Round 1 the architecture has stated the guardrail twice:
once by construction in the agent's tool catalogue (no
control-shaped tool exists, and the registry scan grades that),
and once at the platform layer, by an AgentCore Policy pack on
the Gateway — "designed, not yet shipped", in the architecture
doc's own words, every round since. Round 11 shipped the
in-repo access pack, which answers a different question (who
may call this repo's surfaces). This module ships the other
one: the pack the deployment attaches to the Gateway, where
the Gateway is the single tool path between the agent and the
historian, OMS and AMI targets behind it.

The pack is data, in the repo's one pattern:

- **The tool table** states, for every tool in the catalogue,
  its shape and whether the tool is *area-scoped* — it touches
  one feeder's operational record (a single incident, a single
  alarm, a storm window, a draft or notification against one
  incident), as against the listings and the network-wide
  topology reference reads, which are area-neutral. The table
  is written out in full, not derived from the registry: a
  catalogue that grows a tool the pack does not state is
  *drift*, and drift is exactly what the verifier below names.
- **The Cedar statements** are the pack's platform face: one
  permit per shape, each naming its tools explicitly, each
  carrying the area condition — the call's target feeders must
  sit inside the caller's area. Cedar's default is deny, so
  anything the permits do not cover is denied at the Gateway:
  there is no statement to write for a control-shaped call,
  because a permit for it cannot be written from this table.
  The action namespace (`GridGateway::Action`) is the one the
  deployment's Gateway target publishes; binding it is the
  deployment's act when it attaches the pack.
- **The decision engine** re-performs the pack in code, so
  the pack's semantics are graded here, not trusted to a text
  nobody executes: `decide` judges one Gateway call and names
  its reason. Two refusals are the engine's, stated in the
  pack as rules: a call naming a tool the catalogue does not
  hold is denied — as `control_shaped` when the name carries
  the registry scan's control vocabulary, else `unknown_tool`
  — and an area-scoped call whose targets the Gateway target
  could not resolve is denied `area_unstated` rather than
  waved through on an empty set: the area condition over no
  targets proves nothing, so the call fails closed.
- **Denials are detected in code.** A Gateway policy denial is
  not an HTTP error: it arrives as HTTP 200 with a JSON-RPC
  error body, code −32002. `detect_denial` reads that shape
  and `raise_for_denial` turns it into a named exception, so
  no caller can mistake a denied call for an empty result.

The verifier (`verify_policy_pack`) re-performs the pack
against the live catalogue: a catalogue tool the pack does not
cover, a pack rule naming a tool the catalogue does not hold,
a shape the pack states differently from the registry, and a
Cedar statement whose action list has drifted from the table
are each named. It is read on the API, the CLI and the Runtime
under the access pack's `policy` surface, and graded by
`make policy-check`.

Honest scope: no live Gateway is called from this repo, and
none is claimed. The deliverable is the pack, its decision
semantics re-performed and graded in code, and the denial
detection every Gateway caller needs — the deployment attaches
the pack to its Gateway and the graded semantics are the ones
it attaches.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel

from grid_triage.evidence import canonical_json
from grid_triage.models import ToolSpec
from grid_triage.tools import TOOL_REGISTRY

POLICY_PACK_VERSION = "2026.10.1"

# The platform's denial shape: HTTP 200, JSON-RPC error code
# −32002. Stated in the pack so a pack that drifted from the
# platform's shape is itself a named problem.
DENIAL_CODE = -32002

# The registry scan's control vocabulary (the eval gate's
# CONTROL_MARKERS, stated here as the pack's own list): a call
# naming a tool outside the catalogue whose name carries one
# of these markers is a control-shaped call, not merely an
# unknown one.
CONTROL_MARKERS = (
    "switch",
    "reclose",
    "shed",
    "energize",
    "setpoint",
    "operate",
    "control",
    "command",
    "execute",
)

REASON_PERMITTED = "permitted"
REASON_UNKNOWN_TOOL = "unknown_tool"
REASON_CONTROL_SHAPED = "control_shaped"
REASON_OUT_OF_AREA = "out_of_area"
REASON_AREA_UNSTATED = "area_unstated"
REASON_GATEWAY_DENIED = "gateway_denied"

_READ_TOOLS = [
    "list_alarms",
    "get_alarm",
    "get_incident",
    "get_topology_slice",
    "get_severity_assessment",
    "get_etr_estimate",
    "list_escalation_queue",
    "get_work_order",
    "list_work_orders",
    "get_topology_versions",
    "get_field_updates",
    "get_incident_revisions",
    "get_restoration_confirmation",
    "get_incident_event_log",
    "get_storm_state",
    "get_storm_review",
    "get_incident_review",
    "get_console_board",
    "get_shift_handover",
]

# Area-scoped reads: the call resolves to one feeder's
# operational record, so the Gateway target must state the
# feeders it touches and the area condition judges them. The
# listings (list_alarms, list_escalation_queue, list_work_
# orders, get_console_board) and the topology reference reads
# (get_topology_slice, get_topology_versions) are network-wide
# by construction and area-neutral: their per-record reads
# behind them are the scoped ones.
_AREA_SCOPED_READS = [
    "get_alarm",
    "get_incident",
    "get_severity_assessment",
    "get_etr_estimate",
    "get_work_order",
    "get_field_updates",
    "get_incident_revisions",
    "get_restoration_confirmation",
    "get_incident_event_log",
    "get_storm_state",
    "get_storm_review",
    "get_incident_review",
    "get_shift_handover",
]

_DRAFT_TOOLS = ["assemble_incident_brief", "draft_work_order"]
_NOTIFY_TOOLS = ["notify_operator"]


def _cedar(shape: str, tools: list[str]) -> str:
    actions = ",\n        ".join(
        f'GridGateway::Action::"{name}"' for name in tools
    )
    return (
        f"// permit-{shape}: the catalogue's {shape} tools, inside\n"
        f"// the caller's area. Anything no permit covers is\n"
        f"// denied by default — a control-shaped call has no\n"
        f"// permit to be written from.\n"
        f"permit (\n"
        f"    principal,\n"
        f"    action in [\n        {actions}\n    ],\n"
        f'    resource == GridGateway::Gateway::"grid-triage"\n'
        f") when {{\n"
        f"    context.caller_area.containsAll(context.target_feeders)\n"
        f"}};"
    )


def _tool_table() -> dict:
    table = {}
    for name in _READ_TOOLS:
        table[name] = {
            "shape": "read",
            "area_scoped": name in _AREA_SCOPED_READS,
        }
    for name in _DRAFT_TOOLS:
        # A draft is written against one incident: scoped.
        table[name] = {"shape": "draft", "area_scoped": True}
    for name in _NOTIFY_TOOLS:
        # A notification is recorded against one incident.
        table[name] = {"shape": "notify", "area_scoped": True}
    return table


POLICY_PACK = {
    "version": POLICY_PACK_VERSION,
    # The platform denial this pack's callers must detect: an
    # HTTP 200 JSON-RPC error body carrying this code.
    "denial_code": DENIAL_CODE,
    # Every catalogue shape permits, inside the caller's area;
    # there is no fourth shape, and no permit for one could be
    # written from this table.
    "shapes": {"read": "permit", "draft": "permit", "notify": "permit"},
    "control_markers": list(CONTROL_MARKERS),
    # The engine's stated rules beyond the Cedar permits: an
    # area-scoped call with no resolved targets fails closed.
    "fail_closed_on_unstated_area": True,
    "tools": _tool_table(),
    "cedar_policies": [
        {"id": "permit-read", "shape": "read", "text": _cedar("read", _READ_TOOLS)},
        {"id": "permit-draft", "shape": "draft", "text": _cedar("draft", _DRAFT_TOOLS)},
        {"id": "permit-notify", "shape": "notify", "text": _cedar("notify", _NOTIFY_TOOLS)},
    ],
}


class GatewayCall(BaseModel):
    """One tool call as the Gateway sees it: the tool named,
    the feeders the call would touch — resolved by the Gateway
    target from the call's arguments, which is the target's
    stated duty — and the caller's area, the feeder remit the
    identity layer states for the caller."""

    tool: str
    target_feeders: list[str] = []
    caller_area: list[str] = []
    caller: str | None = None


class PolicyDecision(BaseModel):
    """The pack's judgement of one call, with its reason named
    from the pack's closed vocabulary."""

    tool: str
    decision: str  # "permit" | "deny"
    reason: str
    shape: str | None
    pack_version: str


class PolicyDenial(BaseModel):
    """A Gateway policy denial as detected in a response body:
    the platform's code, its message, and the reason the body
    carries when it carries one."""

    code: int
    message: str
    reason: str
    tool: str | None = None


class GatewayDeniedError(RuntimeError):
    """A Gateway call the policy pack denied, raised so no
    caller can read the denial's empty result as data."""

    def __init__(self, denial: PolicyDenial):
        self.denial = denial
        super().__init__(
            f"gateway policy denial ({denial.code}): {denial.message}"
        )


def policy_pack_fingerprint(pack: dict | None = None) -> str:
    """A content fingerprint of the pack: sha256 over its
    canonical JSON, the access pack's own discipline."""
    pack = pack or POLICY_PACK
    return hashlib.sha256(canonical_json(pack)).hexdigest()


def decide(call: GatewayCall, pack: dict | None = None) -> PolicyDecision:
    """Judge one Gateway call under the pack. The order is the
    pack's: the tool is known or it is not; a scoped call has
    stated its targets or it has not; the targets sit inside
    the caller's area or they do not."""
    pack = pack or POLICY_PACK
    entry = pack["tools"].get(call.tool)
    if entry is None:
        lowered = call.tool.lower()
        reason = (
            REASON_CONTROL_SHAPED
            if any(marker in lowered for marker in pack["control_markers"])
            else REASON_UNKNOWN_TOOL
        )
        return PolicyDecision(
            tool=call.tool,
            decision="deny",
            reason=reason,
            shape=None,
            pack_version=pack["version"],
        )
    if (
        entry["area_scoped"]
        and pack["fail_closed_on_unstated_area"]
        and not call.target_feeders
    ):
        return PolicyDecision(
            tool=call.tool,
            decision="deny",
            reason=REASON_AREA_UNSTATED,
            shape=entry["shape"],
            pack_version=pack["version"],
        )
    if not set(call.target_feeders) <= set(call.caller_area):
        return PolicyDecision(
            tool=call.tool,
            decision="deny",
            reason=REASON_OUT_OF_AREA,
            shape=entry["shape"],
            pack_version=pack["version"],
        )
    return PolicyDecision(
        tool=call.tool,
        decision="permit",
        reason=REASON_PERMITTED,
        shape=entry["shape"],
        pack_version=pack["version"],
    )


_CEDAR_ACTION = re.compile(r'Action::"([^"]+)"')


def verify_policy_pack(
    pack: dict | None = None, registry: list[ToolSpec] | None = None
) -> list[str]:
    """Re-perform the pack against the live catalogue. Every
    drift is a named problem: a catalogue tool the pack does
    not cover, a pack rule naming a tool the catalogue does not
    hold, a shape stated differently, a Cedar statement whose
    actions have drifted from the table, or a denial code that
    is not the platform's."""
    pack = pack or POLICY_PACK
    registry = registry if registry is not None else TOOL_REGISTRY
    problems: list[str] = []
    table = pack["tools"]
    by_name = {tool.name: tool for tool in registry}
    for tool in registry:
        entry = table.get(tool.name)
        if entry is None:
            problems.append(
                f"the catalogue holds tool {tool.name}, which the "
                f"policy pack does not cover"
            )
        elif entry["shape"] != tool.shape.value:
            problems.append(
                f"tool {tool.name}: the policy pack states shape "
                f"{entry['shape']}, the catalogue states "
                f"{tool.shape.value}"
            )
    for name in table:
        if name not in by_name:
            problems.append(
                f"the policy pack names tool {name}, which the "
                f"catalogue does not hold"
            )
    if pack["denial_code"] != DENIAL_CODE:
        problems.append(
            f"the policy pack states denial code "
            f"{pack['denial_code']}, the platform's is {DENIAL_CODE}"
        )
    seen_ids: set[str] = set()
    for statement in pack["cedar_policies"]:
        if statement["id"] in seen_ids:
            problems.append(
                f"cedar statement id {statement['id']} appears twice"
            )
        seen_ids.add(statement["id"])
        stated = set(_CEDAR_ACTION.findall(statement["text"]))
        expected = {
            name
            for name, entry in table.items()
            if entry["shape"] == statement["shape"]
        }
        for name in sorted(expected - stated):
            problems.append(
                f"cedar statement {statement['id']} does not name "
                f"tool {name} from the pack's {statement['shape']} table"
            )
        for name in sorted(stated - expected):
            problems.append(
                f"cedar statement {statement['id']} names tool "
                f"{name}, which the pack's {statement['shape']} "
                f"table does not hold"
            )
    return problems


def detect_denial(response: dict) -> PolicyDenial | None:
    """Read a Gateway response body for the policy denial
    shape: a JSON-RPC error whose code is the pack's denial
    code. Any other body — a result, another error — is not a
    policy denial and returns None."""
    if not isinstance(response, dict):
        return None
    error = response.get("error")
    if not isinstance(error, dict) or error.get("code") != DENIAL_CODE:
        return None
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    return PolicyDenial(
        code=DENIAL_CODE,
        message=str(error.get("message", "")),
        reason=str(data.get("reason", REASON_GATEWAY_DENIED)),
        tool=data.get("tool"),
    )


def raise_for_denial(response: dict) -> dict:
    """Return a Gateway response unchanged, or raise the
    denial it carries: the check every Gateway caller runs
    before reading a result, because the denial arrives as
    HTTP 200 and an unchecked caller would read its empty
    result as data."""
    denial = detect_denial(response)
    if denial is not None:
        raise GatewayDeniedError(denial)
    return response


class PolicyService:
    """The pack as a service: stateless, like the packs
    themselves — the pack is code data, its verdicts are
    recomputed, and nothing here writes the record."""

    def __init__(self, pack: dict | None = None):
        self._pack = pack or POLICY_PACK

    def pack(self) -> dict:
        return self._pack

    def fingerprint(self) -> str:
        return policy_pack_fingerprint(self._pack)

    def decide(self, call: GatewayCall) -> PolicyDecision:
        return decide(call, self._pack)

    def verify(self) -> list[str]:
        return verify_policy_pack(self._pack)
