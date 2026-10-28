"""The planted reference feed — gold by construction.

Three streams for the fictional Kestrel Power & Light, generated
deterministically so tests and evals grade against known truth:

- **The Feeder 7 flood** (``STREAM-SCADA-NORTH``): a tree contact on
  Alder Feeder 7 trips the feeder breaker at 02:13 on 2026-10-14
  and 40 alarms land inside six minutes — protection operations
  first, then the downstream consequences. Planted truth: exactly
  ONE incident, containing exactly these 40 alarm ids.
- **The nuisance stream** (``STREAM-COMMS-SOUTH``): eight
  comms/telemetry alarms on South Feeder 12 across three hours.
  Planted truth: NO incident, and every alarm stays visible as
  uncorrelated.
- **The lone trip** (``STREAM-SCADA-NORTH``): one breaker trip on
  Alder Feeder 3 that morning. Planted truth: one single-alarm
  incident.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from grid_triage.models import Alarm

T0 = datetime(2026, 10, 14, 2, 13, 0, tzinfo=timezone.utc)

# (device, kind, severity) cycled for the flood's consequence alarms.
_FLOOD_CYCLE = [
    ("REC-071", "voltage_sag", "major"),
    ("SEC-072", "phase_loss", "major"),
    ("SEN-0722", "line_sensor_fault", "minor"),
    ("XF-0721", "transformer_temp_high", "major"),
    ("SEC-073", "voltage_sag", "major"),
    ("SEN-0732", "current_fault_indication", "major"),
    ("XF-0731", "transformer_temp_high", "minor"),
    ("REC-071", "voltage_sag", "minor"),
]


def flood_alarms() -> list[Alarm]:
    alarms: list[Alarm] = [
        Alarm(
            alarm_id="ALM-FLD-001",
            stream_id="STREAM-SCADA-NORTH",
            device_id="BRK-070",
            feeder_id="FDR-07",
            kind="breaker_trip",
            severity="critical",
            raised_at=T0,
            message="Feeder 7 breaker tripped on protection",
        ),
        Alarm(
            alarm_id="ALM-FLD-002",
            stream_id="STREAM-SCADA-NORTH",
            device_id="BRK-070",
            feeder_id="FDR-07",
            kind="relay_protection",
            severity="critical",
            raised_at=T0 + timedelta(seconds=4),
            message="Distance relay operated, zone 1",
        ),
        Alarm(
            alarm_id="ALM-FLD-003",
            stream_id="STREAM-SCADA-NORTH",
            device_id="REC-071",
            feeder_id="FDR-07",
            kind="recloser_lockout",
            severity="critical",
            raised_at=T0 + timedelta(seconds=31),
            message="Recloser R1 locked out after unsuccessful reclose attempts",
        ),
    ]
    for i in range(37):
        device, kind, severity = _FLOOD_CYCLE[i % len(_FLOOD_CYCLE)]
        alarms.append(
            Alarm(
                alarm_id=f"ALM-FLD-{i + 4:03d}",
                stream_id="STREAM-SCADA-NORTH",
                device_id=device,
                feeder_id="FDR-07",
                kind=kind,
                severity=severity,
                raised_at=T0 + timedelta(seconds=45 + i * 9),
                message=f"{kind} reported by {device}",
            )
        )
    return alarms


def nuisance_alarms() -> list[Alarm]:
    kinds = ["comms_poll_failure", "tap_step_counter", "meter_ping_failure"]
    devices = ["RTU-120", "REG-1202", "SEN-1201"]
    out = []
    base = datetime(2026, 10, 14, 0, 5, 0, tzinfo=timezone.utc)
    for i in range(8):
        out.append(
            Alarm(
                alarm_id=f"ALM-NUI-{i + 1:03d}",
                stream_id="STREAM-COMMS-SOUTH",
                device_id=devices[i % len(devices)],
                feeder_id="FDR-12",
                kind=kinds[i % len(kinds)],
                severity="info",
                raised_at=base + timedelta(minutes=23 * i),
                message=f"{kinds[i % len(kinds)]} on {devices[i % len(devices)]}",
            )
        )
    return out


def lone_alarms() -> list[Alarm]:
    return [
        Alarm(
            alarm_id="ALM-LONE-001",
            stream_id="STREAM-SCADA-NORTH",
            device_id="BRK-030",
            feeder_id="FDR-03",
            kind="breaker_trip",
            severity="critical",
            raised_at=datetime(2026, 10, 14, 9, 41, 0, tzinfo=timezone.utc),
            message="Feeder 3 breaker tripped on protection",
        )
    ]


def reference_feed() -> list[Alarm]:
    """All planted streams, in arrival order."""
    alarms = nuisance_alarms() + flood_alarms() + lone_alarms()
    return sorted(alarms, key=lambda a: (a.raised_at, a.alarm_id))


FLOOD_ALARM_IDS = [a.alarm_id for a in flood_alarms()]
NUISANCE_ALARM_IDS = [a.alarm_id for a in nuisance_alarms()]
LONE_ALARM_IDS = [a.alarm_id for a in lone_alarms()]


class ReferenceFeedAdapter:
    """The ingest port's reference implementation: returns the
    planted streams. A production adapter reads the historian/OMS
    feed; the port shape does not change."""

    def fetch_alarms(self) -> list[Alarm]:
        return reference_feed()
