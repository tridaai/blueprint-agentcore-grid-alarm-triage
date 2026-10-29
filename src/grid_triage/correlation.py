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

**Storm mode (Round 8, Law 8)** is a stated variation, not a
second engine: when the caller supplies the storm fold, an
incident whose anchor arose while a storm declaration covered
its feeder is correlated with the storm pack's widened window
and stamped with the mode, the storm pack version and the
declaration id. The stamp is judged at the anchor alarm's own
time — the mode the incident arose under — never at the later
moment correlation happens to run. Kinds the storm pack names
``joinable_in_storm`` may join a storm incident (never anchor
one); in normal mode, or into a normal incident, they stay
excluded exactly as above.

**Span cap (Round 12, RT-16).** The window rule above is a
*nearest-member* rule, and left alone it chains: a ladder of
consequences, each inside the window of the last, walks an
incident's membership arbitrarily far from its anchor — the
red team chained one incident across five hours of
nine-minute-spaced sags. Pack version ``2026.10.2`` therefore
adds ``max_span_seconds``: no alarm joins an incident — as a
member or as a joining anchor — when it lies further than the
cap from the incident's **anchor** alarm. Four hours is
twenty-four normal windows and eight storm windows: an
incident is one fault event, and a consequence arriving more
than four hours after the fault that anchors it is a new
operational picture even when every step between was near the
last. What the cap refuses stays visible: a non-anchor beyond
the cap of every window-reachable incident is uncorrelated
with the named reason ``beyond_span_cap`` (never the
``no_anchor_in_window`` it did not earn), and an anchor beyond
the cap opens its own incident, as it should.

