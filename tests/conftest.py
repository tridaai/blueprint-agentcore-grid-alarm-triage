import pytest

from grid_triage.config import Settings
from grid_triage.wiring import Services


@pytest.fixture()
def services(tmp_path) -> Services:
    return Services(
        Settings(
            sqlite_path=str(tmp_path / "grid.db"),
            object_store_dir=str(tmp_path / "objects"),
        )
    )


@pytest.fixture()
def fed(services) -> Services:
    services.intake.ingest_feed(services.feed.fetch_alarms())
    services.correlator.correlate_new(services.topology)
    return services
