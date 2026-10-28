"""Field updates, topology declarations, re-correlation — Round 6.

Round 5 left the incident at its work order. The field then starts
reporting back, and the territory itself changes. This module is
the machinery for both, under Law 6 (topology is versioned data)
and Law 7 (every alarm is evidence):

- **Field updates** are insert-only artefacts posted against an
  incident by a named reporter in a pack-stated role — a new alarm
  observation, a crew note, a damage finding. An update carrying
  a new alarm re-runs correlation for that alarm under the
  incident's **pinned** topology version: the outcome (joined,
  with the rule; or a named non-join) is recorded on the update
  itself. An alarm whose device exists only in a newer topology
  version is *unresolvable under the pinned topology* — it is
  never silently re-correlated under the newer one.
- **Topology v2** arrives as data (``data/topology/v2.json``) plus
  a **declaration act**: a named operator declares the version
  effective from a stated time. Without the act — or before its
  effective time — nothing migrates. There is no silent migration.
- **Re-correlation** is a second named act. It produces a **new
  incident** correlated under v2, linked to the revision it
  supersedes by an :class:`IncidentRevision` record stating the
  diff: alarms and devices added and removed (membership under
  v2 follows the v2 device→feeder mapping, so a section split
  onto a new feeder leaves the group), the severity recomputed
  on both sides at the same instant with the moved formula
  inputs named, and the ETR estimates on both sides linked by
  id. The prior incident's artefacts are never edited. Closed
  incidents are never re-correlated — the attempt is refused
  naming the closure.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from grid_triage.cause import CauseService
from grid_triage.correlation import correlate
from grid_triage.etr import EtrService
from grid_triage.evidence import EvidenceChain, canonical_json
from grid_triage.models import (
    Alarm,
    FieldUpdate,
    Incident,
    IncidentRevision,
    TopologyDeclaration,
)
from grid_triage.severity import SeverityService, compute_assessment
from grid_triage.topology import TopologyView, load_topology_file

UPDATE_PACK = {
    "version": "2026.10.1",
    # The closed update-kind vocabulary and what each kind must
    # carry. Kinds outside the pack are refused, never improvised.
    "kinds": {
        "alarm_observation": {"requires": ["alarm"]},
        "crew_note": {"requires": ["note"]},
        "damage_finding": {"requires": ["device_id", "finding"]},
    },
    "reporter_roles": ["field_crew", "dispatcher", "operator"],
}

TOPOLOGY_PACK = {
    "version": "2026.10.1",
    # The roles allowed to declare a topology version effective
    # and to re-correlate an incident under it — the control-room
    # vocabulary the earlier packs use.
    "actor_roles": ["operator", "duty_manager"],
    # Versions this build can declare. v1 is the baseline every
    # store starts from; it needs no declaration.
    "declarable_versions": ["v2"],
}

TARGET_VERSION = "v2"


class UpdateRefusal(RuntimeError):
    """A field update the record does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class TopologyRefusal(RuntimeError):
    """A topology declaration act the record does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class RevisionRefusal(RuntimeError):
    """A re-correlation act the record does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _hash_id(prefix: str, body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"{prefix}-{digest[:16]}"


class FieldUpdateService:
    """Store-facing field updates: validate against the pack,
    ingest any carried alarm, correlate it under the incident's
    pinned topology, and record the update on the evidence chain."""

    def __init__(self, store, chain: EvidenceChain, pack: dict = UPDATE_PACK):
        self.store = store
        self.chain = chain
        self.pack = pack

    def list_updates(self, incident_id: str) -> list[FieldUpdate]:
        return self.store.list_field_updates(incident_id)

    def _correlation_outcome(
        self, incident: Incident, alarm: Alarm
    ) -> tuple[dict, str | None]:
        """Re-run correlation for ``alarm`` against the incident's
        current members under its pinned topology version. Returns
        (outcome, rule_id when it joins this incident)."""
        payload = self.store.get_topology_payload(incident.topology_version)
        if payload is None:
            return {
                "status": "unresolvable_under_pinned_topology",
                "topology_version": incident.topology_version,
                "reason": (
                    f"the pinned topology version {incident.topology_version} "
                    "is not loaded"
                ),
            }, None
        view = TopologyView(payload)
        if alarm.device_id not in view.devices:
            return {
                "status": "unresolvable_under_pinned_topology",
                "topology_version": incident.topology_version,
                "device_id": alarm.device_id,
                "reason": (
                    f"device {alarm.device_id} does not exist in the pinned "
                    f"topology version {incident.topology_version} — the "
                    "update is not re-correlated under any newer version"
                ),
            }, None
        members = [
            self.store.get_alarm(m.alarm_id)
            for m in self.store.list_memberships(incident.incident_id)
        ]
        candidates = [a for a in members if a is not None] + [alarm]
        result = correlate(
            candidates, view, incident_id_for=lambda a: f"INC-{a.alarm_id}"
        )
        for group in result.incidents:
            if group.anchor_alarm_id != incident.anchor_alarm_id:
                continue
            for membership in result.memberships:
                if (
                    membership.incident_id == group.incident_id
                    and membership.alarm_id == alarm.alarm_id
                ):
                    return {
                        "status": "joined",
                        "topology_version": incident.topology_version,
                        "rule_id": membership.rule_id,
                    }, membership.rule_id
        if any(
            m.alarm_id == alarm.alarm_id for m in result.memberships
        ):
            return {
                "status": "no_join",
                "topology_version": incident.topology_version,
                "reason": (
                    "the alarm correlates as a separate incident under the "
                    "pinned topology — it is not added to this incident"
                ),
            }, None
        reason = result.uncorrelated_reasons.get(alarm.alarm_id, "no_anchor_in_window")
        text = (
            f"the correlation pack excludes kind {alarm.kind} from grouping"
            if reason == "excluded_kind"
            else "no member of this incident is inside the correlation window"
        )
        return {
            "status": "no_join",
            "topology_version": incident.topology_version,
            "reason": text,
        }, None

    def post_update(
        self,
        incident_id: str,
        kind: str,
        reported_by: str,
        role: str,
        reported_at: datetime,
        note: str | None = None,
        device_id: str | None = None,
        finding: str | None = None,
        alarm: Alarm | None = None,
    ) -> FieldUpdate:
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        problems: list[str] = []
        spec = self.pack["kinds"].get(kind)
        if spec is None:
            problems.append(
                f"update kind {kind!r} is not in the update pack "
                f"(kinds: {', '.join(sorted(self.pack['kinds']))})"
            )
        if not reported_by or not reported_by.strip():
            problems.append("a field update names its reporter")
        if role not in self.pack["reporter_roles"]:
            problems.append(
                f"role {role!r} may not post field updates (allowed: "
                f"{', '.join(self.pack['reporter_roles'])})"
            )
        if spec is not None:
            carried = {
                "alarm": alarm,
                "note": note,
                "device_id": device_id,
                "finding": finding,
            }
            for required in spec["requires"]:
                value = carried[required]
                if value is None or (isinstance(value, str) and not value.strip()):
                    problems.append(f"a {kind} update carries a {required}")
        if alarm is not None and self.store.get_alarm(alarm.alarm_id) is not None:
            problems.append(
                f"alarm {alarm.alarm_id} is already in the ingest log — "
                "an update never overwrites history"
            )
        if problems:
            raise UpdateRefusal(problems)

        correlation: dict = {}
        rule_id: str | None = None
        if alarm is not None:
            self.store.add_alarm(alarm)
            correlation, rule_id = self._correlation_outcome(incident, alarm)
            if correlation["status"] == "joined":
                from grid_triage.models import IncidentMembership

                self.store.add_membership(
                    IncidentMembership(
                        incident_id=incident_id,
                        alarm_id=alarm.alarm_id,
                        rule_id=rule_id,
                    )
                )
                self.chain.append(
                    incident_id,
                    "alarm",
                    alarm.model_dump(mode="json"),
                    recorded_on=reported_at,
                    item_id=f"EV-{alarm.alarm_id}",
                )
        body = {
            "incident_id": incident_id,
            "pack_version": self.pack["version"],
            "kind": kind,
            "reported_by": reported_by,
            "role": role,
            "reported_at": reported_at.isoformat(),
            "note": note,
            "device_id": device_id,
            "finding": finding,
            "alarm_id": alarm.alarm_id if alarm else None,
            "correlation": correlation,
        }
        update = FieldUpdate(update_id=_hash_id("UPD", body), **body)
        self.store.add_field_update(update)
        self.chain.append(
            incident_id,
            "field_update",
            update.model_dump(mode="json"),
            recorded_on=reported_at,
            item_id=f"EV-{update.update_id}",
        )
        return update


class TopologyService:
    """The declaration act for topology versions: the version
    payload is data; this act is what makes it effective."""

    def __init__(self, store, pack: dict = TOPOLOGY_PACK):
        self.store = store
        self.pack = pack

    def declaration_for(self, version: str) -> TopologyDeclaration | None:
        declarations = self.store.list_topology_declarations(version)
        return declarations[-1] if declarations else None

    def declarations(self) -> list[TopologyDeclaration]:
        return self.store.list_topology_declarations()

    def declare(
        self,
        version: str,
        declared_by: str,
        role: str,
        declared_at: datetime,
        effective_at: datetime,
    ) -> TopologyDeclaration:
        problems: list[str] = []
        if version not in self.pack["declarable_versions"]:
            problems.append(
                f"topology version {version!r} cannot be declared by this "
                f"build (declarable: {', '.join(self.pack['declarable_versions'])})"
            )
        if not declared_by or not declared_by.strip():
            problems.append("a declaration names the operator declaring it")
        if role not in self.pack["actor_roles"]:
            problems.append(
                f"role {role!r} may not declare a topology version (allowed: "
                f"{', '.join(self.pack['actor_roles'])})"
            )
        if effective_at < declared_at:
            problems.append("a version cannot be effective before it is declared")
        existing = self.declaration_for(version)
        if existing is not None:
            problems.append(
                f"topology {version} is already declared effective by "
                f"{existing.declared_by} (declaration {existing.declaration_id})"
            )
        payload: dict | None = None
        if not problems:
            try:
                payload = load_topology_file(version)
            except (FileNotFoundError, KeyError) as exc:
                problems.append(f"no topology data for version {version}: {exc}")
        if problems:
            raise TopologyRefusal(problems)
        assert payload is not None
        self.store.load_topology(payload, declared_at)
        body = {
            "version": version,
            "pack_version": self.pack["version"],
            "declared_by": declared_by,
            "role": role,
            "declared_at": declared_at.isoformat(),
            "effective_at": effective_at.isoformat(),
            "change_set": payload.get("change_set", []),
        }
        declaration = TopologyDeclaration(
            declaration_id=_hash_id("TOPDECL", body), **body
        )
        self.store.add_topology_declaration(declaration)
        return declaration


class RevisionService:
    """Re-correlation under a declared topology version: a new
    incident, a stated diff, and the severity/ETR recomputation —
    the prior revision stands untouched."""

    def __init__(
        self,
        store,
        chain: EvidenceChain,
        cause: CauseService,
        severity: SeverityService,
        etr: EtrService,
        topology: TopologyService,
        pack: dict = TOPOLOGY_PACK,
    ):
        self.store = store
        self.chain = chain
        self.cause = cause
        self.severity = severity
        self.etr = etr
        self.topology = topology
        self.pack = pack

    # -- lineage ------------------------------------------------------
    def _lineage_ids(self, incident_id: str) -> set[str]:
        ids = {incident_id}
        changed = True
        while changed:
            changed = False
            for revision in self.store.list_incident_revisions():
                pair = {revision.prior_incident_id, revision.revised_incident_id}
                if pair & ids and not pair <= ids:
                    ids |= pair
                    changed = True
        return ids

    def revision_chain(self, incident_id: str) -> list[IncidentRevision]:
        """Every revision touching this incident's lineage, oldest
        first — the revision chain read."""
        if self.store.get_incident(incident_id) is None:
            raise KeyError(f"no such incident: {incident_id}")
        ids = self._lineage_ids(incident_id)
        return [
            r
            for r in self.store.list_incident_revisions()
            if r.prior_incident_id in ids or r.revised_incident_id in ids
        ]

    def revision_for(self, incident_id: str) -> IncidentRevision | None:
        """The revision that superseded this incident, if any."""
        for revision in self.store.list_incident_revisions():
            if revision.prior_incident_id == incident_id:
                return revision
        return None

    # -- the act --------------------------------------------------------
    def recorrelate(
        self,
        incident_id: str,
        recorrelated_by: str,
        role: str,
        recorrelated_at: datetime,
    ) -> IncidentRevision:
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        problems: list[str] = []
        if not recorrelated_by or not recorrelated_by.strip():
            problems.append("a re-correlation names its operator")
        if role not in self.pack["actor_roles"]:
            problems.append(
                f"role {role!r} may not re-correlate an incident (allowed: "
                f"{', '.join(self.pack['actor_roles'])})"
            )
        if incident.topology_version == TARGET_VERSION:
            problems.append(
                f"incident {incident_id} is already correlated under "
                f"topology {TARGET_VERSION}"
            )
        prior_revision = self.revision_for(incident_id)
        if prior_revision is not None:
            problems.append(
                f"incident {incident_id} was already re-correlated as "
                f"{prior_revision.revised_incident_id} (revision "
                f"{prior_revision.revision_id}) — re-correlate the current "
                "revision instead"
            )
        for lineage_id in sorted(self._lineage_ids(incident_id)):
            closure = self.store.get_incident_closure(lineage_id)
            if closure is not None:
                problems.append(
                    f"incident {lineage_id} is closed (closure "
                    f"{closure.closure_id} recorded by {closure.closed_by}) "
                    "— closed incidents are never re-correlated"
                )
        declaration = self.topology.declaration_for(TARGET_VERSION)
        if declaration is None:
            problems.append(
                f"topology {TARGET_VERSION} has not been declared effective "
                "— there is no silent migration"
            )
        elif recorrelated_at < declaration.effective_at:
            problems.append(
                f"topology {TARGET_VERSION} is not effective until "
                f"{declaration.effective_at.isoformat()} (declaration "
                f"{declaration.declaration_id})"
            )
        if problems:
            raise RevisionRefusal(problems)
        assert declaration is not None

        payload = self.store.get_topology_payload(TARGET_VERSION)
        if payload is None:  # the declaration loaded it; belt and braces
            payload = load_topology_file(TARGET_VERSION)
            self.store.load_topology(payload, recorrelated_at)
        view = TopologyView(payload)

        # Candidates: the prior revision's members, plus any alarm
        # a field update brought to this incident that correlation
        # has not placed (the v2-only devices among them).
        prior_members = self.store.list_memberships(incident_id)
        prior_ids = {m.alarm_id for m in prior_members}
        candidate_ids = set(prior_ids)
        for update in self.store.list_field_updates(incident_id):
            if update.alarm_id and update.alarm_id not in candidate_ids:
                if self.store.membership_for_alarm(update.alarm_id) is None:
                    candidate_ids.add(update.alarm_id)
        candidates = [
            a
            for a in (self.store.get_alarm(aid) for aid in sorted(candidate_ids))
            if a is not None
        ]

        depth = 1
        for revision in self.store.list_incident_revisions():
            if revision.revised_incident_id == incident_id:
                depth = revision.revision_number
        revision_number = depth + 1
        revised_id = f"{incident_id}-R{revision_number}"

        def _id_for(anchor: Alarm) -> str:
            if anchor.alarm_id == incident.anchor_alarm_id:
                return revised_id
            return f"INC-{anchor.alarm_id}"

        result = correlate(candidates, view, incident_id_for=_id_for)
        group = next(
            (g for g in result.incidents if g.incident_id == revised_id), None
        )
        if group is None:
            raise RevisionRefusal(
                [
                    f"the anchor alarm {incident.anchor_alarm_id} does not "
                    f"correlate under topology {TARGET_VERSION}"
                ]
            )
        # Membership under the new version follows the new version's
        # device→feeder mapping: an alarm whose device moved to a
        # different feeder in v2 leaves this incident's group.
        kept: list[tuple[str, str]] = []
        for membership in result.memberships:
            if membership.incident_id != revised_id:
                continue
            alarm = self.store.get_alarm(membership.alarm_id)
            if alarm is None:
                continue
            if view.feeder_of(alarm.device_id) != incident.feeder_id:
                continue
            kept.append((membership.alarm_id, membership.rule_id))
        kept_ids = {aid for aid, _ in kept}
        added_ids = sorted(kept_ids - prior_ids)
        removed_ids = sorted(prior_ids - kept_ids)
        prior_devices = {
            a.device_id
            for a in (self.store.get_alarm(aid) for aid in prior_ids)
            if a is not None
        }
        kept_devices = {
            a.device_id
            for a in (self.store.get_alarm(aid) for aid in kept_ids)
            if a is not None
        }

        revised = Incident(
            incident_id=revised_id,
            feeder_id=incident.feeder_id,
            substation_id=view.substation_of_feeder(incident.feeder_id)
            or incident.substation_id,
            topology_version=TARGET_VERSION,
            correlation_pack_version=group.correlation_pack_version,
            anchor_alarm_id=incident.anchor_alarm_id,
            created_at=recorrelated_at,
        )
        self.store.add_incident(revised)
        from grid_triage.models import IncidentMembership

        for alarm_id, rule_id in kept:
            self.store.add_membership(
                IncidentMembership(
                    incident_id=revised_id, alarm_id=alarm_id, rule_id=rule_id
                )
            )
        kept_alarms = sorted(
            (self.store.get_alarm(aid) for aid in kept_ids),
            key=lambda a: (a.raised_at, a.alarm_id),
        )
        for alarm in kept_alarms:
            if alarm is not None:
                # Evidence item ids are globally unique: the same
                # alarm already sits on the prior revision's chain
                # as EV-<alarm>, so the revised chain names its
                # items for the revision.
                self.chain.append(
                    revised_id,
                    "alarm",
                    alarm.model_dump(mode="json"),
                    recorded_on=recorrelated_at,
                    item_id=f"EV-{revised_id}-{alarm.alarm_id}",
                )

        # Severity: both sides recomputed at the same instant, so
        # the band change is the topology's doing, not the clock's.
        prior_payload = self.store.get_topology_payload(incident.topology_version)
        prior_view = TopologyView(prior_payload) if prior_payload else None
        prior_alarms = {
            m.alarm_id: self.store.get_alarm(m.alarm_id) for m in prior_members
        }
        prior_alarms = {k: v for k, v in prior_alarms.items() if v is not None}
        prior_pure = compute_assessment(
            incident, prior_alarms, prior_view, recorrelated_at
        )
        revised_assessment = self.severity.assess(revised_id, recorrelated_at)
        moved: dict = {}
        for key in (
            "customers_affected",
            "critical_loads",
            "duration_minutes",
            "member_alarm_count",
        ):
            if prior_pure.inputs.get(key) != revised_assessment.inputs.get(key):
                moved[key] = {
                    "prior": prior_pure.inputs.get(key),
                    "revised": revised_assessment.inputs.get(key),
                }
        for key, value in prior_pure.inputs.get("points", {}).items():
            if revised_assessment.inputs.get("points", {}).get(key) != value:
                moved[f"points.{key}"] = {
                    "prior": value,
                    "revised": revised_assessment.inputs.get("points", {}).get(key),
                }
        severity_change = {
            "changed": prior_pure.band != revised_assessment.band,
            "prior_band": prior_pure.band,
            "revised_band": revised_assessment.band,
            "prior_score": prior_pure.score,
            "revised_score": revised_assessment.score,
            "prior_assessment_id": prior_pure.assessment_id,
            "revised_assessment_id": revised_assessment.assessment_id,
            "comparison": (
                "both sides recomputed at the re-correlation time — the "
                "difference is the topology change, not elapsed time"
            ),
            "moved_inputs": moved,
        }

        prior_classification = self.cause.current(incident_id)
        revised_classification = self.cause.classify(revised_id, recorrelated_at)
        prior_estimate = self.etr.current(incident_id)
        revised_estimate = self.etr.estimate(revised_id, recorrelated_at)

        body = {
            "prior_incident_id": incident_id,
            "revised_incident_id": revised_id,
            "revision_number": revision_number,
            "base_topology_version": incident.topology_version,
            "target_topology_version": TARGET_VERSION,
            "declaration_id": declaration.declaration_id,
            "recorrelated_by": recorrelated_by,
            "role": role,
            "recorrelated_at": recorrelated_at.isoformat(),
            "diff": {
                "alarms_added": added_ids,
                "alarms_removed": removed_ids,
                "alarms_kept": sorted(kept_ids),
                "devices_added": sorted(kept_devices - prior_devices),
                "devices_removed": sorted(prior_devices - kept_devices),
                "membership_rules": {aid: rule for aid, rule in sorted(kept)},
                "topology_change_set": declaration.change_set,
            },
            "severity_change": severity_change,
            "prior_classification_id": (
                prior_classification.classification_id
                if prior_classification
                else None
            ),
            "revised_classification_id": revised_classification.classification_id,
            "prior_estimate_id": prior_estimate.estimate_id if prior_estimate else None,
            "revised_estimate_id": revised_estimate.estimate_id,
        }
        revision = IncidentRevision(revision_id=_hash_id("REV", body), **body)
        self.store.add_incident_revision(revision)
        self.chain.append(
            revised_id,
            "incident_revision",
            revision.model_dump(mode="json"),
            recorded_on=recorrelated_at,
            item_id=f"EV-{revision.revision_id}",
        )
        return revision
