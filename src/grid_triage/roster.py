"""The crew roster — a versioned artefact, like the topology.

Who can be sent, with what skills, from which depot, on what
shift, and what they are already committed to: that is data, not
code. ``data/roster/v1.json`` carries the fictional Kestrel Power
& Light roster; a roster change is a new version file — never an
edit — and every ETR estimate pins the roster version it was
computed against, exactly as an incident pins its topology
version (Law 6's discipline, applied to people).

Shift windows are daily, in UTC, and may wrap midnight (a night
crew's 18:00–06:00). A crew's ``assignments`` are its current
commitments: each names the incident it is committed to and the
time it is busy until. The ETR engine reads availability from
this artefact and nothing else — a crew that is not on the
roster, or not qualified, does not exist for estimation.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path


def roster_dir() -> Path:
    candidates = []
    if os.environ.get("GRID_ROSTER_DIR"):
        candidates.append(Path(os.environ["GRID_ROSTER_DIR"]))
    candidates.append(Path(__file__).resolve().parents[2] / "data" / "roster")
    candidates.append(Path("/app/data/roster"))
    candidates.append(Path.cwd() / "data" / "roster")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def load_roster_file(version: str) -> dict:
    path = roster_dir() / f"{version}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def available_versions() -> list[str]:
    directory = roster_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("v*.json"))


def _minute_of_day(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _in_window(minute: int, start: int, end: int) -> bool:
    if start == end:
        return True  # a 24-hour window
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end  # wraps midnight


def earliest_in_window(moment: datetime, start: int, end: int) -> datetime:
    """The earliest time at or after ``moment`` that lies inside
    the daily window [start, end) (minutes of day, UTC)."""
    t = moment
    if t.second or t.microsecond:
        t = t.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(3):
        minute = t.hour * 60 + t.minute
        if _in_window(minute, start, end):
            return t
        day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
        if minute < start:
            t = day_start + timedelta(minutes=start)
        else:
            t = day_start + timedelta(days=1, minutes=start)
    return t  # unreachable for a daily window; loop bound is a guard


class RosterView:
    """Read helpers over one roster payload: qualification,
    depots, commitments and shift availability."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.version = payload["version"]
        self.crews = {c["crew_id"]: c for c in payload["crews"]}

    def crew(self, crew_id: str) -> dict | None:
        return self.crews.get(crew_id)

    def qualified(self, skill: str) -> list[dict]:
        """Crews carrying ``skill``, in crew-id order — the
        deterministic candidate set for the wait computation."""
        return [
            self.crews[crew_id]
            for crew_id in sorted(self.crews)
            if skill in self.crews[crew_id].get("skills", [])
        ]

    def depot_of(self, crew: dict) -> str:
        return crew["depot_substation_id"]

    def busy_until(self, crew: dict) -> datetime | None:
        """The crew's latest current commitment, or None."""
        until: list[datetime] = []
        for assignment in crew.get("assignments", []):
            until.append(datetime.fromisoformat(assignment["busy_until"]))
        return max(until) if until else None

    def available_start(self, crew: dict, as_of: datetime) -> datetime:
        """The earliest time at or after ``as_of`` when this crew
        can start: not before its current commitments end, and
        only inside its shift window."""
        floor = as_of
        busy = self.busy_until(crew)
        if busy is not None and busy > floor:
            floor = busy
        shift = crew["shift"]
        return earliest_in_window(
            floor, _minute_of_day(shift["start"]), _minute_of_day(shift["end"])
        )
