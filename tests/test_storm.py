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
