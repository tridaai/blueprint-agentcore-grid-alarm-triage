"""Round 8 — storm mode: the declared state (Law 8).

Storm mode exists only as named operator acts. These tests
grade the declaration lifecycle and the state fold: no act, no
storm — alarm volume alone never flips the state; the state at
any moment names the act that put it there; an extension widens
the future and never the past; and standing down relabels
nothing that was stamped under the window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from grid_triage.correlation import CORRELATION_PACK
from grid_triage.models import Alarm
from grid_triage.severity import SEVERITY_PACK
from grid_triage.storm import STORM_PACK, StormRefusal

T_DECLARE = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)


def _declare(services, **overrides):
    kwargs = {
        "declared_by": "op.dana",
        "role": "operator",
        "reason": "Storm cell crossing the Alder service area",
        "declared_at": T_DECLARE,
        "feeders": ["FDR-07"],
    }
    kwargs.update(overrides)
    return services.storm.declare(**kwargs)


# -- the pack states its deltas honestly -------------------------------


def test_storm_pack_normal_values_match_the_live_packs():
    # The pack's side-by-side table must describe the system as
    # built: the stated normal window is the correlation pack's
    # window, and the stated normal severity parameters are the
    # severity pack's.
    assert (
        STORM_PACK["correlation"]["window_seconds"]["normal"]
        == CORRELATION_PACK["window_seconds"]
    )
    for key, pair in STORM_PACK["severity"].items():
        assert pair["normal"] == SEVERITY_PACK[key], key
        assert pair["storm"] != pair["normal"], key


def test_storm_pack_names_every_change_it_makes():
    # The correlation change is the window and the joinable
    # kinds; severity changes exactly four parameters; the ETR
    # change is one stated factor. Nothing else is in the pack,
    # so nothing else may change.
    assert set(STORM_PACK["correlation"]) == {"window_seconds", "joinable_in_storm"}
    assert set(STORM_PACK["severity"]) == {
        "minutes_per_duration_point",
        "duration_points_cap",
        "alarms_per_breadth_point",
        "breadth_points_cap",
    }
    assert STORM_PACK["etr"]["base_repair_factor"] == {
        "numerator": 6,
        "denominator": 5,
    }


# -- the lifecycle -------------------------------------------------------


def test_declare_records_the_act_with_its_scope_and_reason(services):
    declaration = _declare(services, region="Alder north")
    assert declaration.kind == "declare"
    assert declaration.pack_version == STORM_PACK["version"]
    assert declaration.declared_by == "op.dana"
    assert declaration.feeders == ["FDR-07"]
    assert declaration.region == "Alder north"
    assert declaration.opens_declaration_id is None
    assert declaration.supersedes is None
    assert services.storm.declarations() == [declaration]


def test_declare_refuses_an_unnamed_operator_a_wrong_role_and_no_reason(services):
    with pytest.raises(StormRefusal) as exc:
        _declare(services, declared_by=" ")
    assert any("names the operator" in p for p in exc.value.problems)
    with pytest.raises(StormRefusal) as exc:
        _declare(services, role="field_crew")
    assert any("may not declaration storm mode" in p for p in exc.value.problems)
    with pytest.raises(StormRefusal) as exc:
        _declare(services, reason="")
    assert any("states its reason" in p for p in exc.value.problems)
    assert services.storm.declarations() == []


def test_a_second_overlapping_declaration_is_refused(services):
    first = _declare(services)
    with pytest.raises(StormRefusal) as exc:
        _declare(services, declared_at=T_DECLARE + timedelta(hours=1))
    assert any(first.declaration_id in p for p in exc.value.problems)
    # A territory-wide declaration overlaps every open scope.
    with pytest.raises(StormRefusal):
        _declare(services, feeders=[], declared_at=T_DECLARE + timedelta(hours=1))
    # A disjoint feeder scope is a different window and stands.
    other = _declare(
        services, feeders=["FDR-12"], declared_at=T_DECLARE + timedelta(hours=1)
    )
    assert other.declaration_id != first.declaration_id


def test_state_at_names_the_act_that_put_it_there(services):
    declaration = _declare(services)
    before = services.storm.state_at("FDR-07", T_DECLARE - timedelta(minutes=1))
    assert before == {
        "mode": "normal",
        "feeder_id": "FDR-07",
        "at": (T_DECLARE - timedelta(minutes=1)).isoformat(),
    }
    during = services.storm.state_at("FDR-07", T_DECLARE + timedelta(minutes=30))
    assert during["mode"] == "storm"
    assert during["declaration_id"] == declaration.declaration_id
    assert during["head_act_id"] == declaration.declaration_id
    assert during["declared_by"] == "op.dana"
    assert during["feeders"] == ["FDR-07"]
    outside = services.storm.state_at("FDR-03", T_DECLARE + timedelta(minutes=30))
    assert outside["mode"] == "normal"


def test_extend_widens_the_future_and_never_the_past(services):
    declaration = _declare(services)
    extension = services.storm.extend(
        declaration.declaration_id,
        "op.dana",
        "operator",
        "Cell widening — Feeder 3 added to the storm scope",
        T_DECLARE + timedelta(hours=2),
        feeders=["FDR-07", "FDR-03"],
    )
    assert extension.kind == "extend"
    assert extension.opens_declaration_id == declaration.declaration_id
    assert extension.supersedes == declaration.declaration_id
    # Before the extension, Feeder 3 was out of scope; after it,
    # it is in — and the state read names the extension as head.
    early = services.storm.state_at("FDR-03", T_DECLARE + timedelta(hours=1))
    assert early["mode"] == "normal"
    late = services.storm.state_at("FDR-03", T_DECLARE + timedelta(hours=3))
    assert late["mode"] == "storm"
    assert late["head_act_id"] == extension.declaration_id
    assert late["declaration_id"] == declaration.declaration_id
    assert late["feeders"] == ["FDR-07", "FDR-03"]


def test_extend_keeps_the_scope_it_does_not_restate(services):
    declaration = _declare(services, region="Alder north")
    extension = services.storm.extend(
        declaration.declaration_id,
        "op.rivera",
        "duty_manager",
        "Reason restated for the evening briefing",
        T_DECLARE + timedelta(hours=1),
    )
    assert extension.feeders == ["FDR-07"]
    assert extension.region == "Alder north"


def test_extend_and_stand_down_refusals(services):
    declaration = _declare(services)
    # Naming a non-declare act, or nothing at all.
    with pytest.raises(StormRefusal):
        services.storm.extend(
            "STORM-missing", "op.dana", "operator", "widening",
            T_DECLARE + timedelta(hours=1),
        )
    extension = services.storm.extend(
        declaration.declaration_id, "op.dana", "operator", "widening",
        T_DECLARE + timedelta(hours=1), feeders=["FDR-07", "FDR-03"],
    )
    with pytest.raises(StormRefusal) as exc:
        services.storm.extend(
            extension.declaration_id, "op.dana", "operator", "wrong target",
            T_DECLARE + timedelta(hours=2),
        )
    assert any("names the declaration" in p for p in exc.value.problems)
    # Out-of-order acts are refused: the chain is monotone.
    with pytest.raises(StormRefusal) as exc:
        services.storm.extend(
            declaration.declaration_id, "op.dana", "operator", "too early",
            T_DECLARE + timedelta(minutes=30),
        )
    assert any("act order" in p for p in exc.value.problems)


def test_stand_down_closes_the_window_and_the_fold_shows_it(services):
    declaration = _declare(services)
    stand_down = services.storm.stand_down(
        declaration.declaration_id,
        "op.dana",
        "operator",
        "Cell has passed; damage assessment complete",
        T_DECLARE + timedelta(hours=11),
    )
    assert stand_down.kind == "stand_down"
    assert stand_down.supersedes == declaration.declaration_id
    during = services.storm.state_at("FDR-07", T_DECLARE + timedelta(hours=5))
    assert during["mode"] == "storm"
    after = services.storm.state_at("FDR-07", T_DECLARE + timedelta(hours=12))
    assert after["mode"] == "normal"
    # A closed window is not extended and is not stood down twice.
    with pytest.raises(StormRefusal):
        services.storm.stand_down(
            declaration.declaration_id, "op.dana", "operator", "again",
            T_DECLARE + timedelta(hours=12),
        )
    with pytest.raises(StormRefusal):
        services.storm.extend(
            declaration.declaration_id, "op.dana", "operator", "too late",
            T_DECLARE + timedelta(hours=12),
        )
    # A fresh window may open over the same feeders afterwards.
    second = _declare(services, declared_at=T_DECLARE + timedelta(days=1))
    assert second.declaration_id != declaration.declaration_id


def test_alarm_volume_alone_never_declares_storm(fed):
    # The whole planted feed — a 40-alarm flood among it — is
    # correlated with no declaration anywhere on record: every
    # incident is stamped normal and every state read is normal.
    assert fed.storm.declarations() == []
    for incident in fed.store.list_incidents():
        assert incident.operating_mode == "normal"
        assert incident.storm_declaration_id is None
    state = fed.storm.state_at("FDR-07", datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc))
    assert state["mode"] == "normal"


def test_stamp_for_reads_the_fold(services):
    declaration = _declare(services)
    stamp = services.storm.stamp_for("FDR-07", T_DECLARE + timedelta(hours=1))
    assert stamp == {
        "operating_mode": "storm",
        "storm_pack_version": STORM_PACK["version"],
        "storm_declaration_id": declaration.declaration_id,
    }
    assert services.storm.stamp_for("FDR-12", T_DECLARE + timedelta(hours=1)) == {
        "operating_mode": "normal",
        "storm_pack_version": None,
        "storm_declaration_id": None,
    }
    assert services.storm.lookup("FDR-07", T_DECLARE + timedelta(hours=1)) == {
        "declaration_id": declaration.declaration_id,
        "pack_version": STORM_PACK["version"],
    }
    assert services.storm.lookup("FDR-07", T_DECLARE - timedelta(hours=1)) is None


def test_window_acts_reads_the_whole_chain(services):
    declaration = _declare(services)
    extension = services.storm.extend(
        declaration.declaration_id, "op.dana", "operator", "widening",
        T_DECLARE + timedelta(hours=2), feeders=["FDR-07", "FDR-03"],
    )
    stand_down = services.storm.stand_down(
        declaration.declaration_id, "op.dana", "operator", "passed",
        T_DECLARE + timedelta(hours=9),
    )
    acts = services.storm.window_acts(declaration.declaration_id)
    assert [a.declaration_id for a in acts] == [
        declaration.declaration_id,
        extension.declaration_id,
        stand_down.declaration_id,
    ]
    assert [a.kind for a in acts] == ["declare", "extend", "stand_down"]


def _burst_alarm(alarm_id: str, device: str, kind: str, at: datetime) -> Alarm:
    return Alarm(
        alarm_id=alarm_id,
        stream_id="STREAM-SCADA-NORTH",
        device_id=device,
        feeder_id="FDR-07",
        kind=kind,
        severity="major",
        raised_at=at,
        message=f"{kind} reported by {device}",
    )


# -- correlation, severity and ETR under the declared state --------------

BURST_T0 = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)


def _burst() -> list[Alarm]:
    """A storm burst on Feeder 7, built so the pack's deltas are
    what decide its shape: the 02:12 sag reaches the incident
    only through the widened storm window, and the meter ping
    joins only because the storm pack names it joinable."""
    return [
        _burst_alarm("ALM-STM-001", "BRK-070", "breaker_trip", BURST_T0),
        _burst_alarm(
            "ALM-STM-002", "BRK-070", "relay_protection",
            BURST_T0 + timedelta(seconds=20),
        ),
        _burst_alarm(
            "ALM-STM-003", "REC-071", "recloser_lockout",
            BURST_T0 + timedelta(seconds=40),
        ),
        _burst_alarm(
            "ALM-STM-004", "SEN-0722", "meter_ping_failure",
            BURST_T0 + timedelta(minutes=5),
        ),
        _burst_alarm(
            "ALM-STM-005", "SEC-072", "voltage_sag",
            BURST_T0 + timedelta(minutes=12),
        ),
        _burst_alarm(
            "ALM-STM-006", "SEC-073", "phase_loss",
            BURST_T0 + timedelta(minutes=20),
        ),
    ]


def _correlate_burst(services, declare: bool):
    declaration = _declare(services) if declare else None
    services.intake.ingest(_burst())
    created = services.correlator.correlate_new(services.topology)
    assert len(created) == 1
    return declaration, created[0]


def test_storm_correlation_widens_the_window_and_stamps_the_incident(services):
    declaration, incident = _correlate_burst(services, declare=True)
    members = {
        m.alarm_id for m in services.store.list_memberships(incident.incident_id)
    }
    assert members == {a.alarm_id for a in _burst()}
    assert incident.operating_mode == "storm"
    assert incident.storm_declaration_id == declaration.declaration_id
    assert incident.storm_pack_version == STORM_PACK["version"]
    # The stored row carries the stamp, not just the return value.
    stored = services.store.get_incident(incident.incident_id)
    assert stored.operating_mode == "storm"
    assert stored.storm_declaration_id == declaration.declaration_id


def test_the_same_burst_groups_narrowly_without_a_declaration(services):
    _, incident = _correlate_burst(services, declare=False)
    members = {
        m.alarm_id for m in services.store.list_memberships(incident.incident_id)
    }
    # Only the three protection alarms group: the sag is outside
    # the normal window, the ping is an excluded kind, and the
    # phase loss has no anchor in window. All stay visible.
    assert members == {"ALM-STM-001", "ALM-STM-002", "ALM-STM-003"}
    assert incident.operating_mode == "normal"
    assert incident.storm_declaration_id is None
    uncorrelated = {
        a.alarm_id for a in services.store.list_uncorrelated_alarms()
    }
    assert {"ALM-STM-004", "ALM-STM-005", "ALM-STM-006"} <= uncorrelated


def test_an_incident_raised_before_the_declaration_is_not_stamped(services):
    # The burst predates the declaration: correlation runs after
    # the act is recorded, but the stamp is judged at the
    # anchor's own time — the mode the incident arose under.
    services.intake.ingest(_burst())
    _declare(services, declared_at=BURST_T0 + timedelta(hours=1))
    created = services.correlator.correlate_new(services.topology)
    assert len(created) == 1
    assert created[0].operating_mode == "normal"
    members = {
        m.alarm_id for m in services.store.list_memberships(created[0].incident_id)
    }
    assert members == {"ALM-STM-001", "ALM-STM-002", "ALM-STM-003"}


def test_storm_severity_applies_the_packs_stated_values(services):
    from grid_triage.severity import compute_assessment

    _, incident = _correlate_burst(services, declare=True)
    as_of = BURST_T0 + timedelta(minutes=90)
    assessment = services.severity.assess(incident.incident_id, as_of)
    # Hand-computed under the storm pack: customers 1440 -> 50
    # (capped), hospital 30, duration 90 min -> 90//60 = 1,
    # breadth 6 alarms -> 6//5 = 1. Score 82, SEV-1.
    assert assessment.inputs["points"] == {
        "customers": 50,
        "critical_loads": 30,
        "duration": 1,
        "breadth": 1,
    }
    assert assessment.score == 82
    assert assessment.band == "SEV-1"
    assert assessment.inputs["operating_mode"] == "storm"
    assert assessment.inputs["storm_declaration_id"] == incident.storm_declaration_id
    assert assessment.inputs["storm_adjustments"] == {
        key: {"normal": pair["normal"], "storm": pair["storm"]}
        for key, pair in STORM_PACK["severity"].items()
    }
    # The same incident under the unadjusted pack computes
    # differently in exactly the two named terms — nothing else
    # moved.
    alarms_by_id = {
        m.alarm_id: services.store.get_alarm(m.alarm_id)
        for m in services.store.list_memberships(incident.incident_id)
    }
    normal = compute_assessment(
        incident, alarms_by_id, services.topology, as_of
    )
    assert normal.inputs["points"]["duration"] == 3  # 90//30, cap 10
    assert normal.inputs["points"]["breadth"] == 0  # 6//10, cap 5
    assert normal.score == 83
    assert normal.inputs["operating_mode"] == "normal"


def test_storm_etr_carries_the_stated_adjustment_as_its_own_line(services):
    _, incident = _correlate_burst(services, declare=True)
    estimate = services.etr.estimate(incident.incident_id, BURST_T0 + timedelta(minutes=30))
    assert estimate.status == "computed"
    assert estimate.operating_mode == "storm"
    assert estimate.storm_declaration_id == incident.storm_declaration_id
    assert estimate.storm_pack_version == STORM_PACK["version"]
    components = {c["name"]: c["minutes"] for c in estimate.components}
    # vegetation_contact | feeder_breaker = 150 in the rate pack;
    # the storm factor 6/5 makes 180, and the 30 added minutes
    # are their own line.
    assert components["base_repair"] == 150
    assert components["storm_conditions"] == 30
    assert estimate.total_minutes == (
        components["crew_wait"]
        + components["travel_access"]
        + components["base_repair"]
        + components["storm_conditions"]
    )
    storm_line = next(
        c for c in estimate.components if c["name"] == "storm_conditions"
    )
    assert storm_line["detail"]["storm_base_repair_minutes"] == 180
    assert storm_line["detail"]["factor"] == {"numerator": 6, "denominator": 5}


def test_normal_etr_has_no_storm_line(services):
    _, incident = _correlate_burst(services, declare=False)
    estimate = services.etr.estimate(incident.incident_id, BURST_T0 + timedelta(minutes=30))
    assert estimate.operating_mode == "normal"
    assert estimate.storm_declaration_id is None
    names = [c["name"] for c in estimate.components]
    assert "storm_conditions" not in names


def test_outcomes_record_the_mode_and_the_ledger_keeps_modes_separate(services):
    declaration, storm_incident = _correlate_burst(services, declare=True)
    # A normal incident alongside: a lone Feeder 3 trip, outside
    # the declared scope.
    services.intake.ingest(
        [
            Alarm(
                alarm_id="ALM-STM-LONE", stream_id="STREAM-SCADA-NORTH",
                device_id="BRK-030", feeder_id="FDR-03", kind="breaker_trip",
                severity="critical", raised_at=BURST_T0 + timedelta(hours=3),
                message="Feeder 3 breaker tripped on protection",
            )
        ]
    )
    created = services.correlator.correlate_new(services.topology)
    assert len(created) == 1
    normal_incident = created[0]
    assert normal_incident.operating_mode == "normal"

    estimate = services.etr.estimate(
        storm_incident.incident_id, BURST_T0 + timedelta(minutes=30)
    )
    assert estimate.status == "computed"
    _, storm_outcome, _ = services.closure.close_incident(
        storm_incident.incident_id,
        "op.dana",
        "operator",
        BURST_T0 + timedelta(minutes=400),
        "Storm damage on Feeder 7 cleared and the feeder restored.",
        recorded_at=BURST_T0 + timedelta(minutes=401),
    )
    assert storm_outcome.status == "compared"
    assert storm_outcome.operating_mode == "storm"
    assert storm_outcome.storm_declaration_id == declaration.declaration_id

    _, normal_outcome, _ = services.closure.close_incident(
        normal_incident.incident_id,
        "op.dana",
        "operator",
        BURST_T0 + timedelta(hours=3, minutes=30),
        "Feeder 3 restored after inspection.",
        recorded_at=BURST_T0 + timedelta(hours=3, minutes=31),
    )
    assert normal_outcome.status == "no_computed_estimate"
    assert normal_outcome.operating_mode == "normal"
    assert normal_outcome.storm_declaration_id is None

    ledger = services.closure.accuracy_ledger()
    by_mode = {row["operating_mode"]: row for row in ledger["by_operating_mode"]}
    assert by_mode["storm"]["compared_count"] == 1
    assert by_mode["storm"]["unestimated_closures"] == 0
    assert (
        by_mode["storm"]["mean_signed_error_minutes"]
        == storm_outcome.signed_error_minutes
    )
    assert by_mode["normal"]["compared_count"] == 0
    assert by_mode["normal"]["unestimated_closures"] == 1
    # The separation is checkable: the mode rows' counts sum to
    # the overall figures exactly.
    assert (
        sum(r["compared_count"] for r in ledger["by_operating_mode"])
        == ledger["overall"]["compared_count"]
    )
    assert (
        sum(r["unestimated_closures"] for r in ledger["by_operating_mode"])
        == ledger["unestimated_closures"]
    )


def test_stand_down_does_not_relabel_what_the_window_stamped(services):
    declaration, incident = _correlate_burst(services, declare=True)
    services.storm.stand_down(
        declaration.declaration_id,
        "op.dana",
        "operator",
        "Cell has passed",
        BURST_T0 + timedelta(hours=10),
    )
    stored = services.store.get_incident(incident.incident_id)
    assert stored.operating_mode == "storm"
    assert stored.storm_declaration_id == declaration.declaration_id
    # New alarms after the stand-down correlate in normal mode.
    services.intake.ingest(
        [
            _burst_alarm(
                "ALM-STM-007", "BRK-070", "breaker_trip",
                BURST_T0 + timedelta(hours=12),
            )
        ]
    )
    created = services.correlator.correlate_new(services.topology)
    assert len(created) == 1
    assert created[0].operating_mode == "normal"
