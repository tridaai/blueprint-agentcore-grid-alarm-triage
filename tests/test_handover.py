"""Round 9: the shift handover — assembly from artefacts only,
acceptance bound to the record hash, staleness on its face.

The handover is the board's computation frozen by a named act.
These tests grade the disciplines that make it evidence rather
than a briefing: the sections are the record's own figures, an
acceptance binds the exact record hash, a moved record makes
the handover stale and its acceptance refused, and nothing
about assembling or accepting touches the record underneath.
"""

import json
import sqlite3
from datetime import timedelta

import pytest

from grid_triage.handover import HandoverRefusal
from grid_triage.seed import FLOOD_ALARM_IDS, T0


def _fed_with_flood_assessed(services):
    services.intake.ingest_feed(services.feed.fetch_alarms())
    created = services.correlator.correlate_new(services.topology)
    flood = next(
        i for i in created if i.anchor_alarm_id == FLOOD_ALARM_IDS[0]
    )
    services.severity.assess(flood.incident_id, T0 + timedelta(minutes=30))
    return flood


def test_handover_sections_are_the_record(services):
    flood = _fed_with_flood_assessed(services)
    at = T0 + timedelta(hours=2)
    handover = services.handover.assemble("op.dana", "operator", at)
    sections = handover.sections
    open_ids = {r["incident_id"] for r in sections["open_incidents"]}
    assert flood.incident_id in open_ids
    flood_row = next(
        r for r in sections["open_incidents"]
        if r["incident_id"] == flood.incident_id
    )
    assert flood_row["band"] == "SEV-1"
    assert flood_row["score"] == 85
    assert flood_row["operating_mode"] == "normal"
    # The flood's SEV-1 assessment awaits its acknowledgement.
    acks = sections["pending_approvals"]["severity_acknowledgements"]
    assert [a["incident_id"] for a in acks] == [flood.incident_id]
    # No storm is declared anywhere in this record.
    assert sections["storm"] == {"active": False, "windows": []}
    assert sections["etr_accuracy"]["overall"]["compared_count"] == 0
    assert handover.record_hash  # the hash the acceptance will bind
    assert services.handover.verify(handover.handover_id) == []


def test_assembly_requires_a_named_operator_in_role(services):
    _fed_with_flood_assessed(services)
    with pytest.raises(HandoverRefusal):
        services.handover.assemble("", "operator", T0)
    with pytest.raises(HandoverRefusal):
        services.handover.assemble("op.dana", "field_crew", T0)


def test_identical_reassembly_is_a_noop(services):
    _fed_with_flood_assessed(services)
    at = T0 + timedelta(hours=2)
    first = services.handover.assemble("op.dana", "operator", at)
    again = services.handover.assemble(
        "op.dana", "operator", at + timedelta(minutes=5)
    )
    assert again.handover_id == first.handover_id
    assert len(services.store.list_shift_handovers()) == 1


def test_acceptance_binds_the_record_hash(services):
    _fed_with_flood_assessed(services)
    handover = services.handover.assemble(
        "op.dana", "operator", T0 + timedelta(hours=2)
    )
    acceptance = services.handover.accept(
        handover.handover_id, "op.reyes", "operator",
        T0 + timedelta(hours=2, minutes=5),
    )
    assert acceptance.handover_id == handover.handover_id
    assert acceptance.record_hash == handover.record_hash
    view = services.handover.view(handover)
    assert view["acceptance"]["accepted_by"] == "op.reyes"
    assert view["stale"] is False
    # A second acceptance is refused, naming the standing act.
    with pytest.raises(HandoverRefusal) as exc:
        services.handover.accept(
            handover.handover_id, "op.okafor", "operator",
            T0 + timedelta(hours=2, minutes=6),
        )
    assert "already accepted" in str(exc.value)


def test_self_acceptance_is_refused(services):
    _fed_with_flood_assessed(services)
    handover = services.handover.assemble(
        "op.dana", "operator", T0 + timedelta(hours=2)
    )
    with pytest.raises(HandoverRefusal) as exc:
        services.handover.accept(
            handover.handover_id, "op.dana", "operator",
            T0 + timedelta(hours=2, minutes=5),
        )
    assert "cannot accept their own handover" in str(exc.value)
    assert services.handover.acceptance_for(handover.handover_id) is None


