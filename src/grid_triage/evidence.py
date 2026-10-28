"""Hash-chained evidence — Law 7.

Every alarm that correlation places in an incident becomes an
append-only item on that incident's chain. Each item stores the
hash of the previous item; an item's own hash covers its payload
digest and its predecessor, so altering or reordering history
breaks :func:`verify_chain` mechanically. Corrections are new
items — nothing is ever edited in place. Uncorrelated alarms are
not on any chain: they sit in the append-only ingest log (the
alarms table) and stay visible through the API — suppression is a
view, never a deletion.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from grid_triage.models import EvidenceItem
from grid_triage.object_store import ObjectStore

GENESIS_HASH = "GENESIS"


def canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def item_hash(prev_hash: str, payload_sha256: str, kind: str, object_key: str) -> str:
    material = f"{prev_hash}|{kind}|{object_key}|{payload_sha256}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class EvidenceChain:
    """Appends and verifies evidence items for one store/object-store
    pair. Persistence goes through the store port; payloads live in
    the object store."""

    def __init__(self, store, objects: ObjectStore):
        self.store = store
        self.objects = objects

    def append(
        self,
        incident_id: str,
        kind: str,
        payload: dict,
        recorded_on: datetime,
        item_id: str,
    ) -> EvidenceItem:
        data = canonical_json(payload)
        object_key = f"incidents/{incident_id}/{item_id}.json"
        digest = self.objects.put_object(object_key, data)
        existing = self.store.list_evidence(incident_id)
        prev_hash = existing[-1].item_hash if existing else GENESIS_HASH
        item = EvidenceItem(
            item_id=item_id,
            incident_id=incident_id,
            kind=kind,
            object_key=object_key,
            payload_sha256=digest,
            prev_hash=prev_hash,
            item_hash=item_hash(prev_hash, digest, kind, object_key),
            recorded_on=recorded_on,
        )
        self.store.add_evidence(item)
        return item

    def payload_of(self, item: EvidenceItem) -> dict:
        return json.loads(self.objects.get_object(item.object_key))

    def verify_chain(self, incident_id: str) -> bool:
        items = self.store.list_evidence(incident_id)
        prev = GENESIS_HASH
        for item in items:
            if item.prev_hash != prev:
                return False
            data = self.objects.get_object(item.object_key)
            digest = hashlib.sha256(data).hexdigest()
            if digest != item.payload_sha256:
                return False
            if item_hash(item.prev_hash, digest, item.kind, item.object_key) != item.item_hash:
                return False
            prev = item.item_hash
        return True
