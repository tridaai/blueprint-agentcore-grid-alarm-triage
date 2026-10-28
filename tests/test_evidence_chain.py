"""Hash-chained incident evidence (Law 7): valid after correlation,
broken by any tampering with a stored payload."""

from grid_triage.seed import FLOOD_ALARM_IDS


def test_chain_verifies_for_both_incidents(fed):
    for incident in fed.store.list_incidents():
        assert fed.chain.verify_chain(incident.incident_id)


def test_every_member_alarm_is_an_evidence_item(fed):
    items = fed.store.list_evidence("INC-ALM-FLD-001")
    assert {i.item_id for i in items} == {f"EV-{a}" for a in FLOOD_ALARM_IDS}


def test_tampered_payload_breaks_the_chain(fed, tmp_path):
    assert fed.chain.verify_chain("INC-ALM-FLD-001")
    key = fed.store.list_evidence("INC-ALM-FLD-001")[0].object_key
    path = fed.objects._path(key)
    path.write_bytes(b'{"alarm_id": "ALM-FLD-001", "kind": "edited"}')
    assert not fed.chain.verify_chain("INC-ALM-FLD-001")
