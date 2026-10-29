"""Sealed evidence packs — Round 3.

An incident brief is an argument; the evidence pack is the record
the argument rests on. Sealing is an explicit, named act that
assembles everything the incident rests on at that moment into a
**manifest**:

- every member alarm, with its hash-chain references;
- the correlation decisions — the membership and rule id that
  placed each alarm;
- the current severity assessment, by id and content hash;
- the topology slice the incident covers, at the topology version
  the incident pins;
- the latest brief;
- the prior-incident memory records retrieved for the incident,
  by id and content hash.

``manifest_hash`` covers the member list. A pack is never edited:
when a member is later superseded — a new severity assessment, a
new brief — resealing produces the pack's next version, linked to
the one it supersedes, and the old pack still verifies against the
artefacts it sealed (artefacts of record are immutable; that is
what makes an old pack meaningful).

Verification is recomputation, not trust: every member hash is
recomputed from its source of record, the manifest hash is
recomputed, and the incident's evidence chain must verify whole.
Any drift — an altered alarm payload, a rewritten member hash, a
superseded-but-edited assessment — comes back as a named problem.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from grid_triage.access import ACT_SEAL_PACK, Principal, guard_act
from grid_triage.cause import CauseService
from grid_triage.evidence import EvidenceChain, canonical_json
from grid_triage.memory import MemoryService
from grid_triage.models import EvidencePack, Incident
from grid_triage.severity import SeverityService
from grid_triage.topology import TopologyView


def content_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def manifest_hash(incident_id: str, members: list[dict]) -> str:
    return content_hash({"incident_id": incident_id, "members": members})


class PackRefusal(RuntimeError):
    """Sealing cannot proceed: a component the pack must rest on
    does not exist or cannot be proven."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class EvidencePackService:
    def __init__(
        self,
        store,
        chain: EvidenceChain,
        cause: CauseService,
        severity: SeverityService,
        memory: MemoryService,
    ):
        self.store = store
        self.chain = chain
        self.cause = cause
        self.severity = severity
        self.memory = memory

    # -- member builders (shared by seal and verify) -------------------
    def _correlation_content(self, incident: Incident) -> dict:
        memberships = self.store.list_memberships(incident.incident_id)
        return {
            "correlation_pack_version": incident.correlation_pack_version,
            "memberships": [
                {"alarm_id": m.alarm_id, "rule_id": m.rule_id}
                for m in sorted(memberships, key=lambda m: m.alarm_id)
            ],
        }

    def _slice_content(self, incident: Incident) -> dict | None:
        payload = self.store.get_topology_payload(incident.topology_version)
        if payload is None:
            return None
        anchor = self.store.get_alarm(incident.anchor_alarm_id)
        if anchor is None:
            return None
        view = TopologyView(payload)
        slice_ids = view.outage_slice(anchor.device_id)
        if slice_ids is None:
            return None
        return {
            "topology_version": incident.topology_version,
            "feeder": view.feeders.get(incident.feeder_id),
            "devices": [
                view.devices[d] for d in sorted(slice_ids) if d in view.devices
            ],
            "critical_loads": sorted(
                view.critical_loads_in(slice_ids) or [],
                key=lambda load: load["load_id"],
            ),
        }

    def _priors_content(self, hits) -> dict:
        return {
            "records": [
                {
                    "record_id": hit.record.record_id,
                    "content_hash": content_hash(hit.record.model_dump(mode="json")),
                }
                for hit in hits
            ]
        }

    def _build_members(
        self, incident: Incident, sealed_at: datetime
    ) -> list[dict]:
        problems: list[str] = []
        items = {i.item_id: i for i in self.store.list_evidence(incident.incident_id)}
        alarms = {
            a.alarm_id: a for a in self.store.list_alarms()
        }
        members: list[dict] = []

        memberships = sorted(
            self.store.list_memberships(incident.incident_id),
            key=lambda m: (
                alarms[m.alarm_id].raised_at if m.alarm_id in alarms else m.alarm_id,
                m.alarm_id,
            ),
        )
        if not memberships:
            problems.append("incident has no member alarms to seal")
        for membership in memberships:
            item = items.get(f"EV-{membership.alarm_id}")
            if item is None:
                problems.append(
                    f"member alarm {membership.alarm_id} has no evidence item"
                )
                continue
            members.append(
                {
                    "type": "alarm",
                    "alarm_id": membership.alarm_id,
                    "evidence_item_id": item.item_id,
                    "item_hash": item.item_hash,
                    "payload_sha256": item.payload_sha256,
                }
            )
        members.append(
            {
                "type": "correlation_decisions",
                "incident_id": incident.incident_id,
                **{"content_hash": content_hash(self._correlation_content(incident))},
                "correlation_pack_version": incident.correlation_pack_version,
            }
        )

        # Classification first (retrieval needs the class), then the
        # current severity assessment of record.
        self.cause.classify(incident.incident_id, sealed_at)
        assessment = self.severity.current(incident.incident_id)
        if assessment is None:
            assessment = self.severity.assess(incident.incident_id, sealed_at)
        members.append(
            {
                "type": "severity_assessment",
                "assessment_id": assessment.assessment_id,
                "content_hash": content_hash(assessment.model_dump(mode="json")),
            }
        )

        slice_content = self._slice_content(incident)
        if slice_content is None:
            problems.append(
                "topology slice cannot be proven at version "
                f"{incident.topology_version}"
            )
        else:
            members.append(
                {
                    "type": "topology_slice",
                    "topology_version": incident.topology_version,
                    "content_hash": content_hash(slice_content),
                }
            )

        briefs = self.store.list_briefs(incident.incident_id)
        if not briefs:
            problems.append(
                "no brief has been assembled for this incident — "
                "seal after assembly"
            )
        else:
            brief = briefs[-1]
            members.append(
                {
                    "type": "brief",
                    "brief_id": brief.brief_id,
                    "content_hash": content_hash(brief.model_dump(mode="json")),
                }
            )

        hits = self.memory.retrieve_for_incident(incident.incident_id, sealed_at)
        priors = self._priors_content(hits)
        members.append(
            {
                "type": "priors",
                "incident_id": incident.incident_id,
                "records": priors["records"],
                "content_hash": content_hash(priors),
            }
        )
        if problems:
            raise PackRefusal(problems)
        return members

    # -- the acts --------------------------------------------------------
    def seal(
        self,
        incident_id: str,
        sealed_by: str,
        sealed_at: datetime,
        principal: Principal | None = None,
    ) -> EvidencePack:
        guard_act(
            self,
            principal,
            ACT_SEAL_PACK,
            target=incident_id,
            actor=sealed_by,
            at=sealed_at,
        )
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        if not sealed_by or not sealed_by.strip():
            raise PackRefusal(["sealing names the person who seals the pack"])
        members = self._build_members(incident, sealed_at)
        history = self.store.list_evidence_packs(incident_id)
        version = len(history) + 1
        pack = EvidencePack(
            pack_id=f"PACK-{incident_id}-{version:03d}",
            incident_id=incident_id,
            version=version,
            supersedes=history[-1].pack_id if history else None,
            sealed_by=sealed_by,
            sealed_at=sealed_at,
            members=members,
            manifest_hash=manifest_hash(incident_id, members),
        )
        self.store.add_evidence_pack(pack)
        self.chain.append(
            incident_id,
            "evidence_pack",
            pack.model_dump(mode="json"),
            recorded_on=sealed_at,
            item_id=f"EV-{pack.pack_id}",
        )
        return pack

    def current(self, incident_id: str) -> EvidencePack | None:
        history = self.store.list_evidence_packs(incident_id)
        return history[-1] if history else None

    def history(self, incident_id: str) -> list[EvidencePack]:
        return self.store.list_evidence_packs(incident_id)

    # -- verification ------------------------------------------------------
    def verify_pack(self, pack_id: str) -> list[str]:
        """Recompute every member from its source of record.
        Returns the list of problems; empty means the pack verifies
        exactly as sealed."""
        pack = self.store.get_evidence_pack(pack_id)
        if pack is None:
            raise KeyError(f"no such evidence pack: {pack_id}")
        incident = self.store.get_incident(pack.incident_id)
        problems: list[str] = []
        if incident is None:
            return [f"pack's incident {pack.incident_id} no longer exists"]
        if manifest_hash(pack.incident_id, pack.members) != pack.manifest_hash:
            problems.append("manifest hash does not recompute from the member list")
        if not self.chain.verify_chain(pack.incident_id):
            problems.append("the incident's evidence chain fails verification")
        items = {i.item_id: i for i in self.store.list_evidence(pack.incident_id)}
        for member in pack.members:
            kind = member.get("type")
            if kind == "alarm":
                item = items.get(member["evidence_item_id"])
                if item is None:
                    problems.append(
                        f"alarm member {member['alarm_id']}: evidence item missing"
                    )
                    continue
                if item.item_hash != member["item_hash"]:
                    problems.append(
                        f"alarm member {member['alarm_id']}: item hash drifted"
                    )
                data = self.chain.objects.get_object(item.object_key)
                if hashlib.sha256(data).hexdigest() != member["payload_sha256"]:
                    problems.append(
                        f"alarm member {member['alarm_id']}: payload hash drifted"
                    )
            elif kind == "correlation_decisions":
                if content_hash(self._correlation_content(incident)) != member[
                    "content_hash"
                ]:
                    problems.append("correlation decisions no longer recompute")
            elif kind == "severity_assessment":
                assessment = self.store.get_severity_assessment(
                    member["assessment_id"]
                )
                if assessment is None:
                    problems.append(
                        f"severity member {member['assessment_id']}: missing"
                    )
                elif content_hash(assessment.model_dump(mode="json")) != member[
                    "content_hash"
                ]:
                    problems.append(
                        f"severity member {member['assessment_id']}: content drifted"
                    )
            elif kind == "topology_slice":
                slice_content = self._slice_content(incident)
                if slice_content is None or content_hash(slice_content) != member[
                    "content_hash"
                ]:
                    problems.append("topology slice no longer recomputes")
            elif kind == "brief":
                brief = next(
                    (
                        b
                        for b in self.store.list_briefs(pack.incident_id)
                        if b.brief_id == member["brief_id"]
                    ),
                    None,
                )
                if brief is None:
                    problems.append(f"brief member {member['brief_id']}: missing")
                elif content_hash(brief.model_dump(mode="json")) != member[
                    "content_hash"
                ]:
                    problems.append(f"brief member {member['brief_id']}: drifted")
            elif kind == "priors":
                entries = member["records"]
                if content_hash({"records": entries}) != member["content_hash"]:
                    problems.append("priors member list does not recompute")
                for entry in entries:
                    record = self.memory.memory.get_record(entry["record_id"])
                    if record is None:
                        problems.append(
                            f"prior {entry['record_id']}: memory record missing"
                        )
                    elif content_hash(record.model_dump(mode="json")) != entry[
                        "content_hash"
                    ]:
                        problems.append(
                            f"prior {entry['record_id']}: recorded facts drifted"
                        )
            else:
                problems.append(f"unknown pack member type: {kind!r}")
        return problems
