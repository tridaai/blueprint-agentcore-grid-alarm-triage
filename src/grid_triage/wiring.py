"""Wiring: build the service graph from settings. One place decides
which store/object-store/model implementations sit behind the ports,
so tests, the API, the Runtime entrypoint and the demo all run the
same graph with different settings. The configured topology version
is loaded into the store's relational index at build time.
"""

from __future__ import annotations

from datetime import datetime, timezone

from grid_triage.agent import IncidentAssembler, model_for_profile
from grid_triage.config import Settings
from grid_triage.evidence import EvidenceChain
from grid_triage.object_store import LocalObjectStore
from grid_triage.seed import ReferenceFeedAdapter
from grid_triage.services import CorrelationService, IngestService
from grid_triage.severity import SeverityService
from grid_triage.store import SqlStore
from grid_triage.topology import TopologyView, load_topology_file


class Services:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.objects = LocalObjectStore(settings.object_store_dir)
        if settings.database_url:
            import psycopg

            url = settings.database_url
            self.store = SqlStore("postgres", pg_conn=lambda: psycopg.connect(url))
        else:
            self.store = SqlStore("sqlite", sqlite_path=settings.sqlite_path)
        payload = load_topology_file(settings.topology_version)
        self.store.load_topology(payload, datetime.now(timezone.utc))
        self.topology = TopologyView(payload)
        self.chain = EvidenceChain(self.store, self.objects)
        self.intake = IngestService(self.store)
        self.correlator = CorrelationService(self.store, self.chain)
        self.feed = ReferenceFeedAdapter()
        self.assembler = IncidentAssembler(
            self.store, self.chain, model_for_profile(settings.model_profile)
        )
        self.severity = SeverityService(self.store, self.chain)


def build_services(settings: Settings | None = None) -> Services:
    return Services(settings or Settings.from_env())
