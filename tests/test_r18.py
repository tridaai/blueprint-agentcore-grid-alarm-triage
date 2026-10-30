"""Round 18 — the AgentCore Policy pack ships.

Law 1 has been enforced twice on paper since Round 1: by
construction in the tool catalogue, and at the platform layer
by a Cedar policy pack on the Gateway — "designed, not yet
shipped" in the architecture doc's own words. Round 18 ships
the pack: the tool table and Cedar permits as versioned data,
a decision engine that re-performs them, a verifier that
names drift against the live catalogue in both directions,
and the platform's denial shape (−32002 over HTTP 200)
detected in code.
"""

import copy
import json

from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.cli as cli
import grid_triage.runtime_app as runtime_app
from grid_triage.access import Principal
from grid_triage.policy import (
    CONTROL_MARKERS,
    POLICY_PACK,
    GatewayCall,
    GatewayDeniedError,
    decide,
    detect_denial,
    raise_for_denial,
    verify_policy_pack,
)
from grid_triage.runtime_app import handle_invocation
from grid_triage.tools import TOOL_REGISTRY

OPERATOR = Principal(id="op.dana", role="operator")
MANAGER = Principal(id="dm.reyes", role="duty_manager")
DISPATCHER = Principal(id="dp.osei", role="dispatcher")
CREW = Principal(id="crew.lee", role="field_crew")
AUDITOR = Principal(id="au.vale", role="auditor")

AREA = ["FDR-07", "FDR-03"]


def H(principal):
    return {"x-principal-id": principal.id, "x-principal-role": principal.role}


def _call(tool, targets=(), area=AREA):
    return GatewayCall(
        tool=tool, target_feeders=list(targets), caller_area=list(area)
    )


# -- the standing pack ----------------------------------------


def test_the_standing_pack_verifies_clean(services):
    assert verify_policy_pack() == []
    assert services.policy.verify() == []


def test_the_table_covers_the_catalogue_exactly():
    table = POLICY_PACK["tools"]
    assert set(table) == {t.name for t in TOOL_REGISTRY}
    for tool in TOOL_REGISTRY:
        assert table[tool.name]["shape"] == tool.shape.value
    assert len(table) == 22


def test_the_cedar_statements_name_the_tables_tools():
    import re

    for statement in POLICY_PACK["cedar_policies"]:
        stated = set(re.findall(r'Action::"([^"]+)"', statement["text"]))
        expected = {
            name
            for name, entry in POLICY_PACK["tools"].items()
            if entry["shape"] == statement["shape"]
        }
        assert stated == expected


def test_the_pack_states_the_platform_denial_shape():
    assert POLICY_PACK["denial_code"] == -32002
    assert POLICY_PACK["version"] == "2026.10.1"
    assert POLICY_PACK["control_markers"] == list(CONTROL_MARKERS)


# -- decisions --------------------------------------------------


def test_in_area_calls_permit_across_shapes():
    for tool in ("get_incident", "draft_work_order", "notify_operator"):
        decision = decide(_call(tool, targets=["FDR-07"]))
        assert decision.decision == "permit"
        assert decision.reason == "permitted"
        assert decision.pack_version == "2026.10.1"


def test_out_of_area_calls_deny():
    decision = decide(_call("get_incident", targets=["FDR-12"]))
    assert decision.decision == "deny"
    assert decision.reason == "out_of_area"
    # One feeder outside the area is enough, however many are in.
    decision = decide(
        _call("get_incident", targets=["FDR-07", "FDR-12"])
    )
    assert decision.reason == "out_of_area"


def test_a_scoped_call_with_no_targets_fails_closed():
    for tool in ("get_incident", "get_alarm", "draft_work_order"):
        decision = decide(_call(tool, targets=[]))
        assert decision.decision == "deny"
        assert decision.reason == "area_unstated"


def test_a_neutral_call_needs_no_targets():
    for tool in ("list_alarms", "get_topology_slice", "get_console_board"):
        decision = decide(_call(tool, targets=[], area=[]))
        assert decision.decision == "permit"


def test_an_unknown_tool_denies_as_unknown():
    decision = decide(_call("read_the_valve_manual", targets=[]))
    assert decision.decision == "deny"
    assert decision.reason == "unknown_tool"
    assert decision.shape is None


def test_a_control_shaped_name_denies_as_control():
    for name in ("shed_load", "operate_breaker", "remote_switch"):
        decision = decide(_call(name, targets=["FDR-07"]))
        assert decision.decision == "deny"
        assert decision.reason == "control_shaped"


def test_no_catalogue_tool_is_control_shaped():
    for tool in TOOL_REGISTRY:
        assert decide(_call(tool.name, targets=["FDR-07"])).decision == "permit"


# -- drift, named both ways --------------------------------------


def test_drift_a_catalogue_tool_the_pack_dropped_is_named():
    pack = copy.deepcopy(POLICY_PACK)
    del pack["tools"]["get_incident"]
    problems = verify_policy_pack(pack)
    assert any(
        "get_incident" in p and "does not cover" in p for p in problems
    )


