"""Round 13 — the model gateway and its cost ledger.

Every model call routes through one governed door: recorded
(insert-only), measured, priced by the pack's stated rule and
rates in exact integer micro-dollars, and budget-judged per
incident. Refusals land on the ledger before they propagate and
never touch the port; a wrapped port's error is recorded and
propagates as-is. Totals fold from the records and re-perform
through an independent path. The ledger is an incident-scoped
read surface under the Round 11 pack, with existence hiding.
"""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
from grid_triage.access import AccessDeniedError, Principal
from grid_triage.agent import (
    BedrockProfile,
    BriefStatement,
    Citation,
    ModelNotConfigured,
    OfflineComposer,
)
from grid_triage.model_gateway import (
    MODEL_PACK,
    ModelBudgetRefusal,
    ModelGateway,
    cost_micro,
    estimate_tokens,
    fold_records,
    verify_ledger,
)
from grid_triage.seed import T0

FLOOD = "INC-ALM-FLD-001"
FACTS = [
    {"item_id": "ALM-FLD-001", "field": "kind", "value": "feeder_trip"},
    {"item_id": "ALM-FLD-001", "field": "feeder_id", "value": "FDR-07"},
]

OPERATOR = Principal(id="op.dana", role="operator")
MANAGER = Principal(id="dm.reyes", role="duty_manager")
AUDITOR = Principal(id="qa.ives", role="auditor")
DISPATCHER = Principal(id="dp.osei", role="dispatcher")
CREW = Principal(id="crew.lee", role="field_crew")


def H(principal):
    return {"x-principal-id": principal.id, "x-principal-role": principal.role}


def test_assembly_records_one_completed_call_per_brief(fed):
    fed.assembler.assemble(FLOOD, T0 + timedelta(minutes=30))
    fed.assembler.assemble(FLOOD, T0 + timedelta(minutes=45))
    records = fed.store.list_model_calls(FLOOD)
    assert [r.status for r in records] == ["completed", "completed"]
    assert len(fed.store.list_briefs(FLOOD)) == 2
    for record in records:
        assert record.profile == "offline"
        assert record.purpose == "brief"
        assert record.pack_version == MODEL_PACK["version"]
        # the pack's stated rule, recomputed by hand
        assert record.input_tokens == estimate_tokens(
            record.input_bytes, MODEL_PACK
        )
        assert record.output_tokens == estimate_tokens(
            record.output_bytes, MODEL_PACK
        )
        assert record.cost_micro == 0  # the offline profile is priced at zero


def test_ledger_fold_is_exact_and_verifies(fed):
    fed.assembler.assemble(FLOOD, T0 + timedelta(minutes=30))
    view = fed.model_gateway.ledger_view(FLOOD)
    assert view["calls_recorded"] == 1
    assert view["calls_completed"] == 1
    assert view["calls_refused_budget"] == 0
    assert view["calls_errored"] == 0
    assert view["cost_micro"] == 0
    assert verify_ledger(fed.store) == []
    assert fold_records([])["calls_recorded"] == 0


def test_priced_profile_arithmetic_is_exact(services):
    # Price the offline profile through a superseded pack: the
    # arithmetic is graded exactly, whatever the rates are.
    pack2 = json.loads(json.dumps(MODEL_PACK))
    pack2["rates_micro_per_token"]["offline"] = {"input": 3, "output": 15}
    gateway = ModelGateway(services.store, OfflineComposer(), pack=pack2)
    statements = gateway.propose_brief("INC-X", FACTS, at=T0)
    (record,) = services.store.list_model_calls("INC-X")
    expected_in = estimate_tokens(record.input_bytes, pack2)
    expected_out = estimate_tokens(record.output_bytes, pack2)
    assert record.input_tokens == expected_in
    assert record.output_tokens == expected_out
    assert record.cost_micro == cost_micro("offline", expected_in, expected_out, pack2)
    assert record.cost_micro == expected_in * 3 + expected_out * 15
    assert statements  # the wrapped port's statements pass through untouched


