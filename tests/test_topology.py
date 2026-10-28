"""Topology is versioned data with a relational index (Law 6)."""

from grid_triage.topology import TopologyView, load_topology_file


def test_v1_loads_into_index(services):
    assert services.store.list_topology_versions() == ["v1"]
    devices = services.store.list_devices("v1")
    assert len(devices) == 13
    by_id = {d["device_id"]: d for d in devices}
    assert by_id["REC-071"]["parent_device_id"] == "BRK-070"
    assert by_id["BRK-070"]["feeder_id"] == "FDR-07"


def test_topology_view_relationships():
    view = TopologyView(load_topology_file("v1"))
    assert view.feeder_of("XF-0721") == "FDR-07"
    assert view.substation_of_feeder("FDR-07") == "SUB-ALDER"
    assert view.related("BRK-070", "XF-0721")  # ancestor/descendant
    assert view.related("SEC-072", "SEN-0722")
    assert not view.related("SEC-072", "SEC-073")  # siblings, not related
    assert {c["kind"] for c in view.critical_loads} == {
        "hospital",
        "water_treatment",
        "emergency_911",
    }


def test_topology_version_is_immutable_once_loaded(services):
    # Rebuilding services over the same store must not duplicate rows.
    services.store.load_topology(
        load_topology_file("v1"), __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
    )
    assert len(services.store.list_devices("v1")) == 13
