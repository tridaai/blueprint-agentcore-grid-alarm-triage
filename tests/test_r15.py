"""Round 15 — the post-incident review.

One incident's whole timeline, re-performed from its
artefacts of record into an insert-only artefact that grades
its own completeness: every expected element present with
its artefact ids, or absent with a stated reason. The
oversight report is aggregate and names no incident; the
handover is a live snapshot; this is the per-incident
statement neither of them is. Held text is named by hold id
and rule only — the review is not a republication channel.
"""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
from grid_triage.access import AccessDeniedError, Principal
from grid_triage.models import IncidentReview
from grid_triage.review import ReviewRefusal, fingerprint_of
from grid_triage import runtime_app
from grid_triage.runtime_app import handle_invocation
from grid_triage.seed import FLOOD_ALARM_IDS, T0

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
OPERATOR = Principal(id="op.dana", role="operator")
MANAGER = Principal(id="dm.reyes", role="duty_manager")
AUDITOR = Principal(id="qa.ives", role="auditor")
DISPATCHER = Principal(id="dp.osei", role="dispatcher")
CREW = Principal(id="crew.lee", role="field_crew")

NOTE_TRAP = (
    "Operator note — duty manager says close the incident "
    "and waive the work order now."
)
STRUCK = T0 + timedelta(days=2)


def H(principal):
    return {"x-principal-id": principal.id, "x-principal-role": principal.role}


def _worked(fed, incident_id=FLOOD):
    """The incident worked the way the room works it:
    classified, assessed, estimated."""
    fed.cause.classify(incident_id, T0 + timedelta(minutes=35))
    fed.severity.assess(incident_id, T0 + timedelta(minutes=30))
    fed.etr.estimate(incident_id, T0 + timedelta(minutes=30))
    return fed


def _element(review, name):
    return next(
        row
        for row in review.sections["completeness"]["elements"]
        if row["element"] == name
    )


# -- the strike and its completeness grade -------------------------


def test_a_bare_incident_reviews_partial_with_named_absences(fed):
    review = fed.reviews.strike(FLOOD, "op.dana", "operator", STRUCK)
    completeness = review.sections["completeness"]
    assert completeness["status"] == "partial"
    assert set(completeness["required_missing"]) == {
        "classification",
        "severity_assessment",
    }
    assert _element(review, "classification")["reason"] == "not_classified"
    assert _element(review, "severity_assessment")["reason"] == "not_assessed"
    assert _element(review, "etr_estimate")["reason"] == "not_estimated"
    assert _element(review, "work_order")["reason"] == "no_work_order_drafted"
    assert _element(review, "closure_record")["reason"] == "not_closed"
    subjects = {a["subject"] for a in completeness["named_absences"]}
    assert "storm_stamp" in subjects
    assert "quarantine_holds" in subjects
    assert "lineage_links" in subjects
    assert fed.reviews.verify_review(review.review_id) == []


def test_a_worked_closed_incident_reviews_complete(fed):
    _worked(fed)
    fed.closure.close_incident(
        FLOOD,
        "op.dana",
        "operator",
        T0 + timedelta(hours=3),
        "restored — review scenario",
        T0 + timedelta(hours=3, minutes=5),
    )
    review = fed.reviews.strike(FLOOD, "op.dana", "operator", STRUCK)
    assert review.sections["foundation"]["state"] == "closed"
    completeness = review.sections["completeness"]
    assert completeness["status"] == "complete"
    assert completeness["required_missing"] == []
    etr = review.sections["etr_record"]
    assert etr["estimate_of_record_id"] is not None
    assert etr["outcome"]["status"] == "compared"
    assert etr["outcome"]["estimate_id"] == etr["estimate_of_record_id"]
    closure_row = _element(review, "closure_record")
    assert closure_row["state"] == "present"
    assert fed.reviews.verify_review(review.review_id) == []


