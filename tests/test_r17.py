"""Round 17 — dual control is the only route.

Round 16 gave the topology declaration a second pair of
eyes but left the direct act standing for versions no
proposal stood over — the README's honest status stated the
limit plainly. Round 17 closes it in the build's own
posture: topology pack 2026.10.2 states the route as data,
and the direct act refuses every attempt, naming the
governed route and striking nothing. The closure governs
new declarations — a standing direct declaration struck
under the 2026.10.1 pack stays valid and verifiable — and
every standing declaration gains a verifier, so writing the
artefact straight into the store is named too.
"""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
import grid_triage.cli as cli
import grid_triage.runtime_app as runtime_app
from grid_triage.access import AccessDeniedError, Principal
from grid_triage.models import TopologyDeclaration
from grid_triage.revisions import (
    TOPOLOGY_PACK_V1,
    TopologyService,
    _hash_id,
)
from grid_triage.revisions import (
    TopologyRefusal,
)
from grid_triage.runtime_app import handle_invocation
from grid_triage.seed import T0

FLOOD = "INC-ALM-FLD-001"
OPERATOR = Principal(id="op.dana", role="operator")
MANAGER = Principal(id="dm.reyes", role="duty_manager")
DISPATCHER = Principal(id="dp.osei", role="dispatcher")
CREW = Principal(id="crew.lee", role="field_crew")

DECLARE_AT = T0 - timedelta(days=1)
EFFECTIVE_AT = T0 - timedelta(days=1)
PROPOSED_AT = T0 + timedelta(hours=4)
PROPOSED_EFFECTIVE_AT = T0 + timedelta(hours=5)
DECIDED_AT = T0 + timedelta(hours=4, minutes=30)


def H(principal):
    return {"x-principal-id": principal.id, "x-principal-role": principal.role}


def _route_named(problems) -> bool:
    return any(
        "closed" in p and "decide_topology" in p and "dual control" in p
        for p in problems
    )


def _declare_direct(services, **kw):
    return services.topology_acts.declare(
        kw.get("version", "v2"),
        kw.get("by", "op.dana"),
        kw.get("role", "operator"),
        kw.get("at", PROPOSED_AT),
        kw.get("effective", PROPOSED_EFFECTIVE_AT),
        principal=kw.get("principal"),
    )


# -- the closure ---------------------------------------------------


def test_direct_declare_refused_and_strikes_nothing(services):
    with pytest.raises(TopologyRefusal) as exc:
        _declare_direct(services)
    assert _route_named(exc.value.problems)
    assert services.topology_acts.declaration_for("v2") is None
    assert services.topology_acts.declarations() == []
    # Nothing was loaded on the attempt's strength either.
    assert services.store.list_topology_versions() == ["v1"]


def test_the_refusal_is_total_across_signers_and_shapes(services):
    # A duty manager's signature closes no gap.
    with pytest.raises(TopologyRefusal) as exc:
        _declare_direct(services, by="dm.reyes", role="duty_manager")
    assert _route_named(exc.value.problems)
    # An undeclared version carries both problems.
    with pytest.raises(TopologyRefusal) as exc:
        _declare_direct(services, version="v3")
    assert any("cannot be declared" in p for p in exc.value.problems)
    assert _route_named(exc.value.problems)
    # A backdated attempt is refused for both reasons.
    with pytest.raises(TopologyRefusal) as exc:
        _declare_direct(
            services, at=PROPOSED_EFFECTIVE_AT, effective=PROPOSED_AT
        )
    assert any("before it is declared" in p for p in exc.value.problems)
    assert _route_named(exc.value.problems)
    assert services.topology_acts.declarations() == []


def test_pending_proposal_names_both_problems(services):
    services.topology_acts.propose(
        "v2", "op.dana", "operator", PROPOSED_AT, PROPOSED_EFFECTIVE_AT
    )
    with pytest.raises(TopologyRefusal) as exc:
        _declare_direct(services)
    assert any(
        "pending dual-control proposal" in p for p in exc.value.problems
    )
    assert _route_named(exc.value.problems)


