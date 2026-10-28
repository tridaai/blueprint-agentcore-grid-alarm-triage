"""Wiring: build the service graph from settings. One place decides
which store/object-store/model implementations sit behind the ports,
so tests, the API, the Runtime entrypoint and the demo all run the
same graph with different settings. The configured topology version
is loaded into the store's relational index at build time.
"""

from __future__ import annotations

from datetime import datetime, timezone

from grid_triage.agent import IncidentAssembler, model_for_profile
from grid_triage.board import BoardService
from grid_triage.cause import CauseService
from grid_triage.closure import ClosureService
from grid_triage.config import Settings
from grid_triage.etr import EtrService
from grid_triage.evidence import EvidenceChain
from grid_triage.handover import HandoverService
from grid_triage.memory import LocalMemoryDouble, MemoryService
from grid_triage.object_store import LocalObjectStore
from grid_triage.packs import EvidencePackService
from grid_triage.restoration import EventLogService, RestorationService
from grid_triage.revisions import FieldUpdateService, RevisionService, TopologyService
from grid_triage.roster import RosterView, load_roster_file
from grid_triage.seed import ReferenceFeedAdapter
from grid_triage.services import CorrelationService, IngestService
from grid_triage.severity import SeverityService
from grid_triage.storm import StormService
from grid_triage.store import SqlStore
from grid_triage.topology import TopologyView, load_topology_file
from grid_triage.work_orders import WorkOrderService


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
        # Round 8: the storm declaration acts and the state fold.
        # The service rests on the store alone, so it is built
        # before the consumers that read the fold.
        self.storm = StormService(self.store)
        self.intake = IngestService(self.store)
        self.correlator = CorrelationService(
            self.store, self.chain, storm=self.storm
        )
        self.feed = ReferenceFeedAdapter()
        self.severity = SeverityService(self.store, self.chain)
        self.cause = CauseService(self.store, self.chain)
        # The memory port: AgentCore Memory in production; the
        # local double over the store port here (Round 3).
        self.memory_port = LocalMemoryDouble(self.store)
        self.memory = MemoryService(
            self.store, self.memory_port, self.cause, self.severity
        )
        self.packs = EvidencePackService(
            self.store, self.chain, self.cause, self.severity, self.memory
        )
        # The crew roster: a versioned artefact loaded from data,
        # like the topology (Round 4).
        self.roster = RosterView(load_roster_file(settings.roster_version))
        self.etr = EtrService(self.store, self.chain, self.cause, self.roster)
        self.work_orders = WorkOrderService(
            self.store, self.chain, self.cause, self.etr, self.roster
        )
        self.closure = ClosureService(self.store, self.chain, self.memory)
        # Round 6: field updates, the topology declaration act,
        # and re-correlation under a declared version.
        self.updates = FieldUpdateService(self.store, self.chain)
        self.topology_acts = TopologyService(self.store)
        self.revisions = RevisionService(
            self.store,
            self.chain,
            self.cause,
            self.severity,
            self.etr,
            self.topology_acts,
            storm=self.storm,
        )
        # Round 7: restoration signals and confirmations, the
        # gated close, splits, post-close contradictions and the
        # incident event log. The update service's post-close
        # handler is registered here — the restoration service is
        # built after it, so the hook is set on the instance.
        self.restoration = RestorationService(
            self.store,
            self.chain,
            self.closure,
            self.work_orders,
            self.severity,
            cause=self.cause,
            etr=self.etr,
            storm=self.storm,
        )
        self.updates.post_close_handler = self.restoration.handle_post_close_alarm
        self.event_log = EventLogService(self.store, self.chain)
        # Round 9: the operations board (the one artefact-only
        # computation behind the console) and the shift handover
        # acts that freeze it. Both are read/act surfaces over
        # the same store — neither operates anything.
        self.board = BoardService(
            self.store,
            self.work_orders,
            self.etr,
            self.storm,
            self.closure,
            payload,
        )
        self.handover = HandoverService(self.store, self.board)
        self.assembler = IncidentAssembler(
            self.store,
            self.chain,
            model_for_profile(settings.model_profile),
            cause=self.cause,
            memory=self.memory,
            etr=self.etr,
        )


def build_services(settings: Settings | None = None) -> Services:
    return Services(settings or Settings.from_env())
