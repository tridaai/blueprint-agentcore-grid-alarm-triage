"""AgentCore Policy pack check (Round 18).

Builds the reference feed on a fresh deployment, then grades
the Round 18 claims about the Gateway's platform layer:

- the standing pack verifies clean against the live tool
  catalogue — every tool covered, no phantom rule, shapes in
  agreement, the Cedar statements naming exactly the table's
  tools per shape;
- drift is named in both directions: a catalogue tool dropped
  from the pack, a rule naming a tool the catalogue does not
  hold, a shape stated differently, a tool dropped from (or
  added to) a Cedar statement, a denial code that is not the
  platform's;
- the decision engine re-performs the pack: an in-area call
  permits across the read, draft and notify shapes; a call
  touching a feeder outside the caller's area denies as
  out_of_area; an area-scoped call with no resolved targets
  fails closed as area_unstated; a listing needs no targets;
  a name the catalogue does not hold denies — as
  control_shaped when it carries the control vocabulary,
  else unknown_tool — and the flood incident's own feeder,
  read from the record, permits for an area that holds it
  and denies for one that does not;
- the platform's denial shape is detected in code: an HTTP
  200 JSON-RPC body carrying code −32002 reads as a policy
  denial with its reason, and raise_for_denial raises it —
  while a result body and another error code pass through
  untouched.

Run: uv run python scripts/check_policy.py
"""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from grid_triage.config import Settings  # noqa: E402
from grid_triage.policy import (  # noqa: E402
    POLICY_PACK,
    GatewayCall,
    GatewayDeniedError,
    decide,
    detect_denial,
    raise_for_denial,
    verify_policy_pack,
)
from grid_triage.seed import FLOOD_ALARM_IDS  # noqa: E402
from grid_triage.wiring import Services  # noqa: E402

AREA = ["FDR-07", "FDR-03"]


def call(tool, targets=(), area=AREA):
    return GatewayCall(
        tool=tool, target_feeders=list(targets), caller_area=list(area)
    )


def main() -> int:
    failures: list[str] = []

    def expect(condition: bool, label: str) -> None:
        print(f"  {'ok' if condition else 'FAIL'} — {label}")
        if not condition:
            failures.append(label)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        svc = Services(
            Settings(
                sqlite_path=str(tmp / "grid.db"),
                object_store_dir=str(tmp / "objects"),
            )
        )
        svc.intake.ingest_feed(svc.feed.fetch_alarms())
        created = svc.correlator.correlate_new(svc.topology)
        flood = next(
            i for i in created if i.anchor_alarm_id == FLOOD_ALARM_IDS[0]
        )

        expect(
            svc.policy.verify() == [],
            "the standing pack verifies clean against the catalogue",
        )
        expect(
            svc.policy.pack()["version"] == "2026.10.1"
            and len(svc.policy.pack()["tools"]) == 22,
            "the pack states all 22 catalogue tools under 2026.10.1",
        )

        pack = copy.deepcopy(POLICY_PACK)
        del pack["tools"]["get_incident"]
        problems = verify_policy_pack(pack)
        expect(
            any("get_incident" in p and "does not cover" in p for p in problems),
            "a catalogue tool dropped from the pack is named",
        )
        pack = copy.deepcopy(POLICY_PACK)
        pack["tools"]["trip_breaker"] = {"shape": "read", "area_scoped": True}
        problems = verify_policy_pack(pack)
        expect(
            any("trip_breaker" in p and "does not hold" in p for p in problems),
            "a rule naming a tool the catalogue does not hold is named",
        )
        pack = copy.deepcopy(POLICY_PACK)
        pack["tools"]["get_incident"]["shape"] = "draft"
        problems = verify_policy_pack(pack)
        expect(
            any("get_incident" in p and "states shape" in p for p in problems),
            "a shape stated differently from the catalogue is named",
        )
        pack = copy.deepcopy(POLICY_PACK)
        pack["cedar_policies"][0]["text"] = pack["cedar_policies"][0][
            "text"
        ].replace('        GridGateway::Action::"get_alarm",\n', "")
        problems = verify_policy_pack(pack)
        expect(
            any("get_alarm" in p and "does not name" in p for p in problems),
            "a tool dropped from a Cedar statement is named",
        )
        pack = copy.deepcopy(POLICY_PACK)
        pack["cedar_policies"][0]["text"] = pack["cedar_policies"][0][
            "text"
        ].replace(
            'GridGateway::Action::"get_alarm"',
            'GridGateway::Action::"shed_load"',
        )
        problems = verify_policy_pack(pack)
        expect(
            any("shed_load" in p for p in problems),
            "a tool added to a Cedar statement is named",
        )
        pack = copy.deepcopy(POLICY_PACK)
        pack["denial_code"] = -32601
        problems = verify_policy_pack(pack)
        expect(
            any("denial code" in p for p in problems),
            "a denial code that is not the platform's is named",
        )

        for tool in ("get_incident", "draft_work_order", "notify_operator"):
            decision = decide(call(tool, targets=["FDR-07"]))
            expect(
                decision.decision == "permit"
                and decision.reason == "permitted",
                f"an in-area {tool} call permits",
            )
        decision = decide(call("get_incident", targets=["FDR-12"]))
        expect(
            decision.decision == "deny" and decision.reason == "out_of_area",
            "a call touching a feeder outside the caller's area denies",
        )
        decision = decide(call("get_incident", targets=[]))
        expect(
            decision.decision == "deny"
            and decision.reason == "area_unstated",
            "an area-scoped call with no resolved targets fails closed",
        )
        decision = decide(call("list_alarms", targets=[], area=[]))
        expect(
            decision.decision == "permit",
            "a listing needs no targets and no area",
        )
        decision = decide(call("shed_load", targets=["FDR-07"]))
        expect(
            decision.decision == "deny"
            and decision.reason == "control_shaped",
            "a control-shaped name denies as control-shaped",
        )
        decision = decide(call("read_the_valve_manual", targets=[]))
        expect(
            decision.decision == "deny" and decision.reason == "unknown_tool",
            "a name the catalogue does not hold denies as unknown",
        )
        decision = decide(
            call(
                "get_incident",
                targets=[flood.feeder_id],
                area=[flood.feeder_id],
            )
        )
        expect(
            decision.decision == "permit",
            "the flood incident's feeder permits for an area holding it",
        )
        decision = decide(
            call("get_incident", targets=[flood.feeder_id], area=["FDR-12"])
        )
        expect(
            decision.decision == "deny" and decision.reason == "out_of_area",
            "the flood incident's feeder denies for an area without it",
        )

        body = {
            "jsonrpc": "2.0",
            "id": 7,
            "error": {
                "code": -32002,
                "message": "denied by policy",
                "data": {"reason": "out_of_area", "tool": "get_incident"},
            },
        }
        denial = detect_denial(body)
        expect(
            denial is not None
            and denial.reason == "out_of_area"
            and denial.tool == "get_incident",
            "a −32002 body is detected as a policy denial with its reason",
        )
        try:
            raise_for_denial(body)
            expect(False, "raise_for_denial raises the denial it carries")
        except GatewayDeniedError:
            expect(True, "raise_for_denial raises the denial it carries")
        result = {"jsonrpc": "2.0", "id": 7, "result": {"alarms": []}}
        expect(
            raise_for_denial(result) is result
            and detect_denial(
                {"jsonrpc": "2.0", "id": 7, "error": {"code": -32602}}
            )
            is None,
            "a result body and another error code are not policy denials",
        )

    if failures:
        print(f"POLICY CHECK: FAIL ({len(failures)})")
        return 1
    print("POLICY CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