def test_moved_record_makes_the_handover_stale(fed):
    services = fed
    flood = next(
        i
        for i in services.store.list_incidents()
        if i.anchor_alarm_id == FLOOD_ALARM_IDS[0]
    )
    lone = next(
        i
        for i in services.store.list_incidents()
        if i.incident_id != flood.incident_id
    )
    handover = services.handover.assemble(
        "op.dana", "operator", T0 + timedelta(hours=2)
    )
    assert services.handover.verify(handover.handover_id) == []
    # The record moves: the lone incident is assessed for the
    # first time — its board row and the pending queue change.
    services.severity.assess(lone.incident_id, T0 + timedelta(hours=2, minutes=10))
    problems = services.handover.verify(handover.handover_id)
    assert any("open_incidents" in p for p in problems), problems
    view = services.handover.view(handover)
    assert view["stale"] is True
    assert view["drift"] == problems
    # Acceptance of the stale handover is refused, naming it.
    with pytest.raises(HandoverRefusal) as exc:
        services.handover.accept(
            handover.handover_id, "op.reyes", "operator",
            T0 + timedelta(hours=2, minutes=11),
        )
    assert "stale" in str(exc.value)
    assert services.handover.acceptance_for(handover.handover_id) is None
    # The way forward is a fresh handover, superseding by link.
    fresh = services.handover.assemble(
        "op.dana", "operator", T0 + timedelta(hours=2, minutes=12)
    )
    assert fresh.supersedes == handover.handover_id
    assert services.handover.verify(fresh.handover_id) == []
    acceptance = services.handover.accept(
        fresh.handover_id, "op.reyes", "operator",
        T0 + timedelta(hours=2, minutes=13),
    )
    assert acceptance.record_hash == fresh.record_hash


def test_handover_storm_section_folds_at_its_own_moment(services):
    _fed_with_flood_assessed(services)
    declaration = services.storm.declare(
        "op.dana", "operator", "Storm cell crossing the Alder area",
        T0 + timedelta(minutes=10), feeders=["FDR-07"],
    )
    during = services.handover.assemble(
        "op.dana", "operator", T0 + timedelta(minutes=20)
    )
    storm = during.sections["storm"]
    assert storm["active"] is True
    assert storm["windows"][0]["declaration_id"] == declaration.declaration_id
    assert storm["windows"][0]["declared_by"] == "op.dana"
    services.storm.stand_down(
        declaration.declaration_id, "op.dana", "operator",
        "Cell has passed", T0 + timedelta(minutes=30),
    )
    after = services.handover.assemble(
        "op.reyes", "operator", T0 + timedelta(minutes=40)
    )
    assert after.sections["storm"]["active"] is False
    # The earlier handover still verifies: its storm section is
    # the fold at its own moment, not at the reader's.
    assert services.handover.verify(during.handover_id) == []


def test_handover_mutates_nothing_underneath(fed):
    services = fed
    before = {
        "incidents": len(services.store.list_incidents()),
        "assessments": sum(
            len(services.store.list_severity_assessments(i.incident_id))
            for i in services.store.list_incidents()
        ),
        "orders": len(services.store.list_all_work_orders()),
        "estimates": sum(
            len(services.store.list_etr_estimates(i.incident_id))
            for i in services.store.list_incidents()
        ),
        "closures": len(services.store.list_incident_closures()),
    }
    handover = services.handover.assemble(
        "op.dana", "operator", T0 + timedelta(hours=2)
    )
    services.handover.accept(
        handover.handover_id, "op.reyes", "operator",
        T0 + timedelta(hours=2, minutes=5),
    )
    after = {
        "incidents": len(services.store.list_incidents()),
        "assessments": sum(
            len(services.store.list_severity_assessments(i.incident_id))
            for i in services.store.list_incidents()
        ),
        "orders": len(services.store.list_all_work_orders()),
        "estimates": sum(
            len(services.store.list_etr_estimates(i.incident_id))
            for i in services.store.list_incidents()
        ),
        "closures": len(services.store.list_incident_closures()),
    }
    assert before == after


def test_tampered_sections_are_named_by_the_verifier(services):
    _fed_with_flood_assessed(services)
    handover = services.handover.assemble(
        "op.dana", "operator", T0 + timedelta(hours=2)
    )
    raw = sqlite3.connect(services.settings.sqlite_path)
    payload = json.loads(
        raw.execute(
            "SELECT payload FROM shift_handovers WHERE handover_id = ?",
            (handover.handover_id,),
        ).fetchone()[0]
    )
    payload["sections"]["etr_accuracy"]["overall"]["compared_count"] = 99
    raw.execute(
        "UPDATE shift_handovers SET payload = ? WHERE handover_id = ?",
        (json.dumps(payload), handover.handover_id),
    )
    raw.commit()
    raw.close()
    problems = services.handover.verify(handover.handover_id)
    assert any("record hash" in p for p in problems), problems
    assert any("etr_accuracy" in p for p in problems), problems
