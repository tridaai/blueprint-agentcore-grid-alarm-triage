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
- **Dual control over declarations (Round 16).** The
  declaration was the one remaining single-signature act that
  changes standing state territory-wide: one authorised
  operator made a version effective and unlocked
  re-correlation under it. Round 16 adds the second pair of
  eyes the repo's other consequential acts already have (the
  gated close's confirmation, the quarantine release's
  second reviewer, the handover's acceptance). A
  **proposal** states the whole declaration it asks for —
  version, proposer, effective time, the change set verbatim
  from the payload — and changes nothing: it loads no
  payload, strikes no declaration, and no incident can be
  re-correlated on its strength. A **decision** by a second
  named operator — never the proposer, compared under the
  name folding — confirms or rejects it. Confirmation strikes
  exactly the declaration the proposal stated, with the
  proposal's provenance on its face; rejection requires a
  stated reason and strikes nothing. While a proposal for a
  version stands pending, the direct declaration of that
  version is blocked: the single-signature route cannot be
  used to walk around a live proposal. The direct act itself
  remained for versions no proposal stood over, as the
  record's pre-R16 route — a limit Round 17 closes.
- **Dual control is the only route (Round 17).** The
  topology pack's 2026.10.2 version states the build's
  posture as data (``declaration_route``): the direct
  declaration act is closed, and ``declare`` refuses every
  attempt — naming the governed route, striking nothing,
  loading nothing — whether or not a proposal stands and
  whoever signs. The closure governs new declarations: the
  2026.10.1 pack is kept verbatim above the new one, a
  service struck under it behaves exactly as it did, and a
  standing direct declaration from the earlier posture stays
  valid — read, verified and re-correlated under the current
  one. The bypass that remained is writing the artefact
  straight into the store, and ``verify_declarations`` meets
  it: every standing declaration is re-performed (id
  recomputation, change-set agreement with the payload,
  pack-role and time legality, one declaration per version),
  and a direct declaration must carry a pack version from
  before the closure — one stamped with a pack that closed
  the direct act names no route that could have struck it.
  The deep linkage of a proposal-named declaration stays
  ``verify_proposal``'s job, as it was.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from grid_triage.access import (
    ACT_DECLARE_TOPOLOGY,
    ACT_DECIDE_TOPOLOGY,
    ACT_POST_FIELD_UPDATE,
    ACT_PROPOSE_TOPOLOGY,
    ACT_RECORRELATE,
    Principal,
    fold_name,
    guard_act,
)
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
    TopologyDecision,
    TopologyProposal,
)
from grid_triage.quarantine import QuarantineService, screen_text
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

TOPOLOGY_PACK_V1 = {
    "version": "2026.10.1",
    # The roles allowed to declare a topology version effective
    # and to re-correlate an incident under it — the control-room
    # vocabulary the earlier packs use.
    "actor_roles": ["operator", "duty_manager"],
    # Versions this build can declare. v1 is the baseline every
    # store starts from; it needs no declaration.
    "declarable_versions": ["v2"],
    # Round 16: the dual-control decision vocabulary. A
    # proposal is decided once — confirmed or rejected — by a
    # second operator, never by its proposer.
    "decisions": ["confirmed", "rejected"],
}

# The current pack (Round 17). One change from 2026.10.1: the
# declaration route is stated, and it is dual control only —
# the direct act is closed in this build's posture. The
# 2026.10.1 pack above carries no ``declaration_route`` key
# and keeps the pre-R17 posture, so a record struck under it
# re-performs exactly as it did; the closure governs the
# declarations this build strikes now.
TOPOLOGY_PACK = {
    "version": "2026.10.2",
    "actor_roles": ["operator", "duty_manager"],
    "declarable_versions": ["v2"],
    "decisions": ["confirmed", "rejected"],
    "declaration_route": "dual_control_only",
}


