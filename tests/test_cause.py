"""Round 3: the root-cause classification pack — rules over the
anchor pattern, recorded with the rule that fired, and `unknown`
as a first-class outcome that is never forced."""

from datetime import datetime, timedelta, timezone

from grid_triage.cause import CAUSE_PACK, classify_cause
from grid_triage.models import Alarm, Incident
from grid_triage.seed import T0

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
NOW = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)


def _alarm(alarm_id, device, kind, at, feeder="FDR-07"):
    return Alarm(
        alarm_id=alarm_id,
        stream_id="STREAM-TEST",
        device_id=device,
        feeder_id=feeder,
        kind=kind,
        severity="major",
        raised_at=at,
        message=f"{kind} on {device}",
    )


def _incident(anchor_id):
    return Incident(
        incident_id=f"INC-{anchor_id}",
        feeder_id="FDR-07",
        substation_id="SUB-ALDER",
        topology_version="v1",
        correlation_pack_version="2026.10.1",
        anchor_alarm_id=anchor_id,
        created_at=T0,
    )


def test_flood_classifies_vegetation(fed):
    c = fed.cause.classify(FLOOD, NOW)
    assert c.cause_class == "vegetation_contact"
    assert c.rule_id == "lockout_with_downstream_fault"
    assert c.pack_version == CAUSE_PACK["version"]
    assert c.inputs["anchor_kind"] == "breaker_trip"
    assert c.inputs["anchor_device_id"] == "BRK-070"
    assert c.inputs["member_alarm_count"] == 40
    assert "recloser_lockout" in c.inputs["member_kinds"]


def test_lone_trip_stays_unknown(fed):
    c = fed.cause.classify(LONE, NOW)
    assert c.cause_class == "unknown"
    assert c.rule_id is None


def test_classification_is_recorded_once_and_on_the_chain(fed):
    first = fed.cause.classify(FLOOD, NOW)
    second = fed.cause.classify(FLOOD, NOW + timedelta(hours=1))
    assert second.classification_id == first.classification_id
    assert fed.cause.current(FLOOD).classification_id == first.classification_id
    chain_kinds = [i.kind for i in fed.store.list_evidence(FLOOD)]
    assert chain_kinds.count("cause_classification") == 1
    assert fed.chain.verify_chain(FLOOD)


def test_lightning_rule_wins_on_surge_signature():
    anchor = _alarm("A1", "BRK-070", "breaker_trip", T0)
    surge = _alarm("A2", "SEC-072", "surge_arrester_operation", T0)
    lockout = _alarm("A3", "REC-071", "recloser_lockout", T0)
    alarms = {a.alarm_id: a for a in (anchor, surge, lockout)}
    c = classify_cause(_incident("A1"), alarms, NOW)
    # The surge signature is checked before the lockout pattern.
    assert c.cause_class == "lightning"
    assert c.rule_id == "surge_signature"


def test_equipment_failure_rule():
    anchor = _alarm("A1", "BRK-070", "breaker_trip", T0)
    thermal = _alarm("A2", "XF-0721", "transformer_temp_high", T0)
    alarms = {a.alarm_id: a for a in (anchor, thermal)}
    c = classify_cause(_incident("A1"), alarms, NOW)
    assert c.cause_class == "equipment_failure"
    assert c.rule_id == "thermal_without_lockout"


def test_thermal_with_lockout_is_not_equipment_failure():
    anchor = _alarm("A1", "BRK-070", "breaker_trip", T0)
    thermal = _alarm("A2", "XF-0721", "transformer_temp_high", T0)
    lockout = _alarm("A3", "REC-071", "recloser_lockout", T0)
    sag = _alarm("A4", "SEC-072", "voltage_sag", T0)
    alarms = {a.alarm_id: a for a in (anchor, thermal, lockout, sag)}
    c = classify_cause(_incident("A1"), alarms, NOW)
    assert c.cause_class == "vegetation_contact"


def test_unmatched_pattern_is_unknown_never_forced():
    anchor = _alarm("A1", "BRK-070", "breaker_trip", T0)
    sensor = _alarm("A2", "SEN-0722", "line_sensor_fault", T0)
    alarms = {a.alarm_id: a for a in (anchor, sensor)}
    c = classify_cause(_incident("A1"), alarms, NOW)
    assert c.cause_class == "unknown"
    assert c.rule_id is None
    assert c.cause_class in CAUSE_PACK["classes"]