def test_a_rejection_does_not_reopen_the_direct_route(services):
    acts = services.topology_acts
    proposal = acts.propose(
        "v2", "op.dana", "operator", PROPOSED_AT, PROPOSED_EFFECTIVE_AT
    )
    acts.decide(
        proposal.proposal_id, "dm.reyes", "duty_manager", DECIDED_AT,
        "rejected", reason="the v2 split is not surveyed yet",
    )
    with pytest.raises(TopologyRefusal) as exc:
        _declare_direct(services)
    assert _route_named(exc.value.problems)
    assert acts.declaration_for("v2") is None


def test_a_confirmation_does_not_reopen_the_direct_route(services):
    acts = services.topology_acts
    proposal = acts.propose(
        "v2", "op.dana", "operator", PROPOSED_AT, PROPOSED_EFFECTIVE_AT
    )
    acts.decide(
        proposal.proposal_id, "dm.reyes", "duty_manager", DECIDED_AT,
        "confirmed",
    )
    with pytest.raises(TopologyRefusal) as exc:
        _declare_direct(services)
    assert any("already declared effective" in p for p in exc.value.problems)
    assert _route_named(exc.value.problems)


# -- the two doors: the guard first, the closure behind it --------


def test_access_denial_still_lands_before_the_service(services):
    denials = services.store.count_access_denials()
    with pytest.raises(AccessDeniedError):
        _declare_direct(services, principal=DISPATCHER)
    assert services.store.count_access_denials() == denials + 1
    # A principal the pack grants passes the guard and meets
    # the closure instead — no denial is recorded for it.
    with pytest.raises(TopologyRefusal) as exc:
        _declare_direct(services, principal=OPERATOR)
    assert _route_named(exc.value.problems)
    assert services.store.count_access_denials() == denials + 1


# -- the governed route under the current pack ----------------------


def test_the_governed_route_strikes_a_verifiable_declaration(fed):
    acts = fed.topology_acts
    proposal = acts.propose(
        "v2", "op.dana", "operator", PROPOSED_AT, PROPOSED_EFFECTIVE_AT
    )
    _, declaration = acts.decide(
        proposal.proposal_id, "dm.reyes", "duty_manager", DECIDED_AT,
        "confirmed",
    )
    assert declaration.pack_version == "2026.10.2"
    assert declaration.proposed_by == "op.dana"
    assert declaration.proposal_id == proposal.proposal_id
    assert acts.verify_proposal(proposal.proposal_id) == []
    assert acts.verify_declarations() == []
    revision = fed.revisions.recorrelate(
        FLOOD, "op.dana", "operator", T0 + timedelta(hours=6)
    )
    assert revision.target_topology_version == "v2"


# -- the historical posture stays valid ------------------------------


def test_a_v1_direct_declaration_stands_under_the_current_pack(fed):
    legacy = TopologyService(fed.store, pack=TOPOLOGY_PACK_V1)
    declaration = legacy.declare(
        "v2", "op.dana", "operator", DECLARE_AT, EFFECTIVE_AT
    )
    assert declaration.proposal_id is None
    assert declaration.pack_version == "2026.10.1"
    assert declaration.declared_by == "op.dana"
    # The current pack's service reads the same standing: the
    # declaration is there, it verifies, and re-correlation
    # under it works exactly as it did.
    acts = fed.topology_acts
    assert acts.declaration_for("v2") == declaration
    assert acts.verify_declarations() == []
    revision = fed.revisions.recorrelate(
        FLOOD, "op.dana", "operator", T0 + timedelta(minutes=50)
    )
    assert revision.target_topology_version == "v2"


# -- the verifier against the store -----------------------------------


def _forged_declaration(**overrides) -> TopologyDeclaration:
    from grid_triage.topology import load_topology_file

    body = {
        "version": "v2",
        "pack_version": "2026.10.2",
        "declared_by": "op.dana",
        "role": "operator",
        "declared_at": PROPOSED_AT.isoformat(),
        "effective_at": PROPOSED_EFFECTIVE_AT.isoformat(),
        "change_set": load_topology_file("v2")["change_set"],
        "proposed_by": None,
        "proposal_id": None,
    }
    body.update(overrides)
    return TopologyDeclaration(
        declaration_id=_hash_id("TOPDECL", body), **body
    )