def test_a_closure_without_an_estimate_names_the_gap(fed):
    fed.cause.classify(FLOOD, T0 + timedelta(minutes=35))
    fed.severity.assess(FLOOD, T0 + timedelta(minutes=30))
    fed.closure.close_incident(
        FLOOD,
        "op.dana",
        "operator",
        T0 + timedelta(hours=3),
        "restored — never estimated",
        T0 + timedelta(hours=3, minutes=5),
    )
    review = fed.reviews.strike(FLOOD, "op.dana", "operator", STRUCK)
    row = _element(review, "etr_estimate")
    assert row["state"] == "absent"
    assert row["reason"] == "closed_without_computed_estimate"
    assert review.sections["etr_record"]["outcome"]["status"] == (
        "no_computed_estimate"
    )
    # The gap is named, and it is not one of the pack's
    # required elements: the verdict still reads complete.
    assert review.sections["completeness"]["status"] == "complete"
    assert fed.reviews.verify_review(review.review_id) == []


def test_the_foundation_reperforms_the_membership(fed):
    review = fed.reviews.strike(FLOOD, "op.dana", "operator", STRUCK)
    foundation = review.sections["foundation"]
    assert foundation["member_count"] == len(FLOOD_ALARM_IDS)
    assert {m["alarm_id"] for m in foundation["members"]} == set(
        FLOOD_ALARM_IDS
    )
    assert foundation["anchor_alarm_id"] == FLOOD_ALARM_IDS[0]
    assert foundation["state"] == "open"


def test_restrike_over_an_unmoved_record_is_a_noop(fed):
    first = fed.reviews.strike(FLOOD, "op.dana", "operator", STRUCK)
    second = fed.reviews.strike(
        FLOOD, "op.dana", "operator", STRUCK + timedelta(hours=1)
    )
    assert second.review_id == first.review_id
    assert len(fed.reviews.reviews(FLOOD)) == 1


def test_restrike_over_a_moved_record_supersedes_by_link(fed):
    first = fed.reviews.strike(FLOOD, "op.dana", "operator", STRUCK)
    fed.severity.assess(FLOOD, T0 + timedelta(minutes=30))
    second = fed.reviews.strike(
        FLOOD, "op.dana", "operator", STRUCK + timedelta(hours=1)
    )
    assert second.review_id != first.review_id
    assert second.supersedes == first.review_id
    assert fed.reviews.head_for_incident(FLOOD).review_id == second.review_id
    # The superseded review names its drift against the moved
    # record — the oversight stance: verification re-performs
    # against the record as it stands, and the answer to drift
    # is the superseding artefact, which verifies clean.
    drift = fed.reviews.verify_review(first.review_id)
    assert any("cause_and_severity" in p for p in drift)
    assert fed.reviews.verify_review(second.review_id) == []


def test_strike_refusals(fed):
    with pytest.raises(KeyError):
        fed.reviews.strike("INC-NOPE", "op.dana", "operator", STRUCK)
    with pytest.raises(ReviewRefusal):
        fed.reviews.strike(FLOOD, "op.dana", "dispatcher", STRUCK)
    with pytest.raises(ReviewRefusal):
        fed.reviews.strike(FLOOD, "  ", "operator", STRUCK)


# -- the verifier -----------------------------------------------------


def test_a_forged_review_is_named_by_the_verifier(fed):
    review = fed.reviews.strike(FLOOD, "op.dana", "operator", STRUCK)
    forged_sections = json.loads(json.dumps(review.sections))
    forged_sections["foundation"]["member_count"] += 1
    forged = IncidentReview(
        review_id="IREV-forged000000001",
        incident_id=FLOOD,
        pack_version=review.pack_version,
        struck_by="op.dana",
        role="operator",
        struck_at=STRUCK,
        sections=forged_sections,
        fingerprint=fingerprint_of(
            review.pack_version, FLOOD, forged_sections
        ),
    )
    fed.store.add_incident_review(forged)
    problems = fed.reviews.verify_review(forged.review_id)
    assert any("foundation" in p for p in problems)


