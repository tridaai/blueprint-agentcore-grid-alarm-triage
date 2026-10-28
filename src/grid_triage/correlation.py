"""The correlation pack — Law 2: correlation is rules, not vibes.

Grouping is a pure, deterministic function of the alarms, the
pinned topology and this versioned pack. Every alarm that joins an
incident does so under a named rule, and the membership record
carries that rule id:

- ``anchor`` — a protection/fault-class alarm opens an incident.
- ``anchor_joins_open_incident`` — a later anchor on the same feeder
  inside the window of an open incident joins it instead of opening
  a competing incident (one flood, one incident).
- ``hierarchy_window`` — a non-anchor alarm whose device is the
  anchor's device or in its ancestor/descendant line, inside the
  window, joins.
- ``feeder_window`` — any other non-excluded alarm on the same
  feeder inside the window joins.

Excluded kinds (comms/telemetry noise) never anchor and never join
in this pack version: they stay visibly uncorrelated with that
reason — correlation suppresses nothing (Law 7).
"""

from __future__ import annotations

from datetime import timedelta

from grid_triage.models import Alarm, Incident, IncidentMembership
from grid_triage.topology import TopologyView

CORRELATION_PACK = {
    "version": "2026.10.1",
    "window_seconds": 600,
    "anchor_kinds": ["breaker_trip", "relay_protection", "recloser_lockout"],
    "excluded_kinds": [
        "comms_poll_failure",
        "tap_step_counter",
        "meter_ping_failure",
    ],
}

RULE_ANCHOR = "anchor"
RULE_ANCHOR_JOINS = "anchor_joins_open_incident"
RULE_HIERARCHY = "hierarchy_window"
RULE_FEEDER_WINDOW = "feeder_window"


class CorrelationResult:
    def __init__(self):
        self.incidents: list[Incident] = []
        self.memberships: list[IncidentMembership] = []
        # alarm_id -> reason it stayed uncorrelated (for alarms the
        # pack saw but did not group): excluded kind, or no anchor.
        self.uncorrelated_reasons: dict[str, str] = {}


def correlate(
    alarms: list[Alarm],
    topology: TopologyView,
    pack: dict = CORRELATION_PACK,
    incident_id_for=None,
) -> CorrelationResult:
    """Group ``alarms`` into incidents. Pure: no store access.

    ``incident_id_for(anchor_alarm) -> str`` names new incidents.
    """
    window = timedelta(seconds=pack["window_seconds"])
    anchors = set(pack["anchor_kinds"])
    excluded = set(pack["excluded_kinds"])
    result = CorrelationResult()

    ordered = sorted(alarms, key=lambda a: (a.raised_at, a.alarm_id))
    # Working state per incident: member alarms for window checks.
    members: dict[str, list[Alarm]] = {}
    anchor_of: dict[str, Alarm] = {}

    def nearest_gap(incident_id: str, alarm: Alarm) -> timedelta:
        return min(
            abs((alarm.raised_at - m.raised_at)) for m in members[incident_id]
        )

    def candidate_incidents(alarm: Alarm) -> list[str]:
        out = []
        for incident in result.incidents:
            if incident.feeder_id != alarm.feeder_id:
                continue
            if nearest_gap(incident.incident_id, alarm) <= window:
                out.append(incident.incident_id)
        return out

    def join(incident_id: str, alarm: Alarm, rule_id: str) -> None:
        members[incident_id].append(alarm)
        result.memberships.append(
            IncidentMembership(
                incident_id=incident_id, alarm_id=alarm.alarm_id, rule_id=rule_id
            )
        )

    # Pass 1: anchors open or join incidents.
    for alarm in ordered:
        if alarm.kind in excluded:
            result.uncorrelated_reasons[alarm.alarm_id] = "excluded_kind"
            continue
        if alarm.kind not in anchors:
            continue
        candidates = candidate_incidents(alarm)
        if candidates:
            join(candidates[0], alarm, RULE_ANCHOR_JOINS)
            continue
        incident_id = incident_id_for(alarm)
        incident = Incident(
            incident_id=incident_id,
            feeder_id=alarm.feeder_id,
            substation_id=topology.substation_of_feeder(alarm.feeder_id) or "",
            topology_version=topology.version,
            correlation_pack_version=pack["version"],
            anchor_alarm_id=alarm.alarm_id,
            created_at=alarm.raised_at,
        )
        result.incidents.append(incident)
        members[incident_id] = []
        anchor_of[incident_id] = alarm
        join(incident_id, alarm, RULE_ANCHOR)

    # Pass 2: remaining non-anchor, non-excluded alarms join by
    # window, preferring hierarchy-related anchors.
    for alarm in ordered:
        if alarm.kind in excluded or alarm.kind in anchors:
            continue
        candidates = candidate_incidents(alarm)
        if not candidates:
            result.uncorrelated_reasons[alarm.alarm_id] = "no_anchor_in_window"
            continue
        chosen = candidates[0]
        rule = RULE_FEEDER_WINDOW
        for incident_id in candidates:
            if topology.related(anchor_of[incident_id].device_id, alarm.device_id):
                chosen = incident_id
                rule = RULE_HIERARCHY
                break
        join(chosen, alarm, rule)

    return result
