"""Round 3: the memory port — closed incidents as insert-only
records, and deterministic retrieval of priors under the stated
weights. The planted truth is hand-computed in seed.py."""

from datetime import datetime, timedelta, timezone

import pytest

from grid_triage.memory import LocalMemoryDouble
from grid_triage.models import Alarm
from grid_triage.seed import history_records
from grid_triage.store import MemoryImmutableError, Store

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
LONE_T0 = datetime(2026, 10, 14, 9, 41, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def test_history_loads_and_is_idempotent(fed):
    loaded = fed.memory.load_history()
    assert len(loaded) == 3
    assert {r.incident_id for r in loaded} == {
        "INC-HIST-0042",
        "INC-HIST-0057",
        "INC-HIST-0063",
    }
    again = fed.memory.load_history()
    assert len(fed.memory_port.list_records()) == 3
    assert [r.record_id for r in again] == [r.record_id for r in loaded]


def test_stored_records_equal_the_seed_facts(fed):
    fed.memory.load_history()
    for record in history_records():
        assert fed.store.get_memory_record(record.record_id) == record


def test_flood_priors_exact_order_and_scores(fed):
    fed.memory.load_history()
    hits = fed.memory.retrieve_for_incident(FLOOD, NOW)
    assert [h.record.incident_id for h in hits] == ["INC-HIST-0042", "INC-HIST-0057"]
    assert [h.score for h in hits] == [10, 7]
    assert hits[0].reasons["same_cause_class"] == 4
    assert hits[0].reasons["same_feeder"] == 3
    assert hits[1].reasons["same_anchor_device"] == 2
    # The Feeder 12 record has no structural basis and is excluded.
    assert all(h.record.feeder_id == "FDR-07" for h in hits)


def test_lone_trip_has_no_priors(fed):
    fed.memory.load_history()
    assert fed.memory.retrieve_for_incident(LONE, NOW) == []


def test_records_are_immutable(fed):
    record = fed.memory.load_history()[0]
    forged = record.model_copy(update={"resolution_summary": "A rewritten past."})
    with pytest.raises(MemoryImmutableError):
        fed.memory_port.put_record(forged)
    # Identical re-insert is a no-op, not an edit.
    assert fed.memory_port.put_record(record) == record
    assert fed.store.get_memory_record(record.record_id) == record


def test_port_has_no_update_or_delete():
    for cls in (Store, LocalMemoryDouble):
        names = [n for n in dir(cls) if "record" in n or "memory" in n]
        assert not any(
            n.startswith(("update", "delete", "remove")) for n in names
        ), f"{cls.__name__} exposes a memory mutation: {names}"


def test_record_incident_round_trip(fed):
    """Close the lone trip into memory from its stored facts, then
    a later Feeder 3 incident retrieves it as its only prior."""
    fed.memory.load_history()
    record = fed.memory.record_incident(
        LONE,
        "Breaker BRK-030 inspected; no damage found, feeder re-energised.",
        LONE_T0 + timedelta(hours=2, minutes=19),
    )
    assert record.feeder_id == "FDR-03"
    assert record.cause_class == "unknown"  # the lone pattern classifies unknown
    assert record.severity_band == "SEV-3"
    assert record.severity_score == 32  # 8 customers + 20 for the 911 load + 4 duration
    assert record.device_ids == ["BRK-030"]
    assert record.member_alarm_count == 1

    new_alarm = Alarm(
        alarm_id="ALM-NEW-001",
        stream_id="STREAM-SCADA-NORTH",
        device_id="BRK-030",
        feeder_id="FDR-03",
        kind="breaker_trip",
        severity="critical",
        raised_at=datetime(2026, 10, 20, 8, 15, tzinfo=timezone.utc),
        message="Feeder 3 breaker tripped on protection",
    )
    fed.intake.ingest([new_alarm])
    fed.correlator.correlate_new(fed.topology)
    hits = fed.memory.retrieve_for_incident(
        "INC-ALM-NEW-001", datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
    )
    assert [h.record.incident_id for h in hits] == [LONE]
    # class 4 + feeder 3 + anchor device 2 + device overlap 1 +
    # shared terms {unknown, brk-030} 2 = 12.
    assert hits[0].score == 12


def test_an_incident_is_not_its_own_prior(fed):
    fed.memory.load_history()
    fed.memory.record_incident(
        LONE, "Stood down after inspection.", LONE_T0 + timedelta(hours=1)
    )
    assert fed.memory.retrieve_for_incident(LONE, NOW) == []
