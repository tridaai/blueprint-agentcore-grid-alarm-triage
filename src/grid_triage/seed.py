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

import hashlib
from datetime import datetime, timedelta, timezone

from grid_triage.evidence import canonical_json
from grid_triage.models import Alarm, MemoryRecord

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


# ---------------------------------------------------------------------
# Planted memory history (Round 3) — closed incidents from before
# the live window, as memory records. Gold by construction: the
# retrieval grading is hand-computed against these exact records.
#
# For the Feeder 7 flood (anchor BRK-070, class vegetation_contact,
# member devices BRK-070/REC-071/SEC-072/SEC-073/XF-0721/XF-0731/
# SEN-0722/SEN-0732), the retrieval weights give:
#
# - INC-HIST-0042 (Feeder 7, vegetation): class 4 + feeder 3 +
#   device overlap 1 + shared terms {vegetation_contact, rec-071}
#   2 = 10.
# - INC-HIST-0057 (Feeder 7, equipment failure, same anchor
#   device): feeder 3 + anchor device 2 + device overlap 1 +
#   shared terms {sec-072} 1 = 7.
# - INC-HIST-0063 (Feeder 12): no structural basis — not a prior
#   for the flood at all.
#
# So the flood's priors are exactly [0042, 0057], in that order —
# and the class weight is what puts 0042 first: without it, 0057's
# anchor-device match would win.
# ---------------------------------------------------------------------


def _history_record(**fields) -> MemoryRecord:
    body = {k: v for k, v in fields.items()}
    for key in ("opened_at", "closed_at"):
        body[key] = body[key].isoformat()
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return MemoryRecord(record_id=f"MEM-{digest[:16]}", **fields)


def history_records() -> list[MemoryRecord]:
    return [
        _history_record(
            incident_id="INC-HIST-0042",
            feeder_id="FDR-07",
            substation_id="SUB-ALDER",
            anchor_device_id="REC-071",
            device_ids=["BRK-070", "REC-071", "SEC-072", "XF-0721"],
            cause_class="vegetation_contact",
            cause_rule_id="lockout_with_downstream_fault",
            severity_band="SEV-2",
            severity_score=52,
            member_alarm_count=14,
            customers_affected=1440,
            resolution_summary=(
                "Tree contact at REC-071 cleared by the vegetation crew; "
                "feeder restored the same morning."
            ),
            opened_at=datetime(2026, 6, 18, 6, 12, tzinfo=timezone.utc),
            closed_at=datetime(2026, 6, 18, 11, 40, tzinfo=timezone.utc),
            topology_version="v1",
        ),
        _history_record(
            incident_id="INC-HIST-0057",
            feeder_id="FDR-07",
            substation_id="SUB-ALDER",
            anchor_device_id="BRK-070",
            device_ids=["BRK-070", "SEC-072", "XF-0721"],
            cause_class="equipment_failure",
            cause_rule_id="thermal_without_lockout",
            severity_band="SEV-3",
            severity_score=33,
            member_alarm_count=6,
            customers_affected=760,
            resolution_summary=(
                "Sectionalizer SEC-072 replaced after insulation failure; "
                "feeder restored."
            ),
            opened_at=datetime(2026, 8, 29, 15, 3, tzinfo=timezone.utc),
            closed_at=datetime(2026, 8, 29, 19, 25, tzinfo=timezone.utc),
            topology_version="v1",
        ),
        _history_record(
            incident_id="INC-HIST-0063",
            feeder_id="FDR-12",
            substation_id="SUB-SOUTH",
            anchor_device_id="REG-1202",
            device_ids=["REG-1202", "RTU-120"],
            cause_class="equipment_failure",
            cause_rule_id="thermal_without_lockout",
            severity_band="SEV-4",
            severity_score=12,
            member_alarm_count=3,
            customers_affected=340,
            resolution_summary=(
                "Regulator REG-1202 tap mechanism repaired; voltage back in band."
            ),
            opened_at=datetime(2026, 9, 5, 9, 17, tzinfo=timezone.utc),
            closed_at=datetime(2026, 9, 5, 12, 2, tzinfo=timezone.utc),
            topology_version="v1",
        ),
    ]


HISTORY_INCIDENTS = [r.incident_id for r in history_records()]