def topology_pack_by_version(version: str) -> dict | None:
    """The topology pack a standing artefact names, or None
    when this build cannot read that version. A declaration is
    judged by the pack it was struck under, not the current
    one — the correlation pack's standing rule, applied here."""
    for pack in (TOPOLOGY_PACK_V1, TOPOLOGY_PACK):
        if pack["version"] == version:
            return pack
    return None

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
        # Round 14: the quarantine book screens this door's
        # text (and any carried alarm's message) before anything
        # is stored. The wiring rebinds the shared instance; a
        # bare construction builds its own over the same store —
        # the state lives in the store, so they are one book.
        self.quarantine = QuarantineService(store)
        # Round 7: the restoration service registers its post-close
        # handler here (wiring sets it after construction — the
        # restoration service itself depends on the closure service,
        # so the handler cannot be a constructor argument without a
        # cycle). Signature: (incident, alarm, recorded_at) -> the
        # correlation dict recorded on the update.
        self.post_close_handler = None

    def list_updates(self, incident_id: str) -> list[FieldUpdate]:
        return self.store.list_field_updates(incident_id)

    def _closed_outcome(
        self, incident: Incident, alarm: Alarm, reported_at: datetime
    ) -> tuple[dict, str | None] | None:
        """Round 7: an alarm update against a CLOSED incident never
        joins it and never reopens it. On a device of the closed
        incident's member set it contradicts the recorded
        restoration — the restoration service's handler records
        the contradiction and opens a fresh incident. On any other
        device the update is recorded as evidence with a named
        non-join. Returns None when the incident is not closed."""
        closure = self.store.get_incident_closure(incident.incident_id)
        if closure is None:
            return None
        member_devices = {
            alarm_.device_id
            for alarm_ in (
                self.store.get_alarm(m.alarm_id)
                for m in self.store.list_memberships(incident.incident_id)
            )
            if alarm_ is not None
        }
        if alarm.device_id in member_devices:
            if self.post_close_handler is not None:
                return self.post_close_handler(incident, alarm, reported_at), None
            return {
                "status": "post_close_contradiction",
                "topology_version": incident.topology_version,
                "reason": (
                    f"incident {incident.incident_id} is closed (closure "
                    f"{closure.closure_id}) — the alarm contradicts the "
                    "recorded restoration and does not reopen it"
                ),
            }, None
        return {
            "status": "no_join",
            "topology_version": incident.topology_version,
            "reason": (
                f"incident {incident.incident_id} is closed (closure "
                f"{closure.closure_id}) — updates against it are recorded "
                "as evidence only and never join it"
            ),
        }, None

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
        principal: Principal | None = None,
    ) -> FieldUpdate:
        guard_act(
            self,
            principal,
            ACT_POST_FIELD_UPDATE,
            target=incident_id,
            actor=reported_by,
            act_role=role,
            at=reported_at,
        )
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
            # Round 14: a carried alarm passes the quarantine
            # door like any ingested alarm — a tripped message
            # is held and the alarm stored with it absent.
            alarm = self.quarantine.screen_alarm(alarm, reported_at)
            self.store.add_alarm(alarm)
            closed = self._closed_outcome(incident, alarm, reported_at)
            if closed is not None:
                correlation, rule_id = closed
            else:
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
        # Round 14: the update's own text passes the screen
        # before the update is built. A tripped field is stored
        # as absent — None, exactly as if the text had never
        # arrived — with the hold named in its place; the raw
        # text is placed in the book once the update exists to
        # name as its source. Untouched text passes byte for
        # byte, as it always has.
        stored_note, stored_finding = note, finding
        holds: list[dict] = []
        pending_holds: list[tuple] = []
        for field_name, channel, value in (
            ("note", "field_update_note", note),
            ("finding", "field_update_finding", finding),
        ):
            if not value:
                continue
            rules = screen_text(value, self.quarantine.pack)
            if not rules:
                continue
            hold_id = self.quarantine.hold_id_for(
                channel=channel,
                field=field_name,
                incident_id=incident_id,
                reported_by=reported_by,
                raw_text=value,
                discriminator=f"{kind}@{reported_at.isoformat()}",
            )
            holds.append({"field": field_name, "hold_id": hold_id})
            pending_holds.append((hold_id, channel, field_name, value, rules))
            if field_name == "note":
                stored_note = None
            else:
                stored_finding = None
        body = {
            "incident_id": incident_id,
            "pack_version": self.pack["version"],
            "kind": kind,
            "reported_by": reported_by,
            "role": role,
            "reported_at": reported_at.isoformat(),
            "note": stored_note,
            "device_id": device_id,
            "finding": stored_finding,
            "alarm_id": alarm.alarm_id if alarm else None,
            "correlation": correlation,
            "holds": holds,
        }
        update = FieldUpdate(update_id=_hash_id("UPD", body), **body)
        self.store.add_field_update(update)
        for hold_id, channel, field_name, value, rules in pending_holds:
            self.quarantine.place_hold(
                hold_id=hold_id,
                channel=channel,
                source_ref=update.update_id,
                field=field_name,
                incident_id=incident_id,
                reported_by=reported_by,
                raw_text=value,
                rules=rules,
                at=reported_at,
            )
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

    def _strike_declaration(
        self,
        version: str,
        declared_by: str,
        role: str,
        declared_at: datetime,
        effective_at: datetime,
        change_set: list,
        proposed_by: str | None = None,
        proposal_id: str | None = None,
    ) -> TopologyDeclaration:
        """The one path a declaration comes into the record
        by — the direct act and a dual-control confirmation
        strike through here, so a declaration's shape never
        depends on which route struck it."""
        body = {
            "version": version,
            "pack_version": self.pack["version"],
            "declared_by": declared_by,
            "role": role,
            "declared_at": declared_at.isoformat(),
            "effective_at": effective_at.isoformat(),
            "change_set": change_set,
            "proposed_by": proposed_by,
            "proposal_id": proposal_id,
        }
        declaration = TopologyDeclaration(
            declaration_id=_hash_id("TOPDECL", body), **body
        )
        self.store.add_topology_declaration(declaration)
        return declaration

    def declare(
        self,
        version: str,
        declared_by: str,
        role: str,
        declared_at: datetime,
        effective_at: datetime,
        principal: Principal | None = None,
    ) -> TopologyDeclaration:
        guard_act(
            self,
            principal,
            ACT_DECLARE_TOPOLOGY,
            target=version,
            actor=declared_by,
            act_role=role,
            at=declared_at,
        )
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
        pending = self.pending_proposal_for(version)
        if pending is not None:
            problems.append(
                f"topology {version} stands under a pending dual-control "
                f"proposal ({pending.proposal_id}, proposed by "
                f"{pending.proposed_by}) — the direct declaration is "
                "blocked while it stands; decide the proposal first"
            )
        if self.pack.get("declaration_route") == "dual_control_only":
            # Round 17: the pack states the build's posture —
            # dual control is the only route to a declaration.
            # The refusal names the governed route and is total:
            # it does not depend on a proposal standing, on the
            # version, or on who signs, and nothing is loaded
            # or struck on its strength.
            problems.append(
                "the direct declaration act is closed under topology "
                f"pack {self.pack['version']} — dual control is the "
                "only route to a declaration: propose the version "
                "(propose_topology) and have a second operator decide "
                "the proposal (decide_topology); a single signature "
                "strikes no declaration"
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
        return self._strike_declaration(
            version,
            declared_by,
            role,
            declared_at,
            effective_at,
            payload.get("change_set", []),
        )

    # -- dual control (Round 16) --------------------------------
    def proposals(self, version: str | None = None) -> list[TopologyProposal]:
        return self.store.list_topology_proposals(version)

    def proposal_for(self, proposal_id: str) -> TopologyProposal | None:
        for proposal in self.store.list_topology_proposals():
            if proposal.proposal_id == proposal_id:
                return proposal
        return None

    def decisions_for(self, proposal_id: str) -> list[TopologyDecision]:
        return self.store.list_topology_decisions(proposal_id)

    def proposal_state(self, proposal: TopologyProposal) -> str:
        """A proposal's standing is the fold of its decision
        artefacts: pending until one decision lands, then that
        decision. A second decision is a corruption the
        verifier names — the fold here takes the first by act
        order, as the record stands."""
        decisions = self.decisions_for(proposal.proposal_id)
        if not decisions:
            return "pending"
        return decisions[0].decision

    def pending_proposal_for(self, version: str) -> TopologyProposal | None:
        for proposal in self.store.list_topology_proposals(version):
            if self.proposal_state(proposal) == "pending":
                return proposal
        return None

    def propose(
        self,
        version: str,
        proposed_by: str,
        role: str,
        proposed_at: datetime,
        effective_at: datetime,
        principal: Principal | None = None,
    ) -> TopologyProposal:
        """Open a dual-control proposal. The proposal states
        the whole declaration it asks for and changes nothing:
        no payload is loaded, no declaration is struck, and
        re-correlation under the version stays impossible until
        a second operator confirms."""
        guard_act(
            self,
            principal,
            ACT_PROPOSE_TOPOLOGY,
            target=version,
            actor=proposed_by,
            act_role=role,
            at=proposed_at,
        )
        problems: list[str] = []
        if version not in self.pack["declarable_versions"]:
            problems.append(
                f"topology version {version!r} cannot be proposed by this "
                f"build (declarable: {', '.join(self.pack['declarable_versions'])})"
            )
        if not proposed_by or not proposed_by.strip():
            problems.append("a proposal names the operator proposing it")
        if role not in self.pack["actor_roles"]:
            problems.append(
                f"role {role!r} may not propose a topology version (allowed: "
                f"{', '.join(self.pack['actor_roles'])})"
            )
        if effective_at < proposed_at:
            problems.append("a version cannot be effective before it is proposed")
        existing = self.declaration_for(version)
        if existing is not None:
            problems.append(
                f"topology {version} is already declared effective by "
                f"{existing.declared_by} (declaration {existing.declaration_id}) "
                "— there is nothing to propose"
            )
        pending = self.pending_proposal_for(version)
        if pending is not None:
            problems.append(
                f"topology {version} already stands under proposal "
                f"{pending.proposal_id} (proposed by {pending.proposed_by}) "
                "— one live proposal per version; decide it first"
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
        body = {
            "version": version,
            "pack_version": self.pack["version"],
            "proposed_by": proposed_by,
            "role": role,
            "proposed_at": proposed_at.isoformat(),
            "effective_at": effective_at.isoformat(),
            "change_set": payload.get("change_set", []),
        }
        proposal = TopologyProposal(proposal_id=_hash_id("TOPPROP", body), **body)
        self.store.add_topology_proposal(proposal)
        return proposal

    def decide(
        self,
        proposal_id: str,
        decided_by: str,
        role: str,
        decided_at: datetime,
        decision: str,
        reason: str | None = None,
        principal: Principal | None = None,
    ) -> tuple[TopologyDecision, TopologyDeclaration | None]:
        """The second pair of eyes. The decider may not be the
        proposer — under the name folding, so a case or
        whitespace variant of the proposer's name is the
        proposer. Confirmation strikes exactly the declaration
        the proposal stated, with the proposal's provenance on
        its face; rejection requires a stated reason and
        strikes nothing. Returns the decision artefact and,
        on confirmation, the declaration it struck."""
        guard_act(
            self,
            principal,
            ACT_DECIDE_TOPOLOGY,
            target=proposal_id,
            actor=decided_by,
            act_role=role,
            at=decided_at,
        )
        proposal = self.proposal_for(proposal_id)
        if proposal is None:
            raise KeyError(f"no such topology proposal: {proposal_id}")
        problems: list[str] = []
        if decision not in self.pack["decisions"]:
            problems.append(
                f"decision {decision!r} is not in the topology pack "
                f"(decisions: {', '.join(self.pack['decisions'])})"
            )
        if not decided_by or not decided_by.strip():
            problems.append("a decision names the operator deciding it")
        if role not in self.pack["actor_roles"]:
            problems.append(
                f"role {role!r} may not decide a topology proposal (allowed: "
                f"{', '.join(self.pack['actor_roles'])})"
            )
        if decided_by and fold_name(decided_by) == fold_name(proposal.proposed_by):
            problems.append(
                f"dual control: {proposal.proposed_by} proposed "
                f"{proposal_id} and may not decide it — a second "
                "operator must confirm or reject"
            )
        if decided_at < proposal.proposed_at:
            problems.append("a decision cannot predate the proposal it decides")
        standing = self.decisions_for(proposal_id)
        if standing:
            problems.append(
                f"proposal {proposal_id} is already {standing[0].decision} "
                f"by {standing[0].decided_by} (decision "
                f"{standing[0].decision_id}) — a decided proposal is not "
                "decided twice"
            )
        if decision == "rejected" and not (reason and reason.strip()):
            problems.append("a rejection states its reason")
        if problems:
            raise TopologyRefusal(problems)
        body = {
            "proposal_id": proposal_id,
            "decision": decision,
            "decided_by": decided_by,
            "role": role,
            "decided_at": decided_at.isoformat(),
            "reason": reason,
        }
        record = TopologyDecision(decision_id=_hash_id("TOPDEC", body), **body)
        self.store.add_topology_decision(record)
        declaration: TopologyDeclaration | None = None
        if decision == "confirmed":
            payload = load_topology_file(proposal.version)
            self.store.load_topology(payload, decided_at)
            declaration = self._strike_declaration(
                proposal.version,
                decided_by,
                role,
                decided_at,
                proposal.effective_at,
                proposal.change_set,
                proposed_by=proposal.proposed_by,
                proposal_id=proposal.proposal_id,
            )
        return record, declaration

    def verify_proposal(self, proposal_id: str) -> list[str]:
        """Re-perform one proposal's whole standing from the
        artefacts of record: the proposal's id recomputes from
        its stated fields, its change set matches the version
        payload, its decision fold is legal (at most one
        decision, in the pack's vocabulary, by a second
        operator under the folding, in a pack role, not
        backdated, a rejection reasoned), and the declaration
        record agrees — a confirmed proposal has exactly the
        declaration it stated struck under its name, and no
        other standing has one. Returns the problems named;
        empty is clean."""
        proposal = self.proposal_for(proposal_id)
        if proposal is None:
            raise KeyError(f"no such topology proposal: {proposal_id}")
        problems: list[str] = []
        body = {
            "version": proposal.version,
            "pack_version": proposal.pack_version,
            "proposed_by": proposal.proposed_by,
            "role": proposal.role,
            "proposed_at": proposal.proposed_at.isoformat(),
            "effective_at": proposal.effective_at.isoformat(),
            "change_set": proposal.change_set,
        }
        if _hash_id("TOPPROP", body) != proposal.proposal_id:
            problems.append(
                f"proposal {proposal_id}: its id does not recompute from "
                "its stated fields — the stored artefact is not the "
                "artefact its id names"
            )
        try:
            payload = load_topology_file(proposal.version)
            if payload.get("change_set", []) != proposal.change_set:
                problems.append(
                    f"proposal {proposal_id}: its change set does not "
                    f"match the {proposal.version} payload's change set"
                )
        except (FileNotFoundError, KeyError) as exc:
            problems.append(
                f"proposal {proposal_id}: no topology data for version "
                f"{proposal.version}: {exc}"
            )
        decisions = self.decisions_for(proposal_id)
        if len(decisions) > 1:
            problems.append(
                f"proposal {proposal_id} carries {len(decisions)} "
                "decisions — a proposal is decided once; the fold "
                "is ambiguous"
            )
        for record in decisions:
            decision_body = {
                "proposal_id": record.proposal_id,
                "decision": record.decision,
                "decided_by": record.decided_by,
                "role": record.role,
                "decided_at": record.decided_at.isoformat(),
                "reason": record.reason,
            }
            if _hash_id("TOPDEC", decision_body) != record.decision_id:
                problems.append(
                    f"decision {record.decision_id}: its id does not "
                    "recompute from its stated fields"
                )
            if record.decision not in self.pack["decisions"]:
                problems.append(
                    f"decision {record.decision_id}: {record.decision!r} "
                    "is not a decision the topology pack states"
                )
            if fold_name(record.decided_by) == fold_name(proposal.proposed_by):
                problems.append(
                    f"decision {record.decision_id}: decided by "
                    f"{record.decided_by}, the proposal's own proposer "
                    "under the name folding — dual control does not hold"
                )
            if record.role not in self.pack["actor_roles"]:
                problems.append(
                    f"decision {record.decision_id}: role "
                    f"{record.role!r} may not decide a topology proposal"
                )
            if record.decided_at < proposal.proposed_at:
                problems.append(
                    f"decision {record.decision_id}: decided at "
                    f"{record.decided_at.isoformat()}, before the "
                    "proposal it decides was proposed"
                )
            if record.decision == "rejected" and not (
                record.reason and record.reason.strip()
            ):
                problems.append(
                    f"decision {record.decision_id}: a rejection "
                    "without a stated reason"
                )
        state = self.proposal_state(proposal)
        linked = [
            d
            for d in self.store.list_topology_declarations(proposal.version)
            if d.proposal_id == proposal_id
        ]
        if state == "confirmed":
            if len(linked) != 1:
                problems.append(
                    f"proposal {proposal_id} stands confirmed but "
                    f"{len(linked)} declarations name it — confirmation "
                    "strikes exactly the one declaration it stated"
                )
            else:
                declaration = linked[0]
                decision = decisions[0]
                if declaration.declared_by != decision.decided_by:
                    problems.append(
                        f"declaration {declaration.declaration_id}: "
                        f"declared by {declaration.declared_by}, but the "
                        f"confirming decision was {decision.decided_by}'s"
                    )
                if declaration.proposed_by != proposal.proposed_by:
                    problems.append(
                        f"declaration {declaration.declaration_id}: its "
                        f"proposer reads {declaration.proposed_by!r}, "
                        f"not the proposal's {proposal.proposed_by!r}"
                    )
                if declaration.effective_at != proposal.effective_at:
                    problems.append(
                        f"declaration {declaration.declaration_id}: "
                        "effective at "
                        f"{declaration.effective_at.isoformat()}, not "
                        "the proposed "
                        f"{proposal.effective_at.isoformat()} — a "
                        "confirmation confirms the proposal as struck, "
                        "it does not restate it"
                    )
                if declaration.change_set != proposal.change_set:
                    problems.append(
                        f"declaration {declaration.declaration_id}: its "
                        "change set is not the proposal's change set"
                    )
                if declaration.declared_at != decision.decided_at:
                    problems.append(
                        f"declaration {declaration.declaration_id}: "
                        "declared at "
                        f"{declaration.declared_at.isoformat()}, not at "
                        "the confirming decision's "
                        f"{decision.decided_at.isoformat()}"
                    )
        elif linked:
            problems.append(
                f"proposal {proposal_id} stands {state} but declaration "
                f"{linked[0].declaration_id} names it — a declaration "
                "exists that no confirmation struck"
            )
        return problems

    def verify_declarations(self) -> list[str]:
        """Re-perform every standing declaration in the record
        (Round 17). ``verify_proposal`` covers a proposal and
        the declaration its confirmation struck; a declaration
        itself — above all one naming no proposal — had no
        verifier, so a declaration written straight into the
        store stood on its own say-so. Each declaration is now
        re-performed on its own terms: its id recomputes from
        its stated fields; the pack it names is one this build
        can read, and its role and times are legal under that
        pack; its change set matches the version payload's;
        there is one declaration per version; and a direct
        declaration (one naming no proposal) must carry a pack
        version from before the direct route closed — a direct
        declaration stamped with a pack that closed the act
        names no route that could have struck it. A declaration
        naming a proposal must name one in the record; the deep
        agreement between the two stays ``verify_proposal``'s
        job. Returns the problems named; empty is clean."""
        problems: list[str] = []
        declarations = self.store.list_topology_declarations()
        by_version: dict[str, list[TopologyDeclaration]] = {}
        for declaration in declarations:
            by_version.setdefault(declaration.version, []).append(declaration)
        for version, group in by_version.items():
            if len(group) > 1:
                problems.append(
                    f"topology {version} carries {len(group)} standing "
                    "declarations — a version is declared once; the "
                    "record does not say which declaration stands"
                )
        for declaration in declarations:
            body = {
                "version": declaration.version,
                "pack_version": declaration.pack_version,
                "declared_by": declaration.declared_by,
                "role": declaration.role,
                "declared_at": declaration.declared_at.isoformat(),
                "effective_at": declaration.effective_at.isoformat(),
                "change_set": declaration.change_set,
                "proposed_by": declaration.proposed_by,
                "proposal_id": declaration.proposal_id,
            }
            if _hash_id("TOPDECL", body) != declaration.declaration_id:
                problems.append(
                    f"declaration {declaration.declaration_id}: its id "
                    "does not recompute from its stated fields — the "
                    "stored artefact is not the artefact its id names"
                )
            pack = topology_pack_by_version(declaration.pack_version)
            if pack is None:
                problems.append(
                    f"declaration {declaration.declaration_id}: struck "
                    f"under topology pack {declaration.pack_version!r}, "
                    "which this build cannot read"
                )
                continue
            if declaration.role not in pack["actor_roles"]:
                problems.append(
                    f"declaration {declaration.declaration_id}: role "
                    f"{declaration.role!r} may not declare a topology "
                    "version under the pack it names"
                )
            if declaration.effective_at < declaration.declared_at:
                problems.append(
                    f"declaration {declaration.declaration_id}: "
                    "effective at "
                    f"{declaration.effective_at.isoformat()}, before it "
                    f"was declared at "
                    f"{declaration.declared_at.isoformat()}"
                )
            try:
                payload = load_topology_file(declaration.version)
                if payload.get("change_set", []) != declaration.change_set:
                    problems.append(
                        f"declaration {declaration.declaration_id}: its "
                        "change set does not match the "
                        f"{declaration.version} payload's change set"
                    )
            except (FileNotFoundError, KeyError) as exc:
                problems.append(
                    f"declaration {declaration.declaration_id}: no "
                    f"topology data for version {declaration.version}: "
                    f"{exc}"
                )
            if declaration.proposal_id is None:
                if pack.get("declaration_route") == "dual_control_only":
                    problems.append(
                        f"declaration {declaration.declaration_id}: a "
                        "direct declaration — it names no proposal — "
                        f"stamped with topology pack "
                        f"{declaration.pack_version}, which closed the "
                        "direct act; no route the record allows struck it"
                    )
            elif self.proposal_for(declaration.proposal_id) is None:
                problems.append(
                    f"declaration {declaration.declaration_id}: names "
                    f"proposal {declaration.proposal_id}, which is not "
                    "in the record"
                )
        return problems


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
        storm=None,
    ):
        self.store = store
        self.chain = chain
        self.cause = cause
        self.severity = severity
        self.etr = etr
        self.topology = topology
        self.pack = pack
        # Round 8: the storm state fold, when wired. A revision
        # is produced by the re-correlation act, so the revised
        # incident is stamped with the mode in force at that act.
        # Grouping itself still follows the correlation pack —
        # the stamp labels the revision, it does not re-group it.
        self.storm = storm

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
        principal: Principal | None = None,
    ) -> IncidentRevision:
        guard_act(
            self,
            principal,
            ACT_RECORRELATE,
            target=incident_id,
            actor=recorrelated_by,
            act_role=role,
            at=recorrelated_at,
        )
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

        stamp = (
            self.storm.stamp_for(incident.feeder_id, recorrelated_at)
            if self.storm is not None
            else {}
        )
        revised = Incident(
            incident_id=revised_id,
            feeder_id=incident.feeder_id,
            substation_id=view.substation_of_feeder(incident.feeder_id)
            or incident.substation_id,
            topology_version=TARGET_VERSION,
            correlation_pack_version=group.correlation_pack_version,
            anchor_alarm_id=incident.anchor_alarm_id,
            created_at=recorrelated_at,
            **stamp,
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
