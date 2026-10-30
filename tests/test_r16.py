"""Round 16 — topology dual control.

The declaration of a topology version was the one remaining
single-signature act that changes standing state
territory-wide. A proposal now states the whole declaration
it asks for and changes nothing; a second named operator —
never the proposer, under the name folding — confirms or
rejects it; confirmation strikes exactly the stated
declaration with the proposal's provenance on its face; and
while a proposal stands pending, the direct declaration of
the same version is blocked.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
from grid_triage import runtime_app
from grid_triage.access import AccessDeniedError, Principal
from grid_triage.models import TopologyDecision
from grid_triage.revisions import RevisionRefusal, TopologyRefusal
from grid_triage.runtime_app import handle_invocation
from grid_triage.seed import T0

FLOOD = "INC-ALM-FLD-001"
OPERATOR = Principal(id="op.dana", role="operator")
MANAGER = Principal(id="dm.reyes", role="duty_manager")
AUDITOR = Principal(id="qa.ives", role="auditor")
DISPATCHER = Principal(id="dp.osei", role="dispatcher")
CREW = Principal(id="crew.lee", role="field_crew")

PROPOSED_AT = T0 + timedelta(hours=4)
EFFECTIVE_AT = T0 + timedelta(hours=5)
DECIDED_AT = T0 + timedelta(hours=4, minutes=30)


def H(principal):
    return {"x-principal-id": principal.id, "x-principal-role": principal.role}


def _propose(services, by="op.dana", role="operator", **kw):
    return services.topology_acts.propose(
        "v2", by, role, PROPOSED_AT, EFFECTIVE_AT, **kw
    )


# -- the proposal itself ----------------------------------------


def test_propose_validations(services):
    acts = services.topology_acts
    with pytest.raises(TopologyRefusal):
        acts.propose("v3", "op.dana", "operator", PROPOSED_AT, EFFECTIVE_AT)
    with pytest.raises(TopologyRefusal):
        acts.propose("v2", "op.dana", "dispatcher", PROPOSED_AT, EFFECTIVE_AT)
    with pytest.raises(TopologyRefusal):
        acts.propose("v2", "  ", "operator", PROPOSED_AT, EFFECTIVE_AT)
    with pytest.raises(TopologyRefusal):
        acts.propose("v2", "op.dana", "operator", EFFECTIVE_AT, PROPOSED_AT)
    proposal = _propose(services)
    assert proposal.proposed_by == "op.dana"
    assert proposal.change_set  # carried verbatim from the payload
    assert acts.proposal_state(proposal) == "pending"


def test_a_proposal_changes_nothing(services, fed):
    acts = fed.topology_acts
    _propose(fed)
    assert acts.declaration_for("v2") is None
    assert acts.declarations() == []
    with pytest.raises(RevisionRefusal):
        fed.revisions.recorrelate(FLOOD, "op.dana", "operator", DECIDED_AT)


def test_one_live_proposal_per_version(services):
    _propose(services)
    with pytest.raises(TopologyRefusal) as exc:
        _propose(services, by="dm.reyes", role="duty_manager")
    assert any("one live proposal" in p for p in exc.value.problems)


def test_pending_proposal_blocks_direct_declare(services):
    _propose(services)
    with pytest.raises(TopologyRefusal) as exc:
        services.topology_acts.declare(
            "v2", "op.dana", "operator", PROPOSED_AT, EFFECTIVE_AT
        )
    assert any("pending dual-control proposal" in p for p in exc.value.problems)
    assert services.topology_acts.declaration_for("v2") is None


def test_propose_after_declaration_refused(services):
    # Round 17: the standing declaration is struck through the
    # governed route (the direct act is closed).
    proposal = _propose(services)
    services.topology_acts.decide(
        proposal.proposal_id, "dm.reyes", "duty_manager",
        DECIDED_AT, "confirmed",
    )
    with pytest.raises(TopologyRefusal):
        _propose(services)


# -- dual control ------------------------------------------------


def test_self_decide_refused_exact_and_folded(services):
    acts = services.topology_acts
    proposal = _propose(services)
    for who in ("op.dana", "OP.DANA", " op.dana "):
        with pytest.raises(TopologyRefusal) as exc:
            acts.decide(
                proposal.proposal_id, who, "operator", DECIDED_AT, "confirmed"
            )
        assert any("dual control" in p for p in exc.value.problems)
    assert acts.proposal_state(proposal) == "pending"
    assert acts.declaration_for("v2") is None


def test_decide_validations(services):
    acts = services.topology_acts
    proposal = _propose(services)
    with pytest.raises(KeyError):
        acts.decide("TOPPROP-nope", "dm.reyes", "duty_manager", DECIDED_AT, "confirmed")
    with pytest.raises(TopologyRefusal):
        acts.decide(
            proposal.proposal_id, "dm.reyes", "duty_manager", DECIDED_AT, "maybe"
        )
    with pytest.raises(TopologyRefusal):
        acts.decide(
            proposal.proposal_id, "dp.osei", "dispatcher", DECIDED_AT, "confirmed"
        )
    with pytest.raises(TopologyRefusal):
        acts.decide(
            proposal.proposal_id,
            "dm.reyes",
            "duty_manager",
            PROPOSED_AT - timedelta(minutes=1),
            "confirmed",
        )


def test_rejection_needs_a_reason_and_strikes_nothing(services):
    acts = services.topology_acts
    proposal = _propose(services)
    with pytest.raises(TopologyRefusal):
        acts.decide(
            proposal.proposal_id, "dm.reyes", "duty_manager", DECIDED_AT, "rejected"
        )
    record, declaration = acts.decide(
        proposal.proposal_id,
        "dm.reyes",
        "duty_manager",
        DECIDED_AT,
        "rejected",
        reason="the v2 split is not surveyed yet",
    )
    assert declaration is None
    assert acts.declaration_for("v2") is None
    assert acts.proposal_state(proposal) == "rejected"
    assert acts.verify_proposal(proposal.proposal_id) == []
    # The version is free again for a fresh proposal. Round 17
    # closes the other half of this sentence as it stood in
    # R16: the direct act does not proceed — it refuses,
    # naming the governed route, and strikes nothing.
    with pytest.raises(TopologyRefusal) as exc:
        acts.declare(
            "v2", "op.dana", "operator", PROPOSED_AT, EFFECTIVE_AT
        )
    assert any("closed" in p for p in exc.value.problems)
    assert acts.declaration_for("v2") is None


def test_a_decided_proposal_is_not_decided_twice(services):
    acts = services.topology_acts
    proposal = _propose(services)
    acts.decide(
        proposal.proposal_id, "dm.reyes", "duty_manager", DECIDED_AT, "confirmed"
    )
    with pytest.raises(TopologyRefusal) as exc:
        acts.decide(
            proposal.proposal_id, "op.dana", "operator", DECIDED_AT, "rejected",
            reason="too late",
        )
    assert any("not decided twice" in p for p in exc.value.problems)


def test_confirmation_strikes_exactly_the_stated_declaration(services, fed):
    acts = fed.topology_acts
    proposal = _propose(fed)
    record, declaration = acts.decide(
        proposal.proposal_id, "dm.reyes", "duty_manager", DECIDED_AT, "confirmed"
    )
    assert declaration is not None
    assert declaration.declared_by == "dm.reyes"
    assert declaration.role == "duty_manager"
    assert declaration.declared_at == DECIDED_AT
    assert declaration.effective_at == EFFECTIVE_AT
    assert declaration.change_set == proposal.change_set
    assert declaration.proposed_by == "op.dana"
    assert declaration.proposal_id == proposal.proposal_id
    assert acts.proposal_state(proposal) == "confirmed"
    assert acts.verify_proposal(proposal.proposal_id) == []
    revision = fed.revisions.recorrelate(
        FLOOD, "op.dana", "operator", T0 + timedelta(hours=6)
    )
    assert revision.target_topology_version == "v2"


# -- the verifier against the store -------------------------------


def test_verify_names_a_forged_self_decision(services):
    acts = services.topology_acts
    proposal = _propose(services)
    services.store.add_topology_decision(
        TopologyDecision(
            decision_id="TOPDEC-forged000000001",
            proposal_id=proposal.proposal_id,
            decision="confirmed",
            decided_by="op.dana",
            role="operator",
            decided_at=DECIDED_AT,
            reason=None,
        )
    )
    problems = acts.verify_proposal(proposal.proposal_id)
    assert any("own proposer" in p for p in problems)
    assert any("declarations name it" in p for p in problems)


def test_verify_names_a_declaration_no_confirmation_struck(services):
    acts = services.topology_acts
    proposal = _propose(services)
    acts._strike_declaration(
        "v2",
        "dm.reyes",
        "duty_manager",
        DECIDED_AT,
        EFFECTIVE_AT,
        proposal.change_set,
        proposed_by="op.dana",
        proposal_id=proposal.proposal_id,
    )
    problems = acts.verify_proposal(proposal.proposal_id)
    assert any("no confirmation struck" in p for p in problems)


# -- the access pack judges both acts ------------------------------


def test_access_pack_judges_propose_and_decide(services):
    acts = services.topology_acts
    denials = services.store.count_access_denials()
    with pytest.raises(AccessDeniedError):
        acts.propose(
            "v2",
            "dp.osei",
            "dispatcher",
            PROPOSED_AT,
            EFFECTIVE_AT,
            principal=DISPATCHER,
        )
    assert services.store.count_access_denials() == denials + 1

    proposal = _propose(services)
    with pytest.raises(AccessDeniedError):
        acts.decide(
            proposal.proposal_id,
            "qa.ives",
            "auditor",
            DECIDED_AT,
            "confirmed",
            principal=AUDITOR,
        )
    with pytest.raises(AccessDeniedError):
        acts.decide(
            proposal.proposal_id,
            "dm.reyes",
            "duty_manager",
            DECIDED_AT,
            "confirmed",
            principal=OPERATOR,  # the principal is not the decider
        )
    record, declaration = acts.decide(
        proposal.proposal_id,
        "dm.reyes",
        "duty_manager",
        DECIDED_AT,
        "confirmed",
        principal=MANAGER,
    )
    assert declaration is not None


# -- surfaces -------------------------------------------------------


def test_topology_dual_control_api_flow(fed):
    api.override_services(fed)
    client = TestClient(api.app, raise_server_exceptions=False)
    proposed = client.post(
        "/topology/propose",
        json={
            "proposed_by": "op.dana",
            "role": "operator",
            "proposed_at": PROPOSED_AT.isoformat(),
            "effective_at": EFFECTIVE_AT.isoformat(),
        },
    )
    assert proposed.status_code == 200
    proposal_id = proposed.json()["proposal_id"]
    assert proposed.json()["state"] == "pending"

    self_decide = client.post(
        f"/topology/proposals/{proposal_id}/decide",
        json={
            "decided_by": "op.dana",
            "role": "operator",
            "decision": "confirmed",
            "decided_at": DECIDED_AT.isoformat(),
        },
    )
    assert self_decide.status_code == 422

    decided = client.post(
        f"/topology/proposals/{proposal_id}/decide",
        json={
            "decided_by": "dm.reyes",
            "role": "duty_manager",
            "decision": "confirmed",
            "decided_at": DECIDED_AT.isoformat(),
        },
        headers=H(MANAGER),
    )
    assert decided.status_code == 200
    assert decided.json()["declaration"]["proposed_by"] == "op.dana"
    assert decided.json()["declaration"]["proposal_id"] == proposal_id

    got = client.get(f"/topology/proposals/{proposal_id}")
    assert got.json()["state"] == "confirmed"
    assert got.json()["problems"] == []
    assert len(got.json()["decisions"]) == 1

    listing = client.get("/topology/proposals")
    assert [p["proposal_id"] for p in listing.json()["proposals"]] == [proposal_id]
    hidden = client.get("/topology/proposals", headers=H(CREW))
    assert hidden.json()["proposals"] == []

    refused = client.post(
        "/topology/propose",
        json={
            "proposed_by": "dp.osei",
            "role": "dispatcher",
            "effective_at": EFFECTIVE_AT.isoformat(),
        },
        headers=H(DISPATCHER),
    )
    assert refused.status_code == 403

    missing = client.post(
        "/topology/proposals/TOPPROP-nope/decide",
        json={"decided_by": "dm.reyes", "role": "duty_manager", "decision": "confirmed"},
    )
    assert missing.status_code == 404


def test_topology_dual_control_runtime_actions(fed):
    runtime_app._services = fed
    proposed = handle_invocation(
        {
            "action": "propose_topology",
            "proposed_by": "op.dana",
            "role": "operator",
            "proposed_at": PROPOSED_AT.isoformat(),
            "effective_at": EFFECTIVE_AT.isoformat(),
        }
    )
    assert proposed["status"] == "proposed"
    proposal_id = proposed["proposal"]["proposal_id"]

    self_decide = handle_invocation(
        {
            "action": "decide_topology",
            "proposal_id": proposal_id,
            "decided_by": "op.dana",
            "role": "operator",
            "decision": "confirmed",
        }
    )
    assert self_decide["status"] == "refused"

    decided = handle_invocation(
        {
            "action": "decide_topology",
            "proposal_id": proposal_id,
            "decided_by": "dm.reyes",
            "role": "duty_manager",
            "decision": "confirmed",
            "decided_at": DECIDED_AT.isoformat(),
        }
    )
    assert decided["status"] == "confirmed"
    assert decided["declaration"]["proposed_by"] == "op.dana"

    listing = handle_invocation({"action": "get_topology_proposals"})
    assert listing["status"] == "ok"
    assert listing["proposals"][0]["state"] == "confirmed"

    missing = handle_invocation(
        {
            "action": "decide_topology",
            "proposal_id": "TOPPROP-nope",
            "decided_by": "dm.reyes",
            "role": "duty_manager",
            "decision": "confirmed",
        }
    )
    assert missing["status"] == "no_such_proposal"