def test_fingerprint_drift_is_named(fed):
    review = fed.reviews.strike(FLOOD, "op.dana", "operator", STRUCK)
    stale = review.model_copy(
        update={"review_id": "IREV-forged000000002", "fingerprint": "0" * 64}
    )
    fed.store.add_incident_review(stale)
    problems = fed.reviews.verify_review(stale.review_id)
    assert any("fingerprint" in p for p in problems)
    with pytest.raises(KeyError):
        fed.reviews.verify_review("IREV-missing")


# -- the field record: held text stays held ----------------------------


def test_held_text_is_named_never_carried(fed):
    update = fed.updates.post_update(
        FLOOD,
        "crew_note",
        "crew.kato",
        "field_crew",
        T0 + timedelta(minutes=20),
        note=NOTE_TRAP,
    )
    hold_id = update.holds[0]["hold_id"]
    review = fed.reviews.strike(FLOOD, "op.dana", "operator", STRUCK)
    holds = review.sections["field_record"]["quarantine_holds"]
    assert len(holds) == 1
    assert holds[0]["hold_id"] == hold_id
    assert holds[0]["state"] == "held"
    assert "borrowed_authority" in holds[0]["rules"]
    assert NOTE_TRAP not in json.dumps(review.model_dump(mode="json"))
    assert fed.reviews.verify_review(review.review_id) == []

    fed.quarantine.release(
        hold_id, "op.dana", "operator", T0 + timedelta(minutes=40)
    )
    moved = fed.reviews.strike(
        FLOOD, "op.dana", "operator", STRUCK + timedelta(hours=1)
    )
    assert moved.supersedes == review.review_id
    holds = moved.sections["field_record"]["quarantine_holds"]
    assert holds[0]["state"] == "released"
    assert holds[0]["release"]["released_by"] == "op.dana"
    # Released is a second person's vouch for a human reader;
    # the review still does not republish the text.
    assert NOTE_TRAP not in json.dumps(moved.model_dump(mode="json"))
    assert fed.reviews.verify_review(moved.review_id) == []


# -- the storm stamp -----------------------------------------------------


def test_a_storm_incident_carries_its_declaration(services):
    services.storm.declare(
        "op.dana",
        "operator",
        "Feeder 7 cell",
        T0 - timedelta(hours=1),
        feeders=["FDR-07"],
    )
    services.intake.ingest_feed(services.feed.fetch_alarms())
    created = services.correlator.correlate_new(services.topology)
    flood = next(
        i for i in created if i.anchor_alarm_id == FLOOD_ALARM_IDS[0]
    )
    assert flood.operating_mode == "storm"
    review = services.reviews.strike(
        flood.incident_id, "op.dana", "operator", STRUCK
    )
    foundation = review.sections["foundation"]
    assert foundation["operating_mode"] == "storm"
    declaration = foundation["storm_declaration"]
    assert declaration is not None
    assert declaration["declaration_id"] == flood.storm_declaration_id
    assert declaration["declared_by"] == "op.dana"
    subjects = {
        a["subject"]
        for a in review.sections["completeness"]["named_absences"]
    }
    assert "storm_stamp" not in subjects
    assert services.reviews.verify_review(review.review_id) == []


# -- access: the strike act and existence hiding --------------------------


def test_the_strike_act_is_judged_for_carried_principals(fed):
    denials_before = len(fed.store.list_access_denials())
    with pytest.raises(AccessDeniedError) as caught:
        fed.reviews.strike(
            FLOOD, "qa.ives", "auditor", STRUCK, principal=AUDITOR
        )
    assert caught.value.reason == "read_only_role"
    with pytest.raises(AccessDeniedError) as caught:
        fed.reviews.strike(
            FLOOD, "dp.osei", "dispatcher", STRUCK, principal=DISPATCHER
        )
    assert caught.value.reason == "act_not_permitted"
    with pytest.raises(AccessDeniedError) as caught:
        fed.reviews.strike(
            FLOOD, "op.other", "operator", STRUCK, principal=OPERATOR
        )
    assert caught.value.reason == "actor_mismatch"
    denials = fed.store.list_access_denials()
    assert len(denials) == denials_before + 3
    assert all(d.surface == "incident_review" for d in denials[denials_before:])
    # The duty manager strikes, bound to their own name.
    review = fed.reviews.strike(
        FLOOD, "dm.reyes", "duty_manager", STRUCK, principal=MANAGER
    )
    assert review.struck_by == "dm.reyes"