def test_call_budget_refuses_before_the_port_and_records_it(services):
    class CountingPort:
        profile = "offline"

        def __init__(self):
            self.calls = 0

        def propose_brief(self, facts):
            self.calls += 1
            return [
                BriefStatement(
                    text="feeder_trip on the anchor",
                    citations=[Citation(evidence_item_id="ALM-FLD-001", field="kind")],
                )
            ]

    port = CountingPort()
    pack = dict(MODEL_PACK, max_calls_per_incident=2)
    gateway = ModelGateway(services.store, port, pack=pack)
    gateway.propose_brief("INC-B", FACTS, at=T0)
    gateway.propose_brief("INC-B", FACTS, at=T0)
    with pytest.raises(ModelBudgetRefusal) as refusal:
        gateway.propose_brief("INC-B", FACTS, at=T0)
    assert refusal.value.budget == "call_budget"
    assert port.calls == 2  # the refused call never touched the port
    records = services.store.list_model_calls("INC-B")
    assert [r.status for r in records] == [
        "completed",
        "completed",
        "refused_budget",
    ]
    assert "call_budget" in records[-1].detail
    assert records[-1].cost_micro == 0
    # the budget is per incident: a neighbour is unaffected
    gateway.propose_brief("INC-C", FACTS, at=T0)
    assert port.calls == 3


def test_cost_budget_refuses_on_a_priced_profile(services):
    pack = json.loads(json.dumps(MODEL_PACK))
    pack["rates_micro_per_token"]["offline"] = {"input": 1000, "output": 1000}
    pack["max_cost_micro_per_incident"] = 1  # any completed call crosses it
    gateway = ModelGateway(services.store, OfflineComposer(), pack=pack)
    gateway.propose_brief("INC-P", FACTS, at=T0)
    with pytest.raises(ModelBudgetRefusal) as refusal:
        gateway.propose_brief("INC-P", FACTS, at=T0)
    assert refusal.value.budget == "cost_budget"
    records = services.store.list_model_calls("INC-P")
    assert [r.status for r in records] == ["completed", "refused_budget"]


def test_port_error_is_recorded_and_propagates(services):
    gateway = ModelGateway(services.store, BedrockProfile())
    with pytest.raises(ModelNotConfigured):
        gateway.propose_brief("INC-E", FACTS, at=T0)
    (record,) = services.store.list_model_calls("INC-E")
    assert record.status == "error"
    assert record.profile == "bedrock"
    assert "ModelNotConfigured" in record.detail
    assert record.cost_micro == 0 and record.input_tokens == 0
    assert verify_ledger(services.store) == []


def test_tampered_record_is_named_by_the_verifier(fed):
    fed.assembler.assemble(FLOOD, T0 + timedelta(minutes=30))
    (record,) = fed.store.list_model_calls(FLOOD)
    fed.store._run(
        "UPDATE model_calls SET payload = ? WHERE call_id = ?",
        (
            record.model_copy(
                update={"output_tokens": record.output_tokens + 7}
            ).model_dump_json(),
            record.call_id,
        ),
    )
    problems = verify_ledger(fed.store)
    assert any(record.call_id in p for p in problems)


def test_ledger_read_roles_and_existence_hiding(fed):
    fed.assembler.assemble(FLOOD, T0 + timedelta(minutes=30))
    for principal in (OPERATOR, MANAGER, AUDITOR):
        view = fed.model_gateway.ledger_view(FLOOD, principal)
        assert view["calls_completed"] == 1
    denials_before = fed.store.count_access_denials()
    for principal in (DISPATCHER, CREW):
        with pytest.raises(AccessDeniedError) as denied:
            fed.model_gateway.ledger_view(FLOOD, principal)
        assert denied.value.hidden  # answered as a non-existent incident
    assert fed.store.count_access_denials() == denials_before + 2
    # an authorised role reading a non-existent incident gets the
    # same empty fold a fresh incident gets — nothing leaks
    empty = fed.model_gateway.ledger_view("INC-NOPE-999", OPERATOR)
    assert empty["calls_recorded"] == 0


def test_ledger_api_route(fed):
    fed.assembler.assemble(FLOOD, T0 + timedelta(minutes=30))
    api.override_services(fed)
    client = TestClient(api.app)
    ok = client.get(f"/incidents/{FLOOD}/model-ledger", headers=H(OPERATOR))
    assert ok.status_code == 200
    assert ok.json()["calls_completed"] == 1
    hidden = client.get(f"/incidents/{FLOOD}/model-ledger", headers=H(CREW))
    missing = client.get("/incidents/INC-NOPE-999/model-ledger", headers=H(CREW))
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json() or (
        hidden.json()["detail"] == f"no such incident: {FLOOD}"
    )
    absent = client.get("/incidents/INC-NOPE-999/model-ledger", headers=H(OPERATOR))
    assert absent.status_code == 404
