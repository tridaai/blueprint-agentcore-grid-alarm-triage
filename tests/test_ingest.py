"""Ingest is append-only (Law 7): nothing is dropped, overwritten
or silently deduplicated."""

import pytest

from grid_triage.seed import FLOOD_ALARM_IDS, NUISANCE_ALARM_IDS, reference_feed
from grid_triage.services import DuplicateAlarm


def test_ingest_stores_every_alarm(services):
    alarms = reference_feed()
    count = services.intake.ingest(alarms)
    assert count == len(alarms) == 49
    assert [a.alarm_id for a in services.store.list_alarms()] == [
        a.alarm_id for a in alarms
    ]


def test_redelivery_of_an_alarm_id_is_refused(services):
    alarm = reference_feed()[0]
    services.intake.ingest([alarm])
    with pytest.raises(DuplicateAlarm):
        services.intake.ingest([alarm])
    assert len(services.store.list_alarms()) == 1


def test_uncorrelated_alarms_stay_visible(fed):
    uncorrelated = fed.store.list_uncorrelated_alarms()
    assert {a.alarm_id for a in uncorrelated} == set(NUISANCE_ALARM_IDS)
    # ...and every ingested alarm is accounted for: member or visible.
    members = set()
    for incident in fed.store.list_incidents():
        members |= {m.alarm_id for m in fed.store.list_memberships(incident.incident_id)}
    assert members | {a.alarm_id for a in uncorrelated} == {
        a.alarm_id for a in fed.store.list_alarms()
    }
    assert len(FLOOD_ALARM_IDS) == 40