def test_drift_a_phantom_rule_is_named():
    pack = copy.deepcopy(POLICY_PACK)
    pack["tools"]["trip_breaker"] = {"shape": "read", "area_scoped": True}
    problems = verify_policy_pack(pack)
    assert any(
        "trip_breaker" in p and "does not hold" in p for p in problems
    )


def test_drift_a_shape_stated_differently_is_named():
    pack = copy.deepcopy(POLICY_PACK)
    pack["tools"]["get_incident"]["shape"] = "draft"
    problems = verify_policy_pack(pack)
    assert any(
        "get_incident" in p and "states shape" in p for p in problems
    )


def test_drift_a_cedar_omission_and_addition_are_named():
    pack = copy.deepcopy(POLICY_PACK)
    read = pack["cedar_policies"][0]
    read["text"] = read["text"].replace(
        '        GridGateway::Action::"get_alarm",\n', ""
    )
    problems = verify_policy_pack(pack)
    assert any("get_alarm" in p and "does not name" in p for p in problems)

    pack = copy.deepcopy(POLICY_PACK)
    pack["cedar_policies"][0]["text"] = pack["cedar_policies"][0][
        "text"
    ].replace(
        'GridGateway::Action::"get_alarm"',
        'GridGateway::Action::"shed_load"',
    )
    problems = verify_policy_pack(pack)
    assert any("shed_load" in p for p in problems)


def test_drift_a_denial_code_that_is_not_the_platforms_is_named():
    pack = copy.deepcopy(POLICY_PACK)
    pack["denial_code"] = -32601
    problems = verify_policy_pack(pack)
    assert any("denial code" in p for p in problems)


# -- denial detection ---------------------------------------------


def _denial_body(**data):
    error = {"code": -32002, "message": "denied by policy"}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": 7, "error": error}


def test_a_policy_denial_is_detected_with_its_reason():
    denial = detect_denial(
        _denial_body(reason="out_of_area", tool="get_incident")
    )
    assert denial is not None
    assert denial.code == -32002
    assert denial.reason == "out_of_area"
    assert denial.tool == "get_incident"


def test_a_denial_without_data_carries_the_generic_reason():
    denial = detect_denial(_denial_body())
    assert denial is not None
    assert denial.reason == "gateway_denied"
    assert denial.tool is None


def test_other_bodies_are_not_policy_denials():
    assert detect_denial({"jsonrpc": "2.0", "id": 7, "result": {}}) is None
    assert (
        detect_denial(
            {"jsonrpc": "2.0", "id": 7, "error": {"code": -32602}}
        )
        is None
    )
    assert detect_denial({"result": "not even json-rpc"}) is None


def test_raise_for_denial_raises_and_passes_results_through():
    body = {"jsonrpc": "2.0", "id": 7, "result": {"alarms": []}}
    assert raise_for_denial(body) is body
    try:
        raise_for_denial(_denial_body(reason="area_unstated"))
        raise AssertionError("a denial body passed through unchecked")
    except GatewayDeniedError as exc:
        assert exc.denial.reason == "area_unstated"
        assert "-32002" in str(exc)


# -- surfaces ------------------------------------------------------


def test_the_pack_reads_on_the_api_and_hides_from_ungranted_roles(fed):
    api.override_services(fed)
    client = TestClient(api.app, raise_server_exceptions=False)
    for principal in (None, OPERATOR, MANAGER, AUDITOR):
        headers = H(principal) if principal else {}
        body = client.get("/policy/pack", headers=headers).json()
        assert body["pack"]["version"] == "2026.10.1"
        assert body["fingerprint"]
    for principal in (DISPATCHER, CREW):
        body = client.get("/policy/pack", headers=H(principal)).json()
        assert body == {"pack": None, "fingerprint": None}
    assert client.get("/policy/verify").json() == {"problems": []}


def test_the_pack_reads_on_the_runtime_and_blocks_ungranted_roles(fed):
    runtime_app._services = fed
    got = handle_invocation({"action": "get_policy_pack"})
    assert got["status"] == "ok"
    assert got["pack"]["version"] == "2026.10.1"
    verify = handle_invocation({"action": "verify_policy"})
    assert verify == {"status": "ok", "problems": []}
    blocked = handle_invocation(
        {
            "action": "verify_policy",
            "principal": {"id": CREW.id, "role": CREW.role},
        }
    )
    assert blocked["status"] == "access_denied"
    allowed = handle_invocation(
        {
            "action": "verify_policy",
            "principal": {"id": AUDITOR.id, "role": AUDITOR.role},
        }
    )
    assert allowed == {"status": "ok", "problems": []}


def test_the_pack_reads_on_the_cli(fed, monkeypatch, capsys):
    monkeypatch.setenv("SQLITE_PATH", fed.settings.sqlite_path)
    monkeypatch.setenv("OBJECT_STORE_DIR", fed.settings.object_store_dir)
    assert cli.main(["policy-pack"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["pack"]["version"] == "2026.10.1"
    assert cli.main(["verify-policy"]) == 0
    assert json.loads(capsys.readouterr().out) == {"problems": []}