**Pack versions are the discipline, as everywhere in this
repo.** ``2026.10.1`` carries no cap key and is uncapped by
construction; it is kept verbatim below, and a correlation
re-performs under the pack version its incident pins
(``load_correlation_pack`` fails closed on an unknown
version). The planted fixtures are untouched by the cap —
the flood spans six minutes — and the eval gate grades both
halves: the ladder capped under the current pack, and the
same ladder re-performing uncapped under ``2026.10.1``.
"""

from __future__ import annotations

from datetime import timedelta

from grid_triage.models import Alarm, Incident, IncidentMembership
from grid_triage.storm import storm_joinable_kinds, storm_window_seconds
from grid_triage.topology import TopologyView

# The original pack, kept verbatim: no span-cap key, so
# correlations pinned to it re-perform uncapped, exactly as
# they were computed (Round 12, RT-16).
CORRELATION_PACK_V1 = {
    "version": "2026.10.1",
    "window_seconds": 600,
    "anchor_kinds": ["breaker_trip", "relay_protection", "recloser_lockout"],
    "excluded_kinds": [
        "comms_poll_failure",
        "tap_step_counter",
        "meter_ping_failure",
    ],
}

# The current pack. The one change from 2026.10.1 is the span
# cap (RT-16): no member lies more than four hours from the
# incident's anchor alarm.
CORRELATION_PACK = {
    "version": "2026.10.2",
    "window_seconds": 600,
    "anchor_kinds": ["breaker_trip", "relay_protection", "recloser_lockout"],
    "excluded_kinds": [
        "comms_poll_failure",
        "tap_step_counter",
        "meter_ping_failure",
    ],
    "max_span_seconds": 14400,
}

CORRELATION_PACKS = {
    pack["version"]: pack
    for pack in (CORRELATION_PACK_V1, CORRELATION_PACK)
}


def load_correlation_pack(version: str | None = None) -> dict:
    """The loader, fail-closed like every pack loader in this
    repo: an unknown version is an error, never a silent fall
    back to the current pack. ``None`` is the current pack."""
    if version is None:
        return CORRELATION_PACK
    pack = CORRELATION_PACKS.get(version)
    if pack is None:
        raise ValueError(f"unknown correlation pack version: {version!r}")
    return pack


RULE_ANCHOR = "anchor"
RULE_ANCHOR_JOINS = "anchor_joins_open_incident"
RULE_HIERARCHY = "hierarchy_window"
RULE_FEEDER_WINDOW = "feeder_window"

# The named reason an alarm stays uncorrelated when the only
# incidents its window reaches are beyond the pack's span cap
# (Round 12): there WAS an anchor in window — the cap, not the
# window, refused the join.
REASON_BEYOND_SPAN = "beyond_span_cap"


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
    storm_lookup=None,
    storm_pack: dict | None = None,
) -> CorrelationResult:
    """Group ``alarms`` into incidents. Pure: no store access.

    ``incident_id_for(anchor_alarm) -> str`` names new incidents.
    ``storm_lookup(feeder_id, at)`` — when supplied with a storm
    pack — returns the storm stamp for an anchor arising on that
    feeder at that time (``{"declaration_id", "pack_version"}``)
    or None in normal mode; it is the state fold's read, injected
    so this function stays pure.
    """
    window = timedelta(seconds=pack["window_seconds"])
    # The span cap (Round 12): a pack that carries no
    # ``max_span_seconds`` key — 2026.10.1 — is uncapped by
    # construction, so correlations pinned to it re-perform
    # exactly as computed.
    max_span = (
        timedelta(seconds=pack["max_span_seconds"])
        if pack.get("max_span_seconds") is not None
        else None
    )
    storm_window = (
        timedelta(seconds=storm_window_seconds(storm_pack))
        if storm_pack is not None
        else window
    )
    joinable = storm_joinable_kinds(storm_pack) if storm_pack is not None else set()
    anchors = set(pack["anchor_kinds"])
    excluded = set(pack["excluded_kinds"])
    result = CorrelationResult()

    ordered = sorted(alarms, key=lambda a: (a.raised_at, a.alarm_id))
    # Working state per incident: member alarms for window checks.
    members: dict[str, list[Alarm]] = {}
    anchor_of: dict[str, Alarm] = {}
    # Per-incident correlation window and storm stamp: an
    # incident's window is fixed by the mode in force at its
    # anchor's time, and a joining alarm is tested against the
    # incident's window, never its own moment's.
    windows: dict[str, timedelta] = {}
    stamps: dict[str, dict | None] = {}

    def nearest_gap(incident_id: str, alarm: Alarm) -> timedelta:
        return min(
            abs((alarm.raised_at - m.raised_at)) for m in members[incident_id]
        )

    def window_candidates(alarm: Alarm) -> list[str]:
        """The incidents the nearest-member window reaches,
        before the span cap judges them."""
        out = []
        for incident in result.incidents:
            if incident.feeder_id != alarm.feeder_id:
                continue
            if nearest_gap(incident.incident_id, alarm) <= windows[incident.incident_id]:
                out.append(incident.incident_id)
        return out

    def within_span(incident_id: str, alarm: Alarm) -> bool:
        """The span cap: a member lies no further than the cap
        from the incident's anchor alarm. The anchor's own
        distance is zero, so an incident's founding is never
        capped."""
        if max_span is None:
            return True
        return (
            abs(alarm.raised_at - anchor_of[incident_id].raised_at)
            <= max_span
        )

    def candidate_incidents(alarm: Alarm) -> list[str]:
        return [
            incident_id
            for incident_id in window_candidates(alarm)
            if within_span(incident_id, alarm)
        ]

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
        stamp = (
            storm_lookup(alarm.feeder_id, alarm.raised_at)
            if storm_lookup is not None
            else None
        )
        incident_id = incident_id_for(alarm)
        incident = Incident(
            incident_id=incident_id,
            feeder_id=alarm.feeder_id,
            substation_id=topology.substation_of_feeder(alarm.feeder_id) or "",
            topology_version=topology.version,
            correlation_pack_version=pack["version"],
            anchor_alarm_id=alarm.alarm_id,
            created_at=alarm.raised_at,
            operating_mode="storm" if stamp else "normal",
            storm_pack_version=stamp["pack_version"] if stamp else None,
            storm_declaration_id=stamp["declaration_id"] if stamp else None,
        )
        result.incidents.append(incident)
        members[incident_id] = []
        anchor_of[incident_id] = alarm
        windows[incident_id] = storm_window if stamp else window
        stamps[incident_id] = stamp
        join(incident_id, alarm, RULE_ANCHOR)

    # Pass 2: remaining non-anchor, non-excluded alarms join by
    # window, preferring hierarchy-related anchors. An excluded
    # kind the storm pack names joinable may join a *storm*
    # incident — the one widening of the exclusion storm mode
    # makes, stated in the pack.
    for alarm in ordered:
        if alarm.kind in anchors:
            continue
        if alarm.kind in excluded:
            if alarm.kind not in joinable:
                continue
            candidates = [
                incident_id
                for incident_id in candidate_incidents(alarm)
                if stamps.get(incident_id)
            ]
            if not candidates:
                continue  # stays uncorrelated: excluded_kind
            join(candidates[0], alarm, RULE_FEEDER_WINDOW)
            result.uncorrelated_reasons.pop(alarm.alarm_id, None)
            continue
        candidates = candidate_incidents(alarm)
        if not candidates:
            # The reason names the rule that refused: the cap,
            # when a window-reachable incident existed and the
            # cap alone refused the join; the window otherwise.
            result.uncorrelated_reasons[alarm.alarm_id] = (
                REASON_BEYOND_SPAN
                if window_candidates(alarm)
                else "no_anchor_in_window"
            )
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
