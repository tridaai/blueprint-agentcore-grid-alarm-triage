"""Versioned topology — Law 6.

The service territory is data, not code: ``data/topology/v1.json``
carries the substations, feeders, devices (as a parent-linked tree
per feeder) and critical loads for the fictional Kestrel Power &
Light. The file is loaded into the store's relational index
(topology_* tables) so correlation and the API read one versioned,
queryable shape; the JSON payload itself is stored verbatim per
version. A topology change is a new version file — never an edit.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def topology_dir() -> Path:
    candidates = []
    if os.environ.get("GRID_TOPOLOGY_DIR"):
        candidates.append(Path(os.environ["GRID_TOPOLOGY_DIR"]))
    candidates.append(Path(__file__).resolve().parents[2] / "data" / "topology")
    candidates.append(Path("/app/data/topology"))
    candidates.append(Path.cwd() / "data" / "topology")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def load_topology_file(version: str) -> dict:
    path = topology_dir() / f"{version}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def available_versions() -> list[str]:
    directory = topology_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("v*.json"))


class TopologyView:
    """Read helpers over one topology payload: device → feeder →
    substation, ancestors and descendants in the device tree."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.version = payload["version"]
        self.devices = {d["device_id"]: d for d in payload["devices"]}
        self.feeders = {f["feeder_id"]: f for f in payload["feeders"]}
        self.substations = {s["substation_id"]: s for s in payload["substations"]}
        self.critical_loads = list(payload.get("critical_loads", []))
        self._children: dict[str, list[str]] = {}
        for device in payload["devices"]:
            parent = device.get("parent_device_id")
            if parent:
                self._children.setdefault(parent, []).append(device["device_id"])

    def feeder_of(self, device_id: str) -> str | None:
        device = self.devices.get(device_id)
        return device["feeder_id"] if device else None

    def substation_of_feeder(self, feeder_id: str) -> str | None:
        feeder = self.feeders.get(feeder_id)
        return feeder["substation_id"] if feeder else None

    def ancestors(self, device_id: str) -> set[str]:
        out: set[str] = set()
        cur = self.devices.get(device_id, {}).get("parent_device_id")
        while cur:
            out.add(cur)
            cur = self.devices.get(cur, {}).get("parent_device_id")
        return out

    def descendants(self, device_id: str) -> set[str]:
        out: set[str] = set()
        stack = list(self._children.get(device_id, []))
        while stack:
            cur = stack.pop()
            out.add(cur)
            stack.extend(self._children.get(cur, []))
        return out

    def related(self, a: str, b: str) -> bool:
        """True when a and b are the same device or in an
        ancestor/descendant relation."""
        return a == b or b in self.ancestors(a) or b in self.descendants(a)
