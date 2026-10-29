"""Round 12 — the storm flag on the handover surface (RT-15),
the correlation span cap (RT-16), and operations oversight
reporting.

RT-15: a storm declared after a handover does not stale the
artefact and does not block its acceptance — but the read
surface now flags it, derived at read time, alongside the
artefact. RT-16: correlation pack 2026.10.2 caps a member's
distance from the incident's anchor at four hours; the kept
2026.10.1 pack re-performs uncapped. Oversight: window reports
that are insert-only, aggregate-only and re-performable, with
rates that carry their population and read no-data when it is
empty.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import grid_triage.api as api
from grid_triage.access import AccessDeniedError, Principal
from grid_triage.correlation import (
    CORRELATION_PACK_V1,
    REASON_BEYOND_SPAN,
    correlate,
    load_correlation_pack,
)
from grid_triage.models import Alarm, OversightReport
from grid_triage.oversight import OversightRefusal, fingerprint_of
from grid_triage.seed import FLOOD_ALARM_IDS, T0
from grid_triage.topology import TopologyView, load_topology_file

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
DAY_START = datetime(2026, 10, 14, 0, 0, 0, tzinfo=timezone.utc)
DAY_END = datetime(2026, 10, 15, 0, 0, 0, tzinfo=timezone.utc)
GENERATED_AT = datetime(2026, 10, 16, 9, 0, 0, tzinfo=timezone.utc)

OPERATOR = Principal(id="op.dana", role="operator")
AUDITOR = Principal(id="qa.ives", role="auditor")
DISPATCHER = Principal(id="dp.osei", role="dispatcher")
CREW = Principal(id="crew.lee", role="field_crew")


def H(principal):
    return {"x-principal-id": principal.id, "x-principal-role": principal.role}


def _alarm(aid, device, feeder, kind, at):
    return Alarm(
        alarm_id=aid, stream_id="STREAM-SCADA-NORTH", device_id=device,
        feeder_id=feeder, kind=kind, severity="major", raised_at=at,
        message=f"{kind} on {device}",
    )


def _ladder(count=33):
    """RT-16's ladder: an anchor, then sags nine minutes apart,
    then a second trip nine minutes past the last sag."""
    alarms = [_alarm("A1", "BRK-070", "FDR-07", "breaker_trip", T0)]
    t = T0
    for i in range(count):
        t += timedelta(minutes=9)
        alarms.append(_alarm(f"B{i}", "SEC-072", "FDR-07", "voltage_sag", t))
    alarms.append(
        _alarm("A2", "BRK-070", "FDR-07", "breaker_trip", t + timedelta(minutes=9))
    )
    return alarms


# -- RT-15: the derived storm flag on the handover surface -------


def test_rt15_post_handover_declaration_is_flagged_not_staled(fed):
    handover = fed.handover.assemble("op.dana", "operator", T0 + timedelta(hours=6))
    declaration = fed.storm.declare(
        "op.dana", "operator", "New cell", T0 + timedelta(hours=7),
        feeders=["FDR-07"],
    )
    # The artefact is untouched by the flag: it does not stale,
    # and its acceptance still binds.
    assert fed.handover.verify(handover.handover_id) == []
    view = fed.handover.view(handover)
    assert view["stale"] is False
    assert view["storm_position_moved"] is True
    assert [a["declaration_id"] for a in view["storm_acts_after_handover"]] == [
        declaration.declaration_id
    ]
    assert view["storm_acts_after_handover"][0]["kind"] == "declare"
    fed.handover.accept(
        handover.handover_id, "op.reyes", "operator", T0 + timedelta(hours=8)
    )


def test_rt15_no_later_acts_no_flag(fed):
    fed.storm.declare(
        "op.dana", "operator", "Early cell", T0 + timedelta(hours=1),
        feeders=["FDR-07"],
    )
    handover = fed.handover.assemble("op.dana", "operator", T0 + timedelta(hours=6))
    view = fed.handover.view(handover)
    assert view["storm_position_moved"] is False
    assert view["storm_acts_after_handover"] == []


def test_rt15_a_stand_down_after_handover_is_flagged_too(fed):
    declaration = fed.storm.declare(
        "op.dana", "operator", "Cell", T0 + timedelta(hours=1),
        feeders=["FDR-07"],
    )
    handover = fed.handover.assemble("op.dana", "operator", T0 + timedelta(hours=2))
    assert handover.sections["storm"]["active"] is True
    fed.storm.stand_down(
        declaration.declaration_id, "op.dana", "operator", "Passed",
        T0 + timedelta(hours=3),
    )
    view = fed.handover.view(handover)
    assert view["storm_position_moved"] is True
    assert [a["kind"] for a in view["storm_acts_after_handover"]] == ["stand_down"]
    # The recorded section still says what was true at its moment.
    assert view["sections"]["storm"]["active"] is True


def test_rt15_a_backdated_act_stales_via_the_existing_drift_path(fed):
    """An act declared for a moment BEFORE the handover's is a
    different case from RT-15: it changes the fold at the
    handover's moment, so the storm section itself fails to
    recompute — and the flag, which lists only acts declared
    after the moment, stays empty."""
    handover = fed.handover.assemble("op.dana", "operator", T0 + timedelta(hours=6))
    fed.storm.declare(
        "op.dana", "operator", "Backdated cell", T0 + timedelta(hours=5),
        feeders=["FDR-12"],
    )
    view = fed.handover.view(handover)
    assert view["storm_acts_after_handover"] == []
    assert view["storm_position_moved"] is False
    assert any("storm" in p for p in view["drift"])


def test_rt15_the_flag_reaches_the_api_view(fed):
    api.override_services(fed)
    client = TestClient(api.app, raise_server_exceptions=False)
    handover = fed.handover.assemble("op.dana", "operator", T0 + timedelta(hours=6))
    fed.storm.declare(
        "op.dana", "operator", "New cell", T0 + timedelta(hours=7),
        feeders=["FDR-07"],
    )
    body = client.get(f"/handovers/{handover.handover_id}").json()
    assert body["storm_position_moved"] is True
    assert len(body["storm_acts_after_handover"]) == 1
    assert body["stale"] is False


# -- RT-16: the span cap -----------------------------------------


def test_rt16_the_ladder_is_capped_at_four_hours_from_the_anchor():
    view = TopologyView(load_topology_file("v1"))
    alarms = _ladder()
    result = correlate(alarms, view, incident_id_for=lambda a: f"INC-{a.alarm_id}")
    members = {
        m.alarm_id: m.rule_id
        for m in result.memberships
        if m.incident_id == "INC-A1"
    }
    last = max(a.raised_at for a in alarms if a.alarm_id in members)
    assert last <= T0 + timedelta(hours=4)
    assert result.uncorrelated_reasons["B26"] == REASON_BEYOND_SPAN
    # The second trip, beyond the cap, is its own incident.
    assert {i.incident_id for i in result.incidents} == {"INC-A1", "INC-A2"}
    stamped = {i.incident_id: i.correlation_pack_version for i in result.incidents}
    assert stamped == {"INC-A1": "2026.10.2", "INC-A2": "2026.10.2"}


def test_rt16_an_anchor_beyond_the_cap_opens_its_own_incident():
    view = TopologyView(load_topology_file("v1"))
    # A chain that stays inside the cap (last sag at +234m),
    # then a trip nine minutes past its end: the nearest-member
    # window reaches the standing incident, but the trip lies
    # 243 minutes from its anchor — the cap alone refuses the
    # anchor-join, so the trip founds its own incident.
    alarms = [_alarm("A1", "BRK-070", "FDR-07", "breaker_trip", T0)]
    t = T0
    for i in range(26):
        t += timedelta(minutes=9)
        alarms.append(_alarm(f"B{i}", "SEC-072", "FDR-07", "voltage_sag", t))
    alarms.append(
        _alarm("A3", "BRK-070", "FDR-07", "breaker_trip", t + timedelta(minutes=9))
    )
    result = correlate(alarms, view, incident_id_for=lambda a: f"INC-{a.alarm_id}")
    a3 = next(i for i in result.incidents if i.incident_id == "INC-A3")
    assert a3.anchor_alarm_id == "A3"
    rules = {
        m.alarm_id: m.rule_id
        for m in result.memberships
        if m.incident_id == "INC-A3"
    }
    assert rules == {"A3": "anchor"}
    # And the standing incident kept the whole chain.
    a1_members = {
        m.alarm_id for m in result.memberships if m.incident_id == "INC-A1"
    }
    assert a1_members == {a.alarm_id for a in alarms if a.alarm_id != "A3"}


def test_rt16_the_kept_v1_pack_reperforms_uncapped():
    view = TopologyView(load_topology_file("v1"))
    alarms = _ladder()
    legacy = correlate(
        alarms, view, CORRELATION_PACK_V1,
        incident_id_for=lambda a: f"INC-{a.alarm_id}",
    )
    members = {
        m.alarm_id for m in legacy.memberships if m.incident_id == "INC-A1"
    }
    last = max(a.raised_at for a in alarms if a.alarm_id in members)
    assert last > T0 + timedelta(hours=4)  # the chain, as computed then
    assert all(
        i.correlation_pack_version == "2026.10.1" for i in legacy.incidents
    )
    assert load_correlation_pack("2026.10.1") is CORRELATION_PACK_V1
    with pytest.raises(ValueError):
        load_correlation_pack("1999.01.1")


def test_rt16_the_planted_fixtures_are_untouched_by_the_cap(fed):
    members = {
        m.alarm_id for m in fed.store.list_memberships(FLOOD)
    }
    assert members == set(FLOOD_ALARM_IDS)
    flood = fed.store.get_incident(FLOOD)
    assert flood.correlation_pack_version == "2026.10.2"


# -- oversight reporting ------------------------------------------


def _worked_world(fed):
    """The planted feed, flood assessed, lone closed: a small
    record whose report figures can be checked by hand."""
    fed.severity.assess(FLOOD, T0 + timedelta(minutes=30))
    lone = fed.store.get_incident(LONE)
    fed.closure.close_incident(
        LONE,
        "op.dana",
        "operator",
        lone.created_at + timedelta(hours=2),
        "restored",
        lone.created_at + timedelta(hours=2, minutes=5),
    )
    return fed


def test_report_figures_are_hand_checkable(fed):
    _worked_world(fed)
    report = fed.oversight.generate(
        "op.dana", "operator", DAY_START, DAY_END, GENERATED_AT
    )
    volumes = report.sections["incident_volumes"]
    assert volumes["total"] == 2
    assert volumes["by_state"] == {
        "live_open": 1,
        "closed": 1,
        "superseded": 0,
    }
    assert volumes["closure_rate"] == {
        "value": 0.5,
        "numerator": 1,
        "denominator": 2,
    }
    mix = report.sections["severity_mix"]
    # The flood's head is the SEV-1 assessed above; the lone's
    # head is the SEV-3 the closure itself records when an
    # incident closes (the Round 4 closure grades its incident
    # into the record — probed: a fresh world has no assessment
    # for the lone until it closes).
    assert mix["by_band"] == {"SEV-1": 1, "SEV-3": 1}
    assert mix["sev1_rate"]["value"] == 0.5
    etr = report.sections["etr_accuracy"]
    assert etr["outcomes_recorded"] == 1
    assert etr["unestimated_count"] == 1
    assert etr["compared_rate"] == {
        "value": 0.0,
        "numerator": 0,
        "denominator": 1,
    }
    # Storm and normal accuracy are separate rows, never blended.
    assert set(etr["by_operating_mode"]) == {"normal", "storm"}
    assert fed.oversight.verify_report(report.report_id) == []
    # Aggregate only: no incident id anywhere in the artefact.
    blob = json.dumps(report.model_dump(mode="json"))
    assert FLOOD not in blob and LONE not in blob


def test_report_regeneration_noops_then_supersedes(fed):
    _worked_world(fed)
    first = fed.oversight.generate(
        "op.dana", "operator", DAY_START, DAY_END, GENERATED_AT
    )
    again = fed.oversight.generate(
        "op.dana", "operator", DAY_START, DAY_END,
        GENERATED_AT + timedelta(hours=1),
    )
    assert again.report_id == first.report_id
    assert len(fed.oversight.reports()) == 1
    # The record moves: the flood closes. The next generation
    # over the same window supersedes by link.
    flood = fed.store.get_incident(FLOOD)
    fed.closure.close_incident(
        FLOOD,
        "op.dana",
        "operator",
        flood.created_at + timedelta(hours=3),
        "restored",
        flood.created_at + timedelta(hours=3, minutes=5),
    )
    moved = fed.oversight.generate(
        "op.dana", "operator", DAY_START, DAY_END,
        GENERATED_AT + timedelta(hours=2),
    )
    assert moved.report_id != first.report_id
    assert moved.supersedes == first.report_id
    assert moved.sections["incident_volumes"]["by_state"]["closed"] == 2
    assert fed.oversight.verify_report(moved.report_id) == []
    # The superseded artefact now drifts against the moved
    # record — that drift is exactly what supersession answers —
    # and the artefact itself is untouched, byte for byte.
    drift = fed.oversight.verify_report(first.report_id)
    assert any("incident_volumes" in p for p in drift)
    assert fed.oversight.get(first.report_id).sections == first.sections


def test_report_verifier_names_a_planted_tamper(fed):
    _worked_world(fed)
    report = fed.oversight.generate(
        "op.dana", "operator", DAY_START, DAY_END, GENERATED_AT
    )
    forged_sections = json.loads(json.dumps(report.sections))
    forged_sections["severity_mix"]["by_band"]["SEV-1"] = 99
    forged = OversightReport(
        report_id="REPORT-forged00000001",
        pack_version=report.pack_version,
        generated_by="op.dana",
        role="operator",
        window_start=DAY_START,
        window_end=DAY_END,
        generated_at=GENERATED_AT,
        sections=forged_sections,
        fingerprint=fingerprint_of(
            report.pack_version, DAY_START, DAY_END, forged_sections
        ),
    )
    fed.store.add_oversight_report(forged)
    problems = fed.oversight.verify_report(forged.report_id)
    assert any("severity_mix" in p for p in problems)


def test_report_empty_window_reads_no_data(fed):
    report = fed.oversight.generate(
        "op.dana",
        "operator",
        datetime(2027, 1, 1, tzinfo=timezone.utc),
        datetime(2027, 1, 2, tzinfo=timezone.utc),
        GENERATED_AT + timedelta(days=90),
    )
    volumes = report.sections["incident_volumes"]
    assert volumes["population"] == "no_data"
    assert volumes["closure_rate"] == {
        "value": None,
        "numerator": 0,
        "denominator": 0,
    }
    assert report.sections["etr_accuracy"]["compared_rate"]["value"] is None
    assert fed.oversight.verify_report(report.report_id) == []


def test_report_window_and_role_discipline(fed):
    with pytest.raises(OversightRefusal):
        fed.oversight.generate(
            "op.dana", "operator", DAY_END, DAY_START, GENERATED_AT
        )
    with pytest.raises(OversightRefusal):
        fed.oversight.generate(
            "op.dana", "operator", DAY_START,
            GENERATED_AT + timedelta(days=1), GENERATED_AT,
        )
    with pytest.raises(OversightRefusal):
        fed.oversight.generate(
            "crew.lee", "field_crew", DAY_START, DAY_END, GENERATED_AT
        )
    with pytest.raises(OversightRefusal):
        fed.oversight.generate("", "operator", DAY_START, DAY_END, GENERATED_AT)


def test_report_generation_is_judged_under_the_access_pack(fed):
    before = fed.store.count_access_denials()
    with pytest.raises(AccessDeniedError) as excinfo:
        fed.oversight.generate(
            "qa.ives", "operator", DAY_START, DAY_END, GENERATED_AT,
            principal=AUDITOR,
        )
    assert excinfo.value.reason == "read_only_role"
    with pytest.raises(AccessDeniedError) as excinfo:
        fed.oversight.generate(
            "dp.osei", "operator", DAY_START, DAY_END, GENERATED_AT,
            principal=DISPATCHER,
        )
    assert excinfo.value.reason == "act_not_permitted"
    with pytest.raises(AccessDeniedError) as excinfo:
        fed.oversight.generate(
            "op.dana", "operator", DAY_START, DAY_END, GENERATED_AT,
            principal=Principal(id="op.reyes", role="operator"),
        )
    assert excinfo.value.reason == "actor_mismatch"
    # Every refusal is on the log before the error propagated.
    assert fed.store.count_access_denials() == before + 3
    # The operator who is who they say generates cleanly.
    report = fed.oversight.generate(
        "op.dana", "operator", DAY_START, DAY_END, GENERATED_AT,
        principal=OPERATOR,
    )
    assert fed.oversight.verify_report(report.report_id) == []


def test_report_api_surfaces_and_role_rules(fed):
    _worked_world(fed)
    api.override_services(fed)
    client = TestClient(api.app, raise_server_exceptions=False)
    created = client.post(
        "/oversight/reports",
        json={
            "generated_by": "op.dana",
            "role": "operator",
            "window_start": DAY_START.isoformat(),
            "window_end": DAY_END.isoformat(),
            "generated_at": GENERATED_AT.isoformat(),
        },
        headers=H(OPERATOR),
    )
    assert created.status_code == 200
    report_id = created.json()["report_id"]
    assert "internal oversight artefact" in created.json()["basis"]
    # The auditor reads; the crew does not see the surface.
    assert client.get(
        f"/oversight/reports/{report_id}", headers=H(AUDITOR)
    ).status_code == 200
    assert client.get(
        f"/oversight/reports/{report_id}", headers=H(CREW)
    ).status_code == 404
    assert client.get(
        "/oversight/reports", headers=H(CREW)
    ).json() == {"reports": []}
    verified = client.get(
        f"/oversight/reports/{report_id}/verify", headers=H(OPERATOR)
    )
    assert verified.json() == {"report_id": report_id, "problems": []}
    # Generation by a read-only principal is refused and recorded.
    refused = client.post(
        "/oversight/reports",
        json={
            "generated_by": "qa.ives",
            "role": "operator",
            "window_start": DAY_START.isoformat(),
            "window_end": DAY_END.isoformat(),
            "generated_at": GENERATED_AT.isoformat(),
        },
        headers=H(AUDITOR),
    )
    assert refused.status_code == 403
