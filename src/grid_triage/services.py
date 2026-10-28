"""Ingest and correlation services.

Ingest is append-only (Law 7): alarms land in the ingest log with
a sequence number; a re-delivered alarm id is refused whole rather
than silently overwriting history. Correlation reads every
uncorrelated alarm, runs the pack, and persists incidents +
memberships + one evidence item per member alarm (the alarm's own
payload, hash-chained per incident).
"""

from __future__ import annotations

from datetime import datetime, timezone

from grid_triage.correlation import correlate
from grid_triage.evidence import EvidenceChain
from grid_triage.models import Alarm, Incident
from grid_triage.storm import STORM_PACK
from grid_triage.topology import TopologyView


class DuplicateAlarm(RuntimeError):
    pass


class IngestService:
    def __init__(self, store):
        self.store = store

    def ingest(self, alarms: list[Alarm]) -> int:
        count = 0
        for alarm in alarms:
            if self.store.get_alarm(alarm.alarm_id) is not None:
                raise DuplicateAlarm(f"alarm already ingested: {alarm.alarm_id}")
            self.store.add_alarm(alarm)
            count += 1
        return count

    def ingest_feed(self, alarms: list[Alarm]) -> int:
        return self.ingest(alarms)


def incident_id_for(anchor: Alarm) -> str:
    return f"INC-{anchor.alarm_id}"


class CorrelationService:
    def __init__(self, store, chain: EvidenceChain, storm=None):
        self.store = store
        self.chain = chain
        # Round 8: the storm state fold. When wired, anchors are
        # stamped with the mode in force at their own time and
        # storm incidents correlate under the storm pack's
        # widened rules; without it every incident is normal.
        self.storm = storm

    def correlate_new(self, topology: TopologyView) -> list[Incident]:
        """Correlate all currently uncorrelated alarms against the
        pinned topology version. Alarms already in an incident are
        never re-grouped in R1 (re-correlation under new evidence
        is Round 7)."""
        pending = self.store.list_uncorrelated_alarms()
        if not pending:
            return []
        result = correlate(
            pending,
            topology,
            incident_id_for=incident_id_for,
            storm_lookup=self.storm.lookup if self.storm is not None else None,
            storm_pack=STORM_PACK if self.storm is not None else None,
        )
        by_id = {a.alarm_id: a for a in pending}
        created: list[Incident] = []
        for incident in result.incidents:
            self.store.add_incident(incident)
            created.append(incident)
        for membership in result.memberships:
            self.store.add_membership(membership)
        for incident in result.incidents:
            members = [m for m in result.memberships if m.incident_id == incident.incident_id]
            for membership in sorted(members, key=lambda m: by_id[m.alarm_id].raised_at):
                alarm = by_id[membership.alarm_id]
                self.chain.append(
                    incident.incident_id,
                    "alarm",
                    alarm.model_dump(mode="json"),
                    recorded_on=datetime.now(timezone.utc),
                    item_id=f"EV-{alarm.alarm_id}",
                )
        return created
