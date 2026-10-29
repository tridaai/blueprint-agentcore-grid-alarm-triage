"""Round 11: access control — the pack and its fail-closed
judge, principals judged as carried, existence hiding graded
byte-for-byte on the API, list filtering, denied acts executing
nothing, every refusal on the denial log, and the RT-17
decision: the Round 4 closure surface under the Round 7 gate's
preconditions wherever a principal is carried.

The recipes mirror test_restoration's (the flood is
INC-ALM-FLD-001; its outage set is the BRK-070 slice of
topology v1), derived from the planted fixtures, not from the
code's own outputs.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.runtime_app as runtime_app
from grid_triage.access import (
    ACCESS_PACK_VERSION,
    ACTS,
    SURFACES,
    AccessDeniedError,
    Principal,
    access_pack_fingerprint,
    load_access_pack,
)
from grid_triage.closure import ClosureRefusal
from grid_triage.handover import HandoverRefusal
from grid_triage.seed import T0

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
BOGUS = "INC-NOPE-404"
FLOOD_DEVICES = [
    "BRK-070", "REC-071", "SEC-072", "SEC-073",
    "XF-0721", "XF-0731", "SEN-0722", "SEN-0732",
]
RESTORED_AT = T0 + timedelta(minutes=270)

OPERATOR = Principal(id="op.dana", role="operator")
DUTY = Principal(id="dm.okafor", role="duty_manager")
DISPATCHER = Principal(id="disp.ray", role="dispatcher")
CREW = Principal(id="crew.lee", role="field_crew")
AUDITOR = Principal(id="qa.ives", role="auditor")


def H(principal: Principal) -> dict:
    return {
        "X-Principal-Id": principal.id,
        "X-Principal-Role": principal.role,
    }


def _ready_flood(fed):
    """The flood assessed, classified, estimated, its work order
    approved and the crew's note on record (test_restoration's
    recipe)."""
    at = T0 + timedelta(minutes=30)
    fed.severity.assess(FLOOD, at)
    fed.cause.classify(FLOOD, at)
    fed.etr.estimate(FLOOD, at)
    order = fed.work_orders.draft(FLOOD, at)
    fed.work_orders.assign(
        order.order_id, "CREW-NORTH-VEG", "op.dana",
        T0 + timedelta(minutes=90), T0 + timedelta(minutes=270), at,
    )
    fed.work_orders.decide(
        order.order_id, "approved", "op.dana", "operator", at
    )
    fed.updates.post_update(
        FLOOD, "crew_note", "crew.lee", "field_crew",
        T0 + timedelta(minutes=47),
        note="Tree on the line at REC-071; section restored.",
    )
    return order


def _full_signals(fed, incident_id=FLOOD):
    for device_id in FLOOD_DEVICES:
        fed.restoration.record_signal(
            incident_id, "meter", "disp.ray", "dispatcher",
            RESTORED_AT - timedelta(minutes=5), "restored",
            device_id=device_id,
        )
    fed.restoration.record_signal(
        incident_id, "topology", "disp.ray", "dispatcher",
        RESTORED_AT - timedelta(minutes=4), "supplied",
        feeder_id="FDR-07",
    )


# -- the pack ---------------------------------------------------------


def test_access_pack_shape_and_fail_closed_loader(services):
    pack = services.access.pack
    assert pack.version == ACCESS_PACK_VERSION == "2026.10.1"
    assert [g.role for g in pack.roles] == [
        "operator", "duty_manager", "dispatcher", "field_crew",
        "auditor",
    ]
    # The vocabularies are closed: no grant names a surface or
    # an act outside them.
    for grant in pack.roles:
        assert set(grant.read_surfaces) <= set(SURFACES)
        assert set(grant.acts) <= set(ACTS)
    by_role = {g.role: g for g in pack.roles}
    # The auditor reads everything (the denial log included)
    # and performs nothing.
    assert set(by_role["auditor"].read_surfaces) == set(SURFACES)
    assert by_role["auditor"].acts == []
    # A crew holds exactly its work orders.
    assert set(by_role["field_crew"].read_surfaces) == {
        "work_orders", "access_pack",
    }
    assert set(by_role["field_crew"].acts) == {
        "post_field_update", "complete_work_order",
    }
    # Act grants never exceed the engine packs: signals are a
    # dispatcher/operator act, field updates are not a duty
    # manager act.
    assert set(pack.required_roles("record_signal")) == {
        "dispatcher", "operator",
    }
    assert "duty_manager" not in pack.required_roles("post_field_update")
    # The loader is fail-closed, like every pack loader here.
    with pytest.raises(ValueError):
        load_access_pack("1999.01.1")
    # The fingerprint is over content: a tampered copy differs.
    tampered = pack.model_copy(deep=True)
    tampered.roles[4].acts = ["declare_storm"]
    assert access_pack_fingerprint(tampered) != access_pack_fingerprint(pack)


def test_authentication_judged_as_carried(services):
    control = services.access
    at = T0
    # No principal where one is required.
    with pytest.raises(AccessDeniedError) as exc:
        control.authenticate(None, surface="runtime", target="x", at=at)
    assert exc.value.reason == "principal_required"
    # Padded parts are malformed — judged as carried, never
    # trimmed into validity.
    for raw in (
        {"id": " op.dana", "role": "operator"},
        {"id": "op.dana", "role": "operator "},
        {"id": "op.dana"},
        {"role": "operator"},
        {"id": "", "role": "operator"},
    ):
        with pytest.raises(AccessDeniedError) as exc:
            control.authenticate(raw, surface="runtime", target="x", at=at)
        assert exc.value.reason == "malformed_principal", raw
    with pytest.raises(AccessDeniedError) as exc:
        control.authenticate(
            {"id": "op.dana", "role": "superuser"},
            surface="runtime", target="x", at=at,
        )
    assert exc.value.reason == "unknown_role"
    # Every refusal above is on the log, in order.
    denials = control.denials()
    assert [d.reason for d in denials] == (
        ["principal_required"] + ["malformed_principal"] * 5
        + ["unknown_role"]
    )
    assert all(d.pack_version == ACCESS_PACK_VERSION for d in denials)
    # A clean pair authenticates.
    assert control.authenticate(
        {"id": "op.dana", "role": "operator"}, surface="runtime", at=at
    ) == OPERATOR


# -- existence hiding on the API ---------------------------------------

INCIDENT_GET_ROUTES = [
    "/incidents/{id}",
    "/incidents/{id}/classification",
    "/incidents/{id}/priors",
    "/incidents/{id}/pack",
    "/incidents/{id}/packs",
    "/incidents/{id}/severity",
    "/incidents/{id}/etr",
    "/incidents/{id}/closure",
    "/incidents/{id}/chain",
    "/incidents/{id}/updates",
    "/incidents/{id}/revisions",
    "/incidents/{id}/work-orders",
    "/incidents/{id}/restoration/signals",
    "/incidents/{id}/restoration/confirmation",
    "/incidents/{id}/close-act",
    "/incidents/{id}/splits",
    "/incidents/{id}/contradictions",
    "/incidents/{id}/event-log",
]


def test_api_existence_hiding_is_byte_identical(fed):
    api.override_services(fed)
    client = TestClient(api.app, raise_server_exceptions=False)
    for template in INCIDENT_GET_ROUTES:
        real = client.get(
            template.format(id=FLOOD), headers=H(CREW)
        )
        bogus = client.get(
            template.format(id=BOGUS), headers=H(CREW)
        )
        missing = client.get(template.format(id=BOGUS))
        assert real.status_code == bogus.status_code == 404, template
        # Byte-identical once the requested id (the only part of
        # the body that names the request) is aligned — and
        # identical to what a principal-less caller gets for a
        # genuinely missing incident on the same route.
        assert real.content.replace(
            FLOOD.encode(), BOGUS.encode()
        ) == bogus.content == missing.content, template
        # The hiding refusals are recorded — against the id the
        # requester supplied, and nothing else about it.
    denials = [
        d for d in fed.access.denials()
        if d.reason == "incident_not_visible"
    ]
    assert {d.target for d in denials} == {FLOOD, BOGUS}
    assert all(d.principal_id == CREW.id for d in denials)
    # Sanity: the same routes serve a role with incident scope.
    for template in INCIDENT_GET_ROUTES:
        ok = client.get(template.format(id=FLOOD), headers=H(OPERATOR))
        assert ok.status_code == 200, template


def test_api_lists_filter_and_surface_refusals(fed):
    api.override_services(fed)
    client = TestClient(api.app, raise_server_exceptions=False)
    # Lists filter, never leak counts.
    assert client.get("/incidents", headers=H(CREW)).json() == []
    assert len(client.get("/incidents", headers=H(OPERATOR)).json()) == 2
    assert client.get("/alarms", headers=H(CREW)).json() == []
    assert client.get("/work-orders", headers=H(CREW)).status_code == 200
    assert client.get("/handovers", headers=H(CREW)).json() == {
        "handovers": []
    }
    # Surface reads refuse outright where the role has no scope.
    assert client.get("/console/data", headers=H(CREW)).status_code == 403
    assert client.get("/console/data", headers=H(DISPATCHER)).status_code == 200
    assert client.get("/etr/accuracy", headers=H(CREW)).status_code == 403
    # The denial log is a guarded surface of its own.
    assert client.get("/access/denials").status_code == 200
    assert client.get(
        "/access/denials", headers=H(AUDITOR)
    ).status_code == 200
    assert client.get(
        "/access/denials", headers=H(DUTY)
    ).status_code == 200
    assert client.get(
        "/access/denials", headers=H(OPERATOR)
    ).status_code == 403
    assert client.get(
        "/access/denials", headers=H(CREW)
    ).status_code == 403
    # The pack itself is public data.
    body = client.get("/access/pack").json()
    assert body["pack"]["version"] == ACCESS_PACK_VERSION
    assert body["fingerprint"] == access_pack_fingerprint(fed.access.pack)
    # A partial pair and an unknown role are refused at
    # authentication (401) — and recorded.
    before = fed.store.count_access_denials()
    partial = client.get(
        "/incidents", headers={"X-Principal-Id": "op.dana"}
    )
    assert partial.status_code == 401
    unknown = client.get(
        "/incidents",
        headers={"X-Principal-Id": "op.dana", "X-Principal-Role": "root"},
    )
    assert unknown.status_code == 401
    reasons = [d.reason for d in fed.access.denials()[before:]]
    assert reasons == ["malformed_principal", "unknown_role"]


# -- denied acts execute nothing ---------------------------------------


def test_denied_acts_execute_nothing(fed):
    at = T0 + timedelta(minutes=30)
    # The auditor performs no acts — each attempt refused with
    # the read-only reason, nothing written, each refusal logged.
    with pytest.raises(AccessDeniedError) as exc:
        fed.storm.declare(
            "qa.ives", "operator", "audit probe", at, principal=AUDITOR
        )
    assert exc.value.reason == "read_only_role"
    assert fed.store.list_storm_declarations() == []

    order = fed.work_orders.draft(FLOOD, at)
    with pytest.raises(AccessDeniedError) as exc:
        fed.work_orders.decide(
            order.order_id, "approved", "qa.ives", "operator", at,
            principal=AUDITOR,
        )
    assert exc.value.reason == "read_only_role"
    assert fed.store.list_work_order_decisions(order.order_id) == []

    handover = fed.handover.assemble("op.dana", "operator", at)
    with pytest.raises(AccessDeniedError) as exc:
        fed.handover.accept(
            handover.handover_id, "qa.ives", "operator", at,
            principal=AUDITOR,
        )
    assert exc.value.reason == "read_only_role"
    assert fed.handover.acceptance_for(handover.handover_id) is None

    # A role without the act is refused naming the required
    # roles — dispatch cannot close, gated or otherwise.
    with pytest.raises(AccessDeniedError) as exc:
        fed.restoration.close_incident(
            FLOOD, "disp.ray", "dispatcher", RESTORED_AT, "Done.",
            recorded_at=RESTORED_AT, principal=DISPATCHER,
        )
    assert exc.value.reason == "act_not_permitted"
    assert "operator" in str(exc.value)
    assert fed.store.get_incident_closure(FLOOD) is None
    # Four attempts, four denials, in order.
    assert [d.reason for d in fed.access.denials()] == [
        "read_only_role", "read_only_role", "read_only_role",
        "act_not_permitted",
    ]


def test_actor_and_role_binding(fed):
    at = RESTORED_AT
    # A principal may not sign another person's act.
    with pytest.raises(AccessDeniedError) as exc:
        fed.closure.close_incident(
            FLOOD, "op.dana", "operator", at, "Done.", at,
            principal=Principal(id="op.rivera", role="operator"),
        )
    assert exc.value.reason == "actor_mismatch"
    # Holding an act does not let a caller record it under a
    # different role than their own.
    with pytest.raises(AccessDeniedError) as exc:
        fed.restoration.record_signal(
            FLOOD, "meter", "disp.ray", "operator", at, "restored",
            device_id="BRK-070", principal=DISPATCHER,
        )
    assert exc.value.reason == "role_mismatch"
    assert fed.restoration.list_signals(FLOOD) == []
    # The same signal under the dispatcher's own role and name
    # lands.
    fed.restoration.record_signal(
        FLOOD, "meter", "disp.ray", "dispatcher", at, "restored",
        device_id="BRK-070", principal=DISPATCHER,
    )
    assert len(fed.restoration.list_signals(FLOOD)) == 1
    # Name variants are the same person under the folding: a
    # case-variant principal passes the ACCESS layer and meets
    # the engine (here the RT-17 gate: no confirmation yet).
    with pytest.raises(ClosureRefusal):
        fed.closure.close_incident(
            FLOOD, "op.dana", "operator", at, "Done.", at,
            principal=Principal(id="OP.DANA", role="operator"),
        )


# -- RT-17: the Round 4 closure surface under the gate ------------------


def test_rt17_carried_close_requires_the_gate_preconditions(fed):
    order = _ready_flood(fed)
    recorded = T0 + timedelta(minutes=300)
    # No confirmation of record at all.
    with pytest.raises(ClosureRefusal) as exc:
        fed.closure.close_incident(
            FLOOD, "op.dana", "operator", RESTORED_AT, "Done.",
            recorded, principal=OPERATOR,
        )
    assert any(
        "no restoration confirmation" in p for p in exc.value.problems
    )
    assert fed.store.get_incident_closure(FLOOD) is None
    # A partial confirmation is named as such.
    fed.restoration.record_signal(
        FLOOD, "meter", "disp.ray", "dispatcher",
        RESTORED_AT - timedelta(minutes=5), "restored",
        device_id="BRK-070",
    )
    fed.restoration.confirm(FLOOD, RESTORED_AT)
    with pytest.raises(ClosureRefusal) as exc:
        fed.closure.close_incident(
            FLOOD, "op.dana", "operator", RESTORED_AT, "Done.",
            recorded, principal=OPERATOR,
        )
    assert any("partial" in p for p in exc.value.problems)
    # Complete confirmation, but the head order is approved and
    # uncompleted: the refusal names the order — this surface
    # cannot express the waiver the gated close can.
    _full_signals(fed)
    fed.restoration.confirm(FLOOD, RESTORED_AT)
    with pytest.raises(ClosureRefusal) as exc:
        fed.closure.close_incident(
            FLOOD, "op.dana", "operator", RESTORED_AT, "Done.",
            recorded, principal=OPERATOR,
        )
    assert any(order.order_id in p for p in exc.value.problems)
    assert fed.store.get_incident_closure(FLOOD) is None
    # Gate refusals are engine refusals, not access denials: the
    # caller's access was in order throughout.
    assert fed.store.count_access_denials() == 0
    # Complete the work and the carried close lands — and the
    # R4 accuracy-ledger contract is intact (the outcome is the
    # compared one, exactly as the principal-less path grades).
    fed.work_orders.complete(
        order.order_id, "crew.lee", "field_crew",
        RESTORED_AT - timedelta(minutes=2),
    )
    closure, outcome, _record = fed.closure.close_incident(
        FLOOD, "op.dana", "operator", RESTORED_AT,
        "Vegetation crew cleared the tree contact at REC-071.",
        recorded, principal=OPERATOR,
    )
    assert closure.incident_id == FLOOD
    assert outcome.status == "compared"
    assert fed.closure.accuracy_ledger()["overall"]["compared_count"] == 1


def test_rt17_principal_less_close_is_unchanged(fed):
    # The trusted local path keeps the Round 4 behaviour exactly:
    # no confirmation, no access artefacts, the closure lands.
    closure, _outcome, _record = fed.closure.close_incident(
        LONE, "op.dana", "operator", T0 + timedelta(hours=3),
        "Lone breaker reclosed after inspection.",
        T0 + timedelta(hours=4),
    )
    assert closure.incident_id == LONE
    assert fed.store.count_access_denials() == 0


def test_rt17_wrong_role_carried_close_is_an_access_denial(fed):
    with pytest.raises(AccessDeniedError) as exc:
        fed.closure.close_incident(
            FLOOD, "disp.ray", "dispatcher", RESTORED_AT, "Done.",
            RESTORED_AT, principal=DISPATCHER,
        )
    assert exc.value.reason == "act_not_permitted"
    assert fed.store.get_incident_closure(FLOOD) is None
    assert fed.store.count_access_denials() == 1


def test_gated_close_with_principal_is_not_double_gated(fed):
    order = _ready_flood(fed)
    _full_signals(fed)
    fed.restoration.confirm(FLOOD, RESTORED_AT)
    # The gated close under a carried principal: the waiver is
    # the gated surface's own instrument — the internal closure
    # step must not re-refuse it under the RT-17 gate.
    act, _closure, _outcome, _record = fed.restoration.close_incident(
        FLOOD, "op.dana", "operator", RESTORED_AT,
        "Crew stood down; patrol folded into morning rounds.",
        waivers={order.order_id: "Patrol folded into morning rounds."},
        recorded_at=RESTORED_AT, principal=OPERATOR,
    )
    assert act.waived_orders == [
        {
            "order_id": order.order_id,
            "reason": "Patrol folded into morning rounds.",
        }
    ]
    assert fed.store.count_access_denials() == 0


# -- handover: roles checked where principals are carried ---------------


def test_handover_roles_checked(fed):
    at = T0 + timedelta(hours=2)
    with pytest.raises(AccessDeniedError) as exc:
        fed.handover.assemble(
            "crew.lee", "field_crew", at, principal=CREW
        )
    assert exc.value.reason == "act_not_permitted"
    assert fed.handover.handovers() == []
    handover = fed.handover.assemble(
        "op.dana", "operator", at, principal=OPERATOR
    )
    # Assembler ≠ acceptor is the engine's rule and still holds
    # under principals.
    with pytest.raises(HandoverRefusal):
        fed.handover.accept(
            handover.handover_id, "op.dana", "operator", at,
            principal=OPERATOR,
        )
    acceptance = fed.handover.accept(
        handover.handover_id, "op.rivera", "operator", at,
        principal=Principal(id="op.rivera", role="operator"),
    )
    assert acceptance.handover_id == handover.handover_id


# -- the Runtime surface -------------------------------------------------


def _invoke(fed, payload):
    runtime_app._services = fed
    return runtime_app.handle_invocation(payload)


def test_runtime_existence_hiding_and_denials(fed):
    real = _invoke(
        fed,
        {
            "action": "get_severity",
            "incident_id": FLOOD,
            "principal": {"id": CREW.id, "role": CREW.role},
        },
    )
    bogus = _invoke(
        fed,
        {
            "action": "get_severity",
            "incident_id": BOGUS,
            "principal": {"id": CREW.id, "role": CREW.role},
        },
    )
    assert real == {"status": "no_such_incident", "incident_id": FLOOD}
    assert bogus == {"status": "no_such_incident", "incident_id": BOGUS}
    # A role with scope reads normally.
    ok = _invoke(
        fed,
        {
            "action": "get_severity",
            "incident_id": FLOOD,
            "principal": {"id": OPERATOR.id, "role": OPERATOR.role},
        },
    )
    assert ok["status"] == "ok"
    # A malformed principal is refused before the action runs.
    bad = _invoke(
        fed,
        {
            "action": "get_severity",
            "incident_id": FLOOD,
            "principal": {"id": " op.dana", "role": "operator"},
        },
    )
    assert bad["status"] == "access_denied"
    assert bad["reason"] == "malformed_principal"
    # A denied act executes nothing and is recorded.
    denied = _invoke(
        fed,
        {
            "action": "declare_storm",
            "declared_by": "qa.ives",
            "role": "operator",
            "reason": "audit probe",
            "principal": {"id": AUDITOR.id, "role": AUDITOR.role},
        },
    )
    assert denied["status"] == "access_denied"
    assert denied["reason"] == "read_only_role"
    assert fed.store.list_storm_declarations() == []
    # A permitted act under its own principal lands.
    recorded = _invoke(
        fed,
        {
            "action": "record_restoration_signal",
            "incident_id": FLOOD,
            "source": "meter",
            "recorded_by": "disp.ray",
            "role": "dispatcher",
            "state": "restored",
            "device_id": "BRK-070",
            "principal": {"id": DISPATCHER.id, "role": DISPATCHER.role},
        },
    )
    assert recorded["status"] == "recorded"
    # The denial log through the Runtime: guarded.
    as_operator = _invoke(
        fed,
        {
            "action": "list_access_denials",
            "principal": {"id": OPERATOR.id, "role": OPERATOR.role},
        },
    )
    assert as_operator["status"] == "access_denied"
    assert as_operator["reason"] == "surface_not_readable"
    as_auditor = _invoke(
        fed,
        {
            "action": "list_access_denials",
            "principal": {"id": AUDITOR.id, "role": AUDITOR.role},
        },
    )
    assert as_auditor["status"] == "ok"
    assert {d["reason"] for d in as_auditor["denials"]} >= {
        "incident_not_visible", "malformed_principal", "read_only_role",
    }
    pack = _invoke(fed, {"action": "get_access_pack"})
    assert pack["status"] == "ok"
    assert pack["pack"]["version"] == ACCESS_PACK_VERSION
