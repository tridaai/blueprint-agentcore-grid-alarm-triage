"""The SQL store: reopen persistence and idempotent migrations."""

from grid_triage.config import Settings
from grid_triage.store import SqlStore
from grid_triage.wiring import Services


def test_reopen_preserves_alarms_and_incidents(tmp_path):
    settings = Settings(
        sqlite_path=str(tmp_path / "grid.db"),
        object_store_dir=str(tmp_path / "objects"),
    )
    svc = Services(settings)
    svc.intake.ingest_feed(svc.feed.fetch_alarms())
    svc.correlator.correlate_new(svc.topology)

    reopened = SqlStore("sqlite", sqlite_path=settings.sqlite_path)
    assert len(reopened.list_alarms()) == 49
    assert len(reopened.list_incidents()) == 2
    assert reopened.list_topology_versions() == ["v1"]


def test_migrations_are_idempotent(tmp_path):
    path = str(tmp_path / "grid.db")
    SqlStore("sqlite", sqlite_path=path)
    store = SqlStore("sqlite", sqlite_path=path)  # second open applies nothing new
    assert store.list_alarms() == []
