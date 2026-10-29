"""Correlation graded against the planted truth (Law 2)."""

from grid_triage.correlation import CORRELATION_PACK
from grid_triage.seed import FLOOD_ALARM_IDS, LONE_ALARM_IDS


def test_flood_is_exactly_one_incident_with_exact_membership(fed):
    incidents = fed.store.list_incidents()
    flood = [i for i in incidents if i.feeder_id == "FDR-07"]
    assert len(flood) == 1
    members = {m.alarm_id for m in fed.store.list_memberships(flood[0].incident_id)}
    assert members == set(FLOOD_ALARM_IDS)


def test_nuisance_stream_creates_no_incident(fed):
    assert all(i.feeder_id != "FDR-12" for i in fed.store.list_incidents())


def test_lone_trip_is_a_single_alarm_incident(fed):
    lone = [i for i in fed.store.list_incidents() if i.feeder_id == "FDR-03"]
    assert len(lone) == 1
    members = fed.store.list_memberships(lone[0].incident_id)
    assert [m.alarm_id for m in members] == LONE_ALARM_IDS
    assert members[0].rule_id == "anchor"


def test_every_membership_carries_a_rule_and_versions_are_pinned(fed):
    for incident in fed.store.list_incidents():
        assert incident.topology_version == "v1"
        # The stamp is the version of the pack the incident was
        # correlated under — the current pack (2026.10.2 since
        # Round 12's span-cap revision; the literal moved with
        # the pack, deliberately, and 2026.10.1 correlations
        # re-perform under the kept pack: tests/test_r12.py).
        assert incident.correlation_pack_version == CORRELATION_PACK["version"]
        for membership in fed.store.list_memberships(incident.incident_id):
            assert membership.rule_id in {
                "anchor",
                "anchor_joins_open_incident",
                "hierarchy_window",
                "feeder_window",
            }
    flood = next(i for i in fed.store.list_incidents() if i.feeder_id == "FDR-07")
    rules = {m.alarm_id: m.rule_id for m in fed.store.list_memberships(flood.incident_id)}
    assert rules["ALM-FLD-001"] == "anchor"
    # Later protection operations join the open incident, they do
    # not open competing ones.
    assert rules["ALM-FLD-002"] == "anchor_joins_open_incident"
    assert rules["ALM-FLD-003"] == "anchor_joins_open_incident"


def test_correlation_is_idempotent_over_stored_alarms(fed):
    before = fed.store.list_incidents()
    assert fed.correlator.correlate_new(fed.topology) == []
    assert fed.store.list_incidents() == before