def test_verify_names_a_forged_direct_declaration(services):
    # A direct declaration stamped with the pack that closed
    # the direct act, written straight into the store with a
    # self-consistent id: no route the record allows struck
    # it, and the verifier says so.
    forged = _forged_declaration()
    services.store.add_topology_declaration(forged)
    problems = services.topology_acts.verify_declarations()
    assert any(
        "no route the record allows" in p and forged.declaration_id in p
        for p in problems
    )


def test_verify_names_a_tampered_declaration(services):
    from grid_triage.topology import load_topology_file

    payload = load_topology_file("v2")
    tampered = _forged_declaration(
        pack_version="2026.10.1",
        change_set=payload["change_set"][:-1],
    )
    services.store.add_topology_declaration(tampered)
    problems = services.topology_acts.verify_declarations()
    assert any("change set does not match" in p for p in problems)
    # The same artefact under a wrong id is named for that too.
    wrong_id = tampered.model_copy(
        update={"declaration_id": "TOPDECL-forged00000000 1".replace(" ", "")}
    )
    services.store.add_topology_declaration(wrong_id)
    problems = services.topology_acts.verify_declarations()
    assert any("does not recompute" in p for p in problems)
    assert any("carries 2 standing declarations" in p for p in problems)


def test_verify_names_a_declaration_naming_a_missing_proposal(services):
    forged = _forged_declaration(
        pack_version="2026.10.1",
        proposed_by="op.dana",
        proposal_id="TOPPROP-nope00000000",
    )
    services.store.add_topology_declaration(forged)
    problems = services.topology_acts.verify_declarations()
    assert any("not in the record" in p for p in problems)


def test_verify_is_clean_on_an_empty_and_a_governed_store(services, fed):
    assert services.topology_acts.verify_declarations() == []
    acts = fed.topology_acts
    proposal = acts.propose(
        "v2", "op.dana", "operator", PROPOSED_AT, PROPOSED_EFFECTIVE_AT
    )
    acts.decide(
        proposal.proposal_id, "dm.reyes", "duty_manager", DECIDED_AT,
        "confirmed",
    )
    assert acts.verify_declarations() == []


# -- surfaces ----------------------------------------------------------


def test_direct_declare_refused_on_the_api_and_verify_reads_clean(fed):
    api.override_services(fed)
    client = TestClient(api.app, raise_server_exceptions=False)
    refused = client.post(
        "/topology/declare",
        json={
            "version": "v2",
            "declared_by": "op.dana",
            "role": "operator",
            "declared_at": PROPOSED_AT.isoformat(),
            "effective_at": PROPOSED_EFFECTIVE_AT.isoformat(),
        },
    )
    assert refused.status_code == 422
    assert _route_named(refused.json()["detail"]["problems"])
    assert fed.topology_acts.declaration_for("v2") is None

    verify = client.get("/topology/verify")
    assert verify.status_code == 200
    assert verify.json() == {"problems": []}
    hidden = client.get("/topology/verify", headers=H(CREW))
    assert hidden.json() == {"problems": []}


def test_direct_declare_refused_on_the_runtime_and_verify_action(fed):
    runtime_app._services = fed
    refused = handle_invocation(
        {
            "action": "declare_topology",
            "version": "v2",
            "declared_by": "op.dana",
            "role": "operator",
            "declared_at": PROPOSED_AT.isoformat(),
            "effective_at": PROPOSED_EFFECTIVE_AT.isoformat(),
        }
    )
    assert refused["status"] == "refused"
    assert _route_named(refused["problems"])
    assert fed.topology_acts.declaration_for("v2") is None

    verify = handle_invocation({"action": "verify_topology"})
    assert verify["status"] == "ok"
    assert verify["problems"] == []


def test_direct_declare_refused_on_the_cli_and_verify_command(
    fed, monkeypatch, capsys
):
    monkeypatch.setenv("SQLITE_PATH", fed.settings.sqlite_path)
    monkeypatch.setenv("OBJECT_STORE_DIR", fed.settings.object_store_dir)
    assert (
        cli.main(
            ["declare-topology", "--by", "op.dana", "--role", "operator",
             "--declared-at", PROPOSED_AT.isoformat(),
             "--effective-at", PROPOSED_EFFECTIVE_AT.isoformat()]
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "refused:" in out and "closed" in out
    assert fed.topology_acts.declaration_for("v2") is None

    assert cli.main(["verify-topology"]) == 0
    assert json.loads(capsys.readouterr().out) == {"problems": []}