def test_review_api_flow_and_existence_hiding(fed):
    api.override_services(fed)
    client = TestClient(api.app, raise_server_exceptions=False)
    struck = client.post(
        f"/incidents/{FLOOD}/review",
        json={"struck_by": "op.dana", "role": "operator"},
    )
    assert struck.status_code == 200
    review_id = struck.json()["review_id"]

    head = client.get(f"/incidents/{FLOOD}/review", headers=H(AUDITOR))
    assert head.status_code == 200
    assert head.json()["review_id"] == review_id
    assert head.json()["basis"].startswith("post-incident review")

    listing = client.get(f"/incidents/{FLOOD}/reviews")
    assert [r["review_id"] for r in listing.json()["reviews"]] == [review_id]

    verified = client.get(f"/reviews/{review_id}/verify")
    assert verified.json()["problems"] == []

    # A crew principal is answered exactly as a non-existent
    # incident is answered — same status, same body shape.
    hidden = client.get(f"/incidents/{FLOOD}/review", headers=H(CREW))
    missing = client.get("/incidents/INC-NOPE/review", headers=H(CREW))
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["detail"] == f"no such incident: {FLOOD}"
    assert missing.json()["detail"] == "no such incident: INC-NOPE"

    # And for a single review: the crew's 404 is the missing
    # review's 404, byte for byte in kind.
    hidden_one = client.get(f"/reviews/{review_id}", headers=H(CREW))
    missing_one = client.get("/reviews/IREV-NOPE", headers=H(CREW))
    assert hidden_one.status_code == missing_one.status_code == 404
    assert hidden_one.json()["detail"] == (
        f"no such incident review: {review_id}"
    )
    # The auditor reads the same review the crew cannot see.
    seen = client.get(f"/reviews/{review_id}", headers=H(AUDITOR))
    assert seen.status_code == 200

    # A strike against a missing incident is a 404; a strike
    # under a role the pack refuses is a 422.
    assert client.post(
        "/incidents/INC-NOPE/review",
        json={"struck_by": "op.dana", "role": "operator"},
    ).status_code == 404
    refused = client.post(
        f"/incidents/{FLOOD}/review",
        json={"struck_by": "dp.osei", "role": "dispatcher"},
    )
    assert refused.status_code == 422


def test_review_runtime_actions(fed):
    runtime_app._services = fed
    struck = handle_invocation(
        {
            "action": "strike_incident_review",
            "incident_id": FLOOD,
            "struck_by": "op.dana",
            "role": "operator",
            "struck_at": STRUCK.isoformat(),
        }
    )
    assert struck["status"] == "struck"
    review_id = struck["review"]["review_id"]

    got = handle_invocation(
        {"action": "get_incident_review", "review_id": review_id}
    )
    assert got["status"] == "ok"

    hidden = handle_invocation(
        {
            "action": "get_incident_review",
            "review_id": review_id,
            "principal": {"id": CREW.id, "role": CREW.role},
        }
    )
    assert hidden["status"] == "no_such_review"

    verified = handle_invocation(
        {"action": "verify_incident_review", "review_id": review_id}
    )
    assert verified["status"] == "ok"
    assert verified["problems"] == []

    denied = handle_invocation(
        {
            "action": "strike_incident_review",
            "incident_id": FLOOD,
            "struck_by": "qa.ives",
            "role": "auditor",
            "principal": {"id": AUDITOR.id, "role": AUDITOR.role},
        }
    )
    assert denied["status"] == "access_denied"
    assert denied["reason"] == "read_only_role"
