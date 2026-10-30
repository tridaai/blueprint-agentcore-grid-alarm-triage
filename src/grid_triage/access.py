"""Access control — principals, roles, existence hiding (Round 11).

Rounds 1–10 built the engines and their act packs: every named
act names its human and the roles its own pack allows, the gated
close earns a close, the red team hardened the record. None of
it answered the prior question — WHO is calling the surfaces,
and what may that caller see? The surfaces took actor strings
on trust, and an incident id was a capability: anyone who could
guess or learn one could read the incident file. The red team
recorded the sharpest edge of that as RT-17: the Round 4
closure surface was callable by anyone who reached it. This
module is the registry the rest of the system now checks
against, and it follows the repo's one pattern — the rules are
a versioned pack, evaluated in code, and every refusal is a
recorded artefact.

- **The access pack (2026.10.1, below)** states, per role, the
  surfaces the role may read and the acts it may perform. The
  role vocabulary is the control room's own — operator,
  duty_manager, dispatcher, field_crew, auditor — and each
  act's grant never exceeds the engine pack that judges the
  act itself (the restoration pack's signal roles are
  dispatcher/operator, so the access pack grants
  ``record_signal`` to exactly those roles; the field-update
  pack's reporter roles are field_crew/dispatcher/operator, so
  duty_manager is not granted ``post_field_update``). Where both
  layers judge, both must pass: the access pack is the door,
  the engine pack is the act's own discipline, unchanged.
- **A principal is an (id, role) pair.** Surfaces carry it
  explicitly: `X-Principal-Id` / `X-Principal-Role` headers on
  the API, a `principal` object on a Runtime payload, a
  `principal=` argument on the service methods that record
  acts. A carried principal is always authenticated against
  the pack: the pair is judged *as carried* (a padded or
  partial pair is malformed, never trimmed into validity), and
  an unknown role is refused with a named error — there is no
  default principal and no silent downgrade at this layer.
  **Enforcement governs calls that carry a principal.** The
  calls that carry none — the CLI, the local harness, the
  pre-R11 test paths — remain the trusted local operations
  path, exactly as they have been since Round 1. In a
  deployment, AgentCore Identity terminates caller identity in
  front of these surfaces and every call presents a principal.
  The README states this scope plainly.
- **A carried principal binds the act's human and its role.**
  Where an act names its human (a closure's `closed_by`, a
  decision's `decided_by`), the principal's id must be that
  human, compared under a stated folding — case and whitespace
  variants of a name are the same person, so "Op. Dana" cannot
  sign op.dana's act in a different case and call it a
  different caller. Where an act carries a role (a decision's
  `role`, a declaration's `role`), the principal's role must
  be that role: being authorised for an act does not let a
  dispatcher record it *as* an operator. The one exemption is
  the assembler's own drafting: a work order drafted by
  ``system`` names no human, so no principal id is bound —
  the act permission alone governs who may trigger it.
- **Incident reads are existence-hidden.** A principal whose
  role has no read scope over incidents (today: field crews,
  who hold their work orders and nothing else) gets, for any
  incident id on every incident-scoped read surface, exactly
  the response a non-existent incident earns — byte for byte.
  List surfaces filter instead: a role that cannot read
  incidents sees an empty list, never a count. An incident id
  cannot be probed for existence by anyone the pack does not
  entitle to see it.
- **The auditor reads everything and does nothing.** Every
  surface — incident files, the board, the denial log itself —
  is readable; every act is refused, with its own named reason,
  and the refusal recorded like any other.
- **Every refusal is an artefact.** A denial lands in the
  insert-only denial log (migration 0010) BEFORE the error
  propagates: principal, role, surface, target, reason, pack
  version. The log is itself a guarded surface (auditor +
  duty_manager). A denial never leaks what it protects: for a
  hidden incident the recorded target is the requested id
  string — which the requester supplied — and no field of the
  denial says whether the incident exists.
- **RT-17 is closed at the closure service** (see
  `grid_triage/closure.py`): a principal-carrying call on the
  Round 4 closure surface must hold the close act, be the
  closure's named operator, and meet the Round 7 gate's
  preconditions — a complete restoration confirmation of
  record and no open work-order position. The gated surface
  enforces the same access act at its own door and calls the
  closure service as an internal, already-judged step.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping

from pydantic import BaseModel

from grid_triage.evidence import canonical_json

ACCESS_PACK_VERSION = "2026.10.1"

ROLE_OPERATOR = "operator"
ROLE_DUTY_MANAGER = "duty_manager"
ROLE_DISPATCHER = "dispatcher"
ROLE_FIELD_CREW = "field_crew"
ROLE_AUDITOR = "auditor"
ROLES = [
    ROLE_OPERATOR,
    ROLE_DUTY_MANAGER,
    ROLE_DISPATCHER,
    ROLE_FIELD_CREW,
    ROLE_AUDITOR,
]

# Readable surfaces — the closed vocabulary the pack grants over.
SURFACE_INCIDENT = "incident"
SURFACE_ALARMS = "alarms"
SURFACE_SEVERITY = "severity"
SURFACE_ETR = "etr"
SURFACE_WORK_ORDERS = "work_orders"
SURFACE_FIELD_UPDATES = "field_updates"
SURFACE_RESTORATION = "restoration"
SURFACE_STORM = "storm"
SURFACE_CONSOLE = "console"
SURFACE_MEMORY = "memory"
SURFACE_PACKS = "packs"
SURFACE_TOPOLOGY = "topology"
SURFACE_DENIAL_LOG = "denial_log"
SURFACE_ACCESS_PACK = "access_pack"
SURFACE_OVERSIGHT = "oversight"
SURFACE_MODEL_LEDGER = "model_ledger"
SURFACE_QUARANTINE = "quarantine"
SURFACE_INCIDENT_REVIEW = "incident_review"
SURFACE_POLICY = "policy"
SURFACES = [
    SURFACE_INCIDENT,
    SURFACE_ALARMS,
    SURFACE_SEVERITY,
    SURFACE_ETR,
    SURFACE_WORK_ORDERS,
    SURFACE_FIELD_UPDATES,
    SURFACE_RESTORATION,
    SURFACE_STORM,
    SURFACE_CONSOLE,
    SURFACE_MEMORY,
    SURFACE_PACKS,
    SURFACE_TOPOLOGY,
    SURFACE_DENIAL_LOG,
    SURFACE_ACCESS_PACK,
    SURFACE_OVERSIGHT,
    SURFACE_MODEL_LEDGER,
    SURFACE_QUARANTINE,
    SURFACE_INCIDENT_REVIEW,
    SURFACE_POLICY,
]

# Acts — the named operations the pack gates. These are the
# surface-level acts; the engines keep their own packs behind
# them (the work pack's approver roles, the storm pack's actor
# roles, the gated close's preconditions).
ACT_INGEST = "ingest"
ACT_ASSEMBLE_BRIEF = "assemble_brief"
ACT_CLASSIFY = "classify"
ACT_ASSESS_SEVERITY = "assess_severity"
ACT_ACKNOWLEDGE_SEVERITY = "acknowledge_severity"
ACT_ESTIMATE_ETR = "estimate_etr"
ACT_SEAL_PACK = "seal_pack"
ACT_CLOSE_INCIDENT = "close_incident"
ACT_GATED_CLOSE = "gated_close"
ACT_DRAFT_WORK_ORDER = "draft_work_order"
ACT_REVISE_WORK_ORDER = "revise_work_order"
ACT_ASSIGN_WORK_ORDER = "assign_work_order"
ACT_DECIDE_WORK_ORDER = "decide_work_order"
ACT_COMPLETE_WORK_ORDER = "complete_work_order"
ACT_POST_FIELD_UPDATE = "post_field_update"
ACT_DECLARE_TOPOLOGY = "declare_topology"
ACT_PROPOSE_TOPOLOGY = "propose_topology"
ACT_DECIDE_TOPOLOGY = "decide_topology"
ACT_RECORRELATE = "recorrelate"
ACT_RECORD_SIGNAL = "record_signal"
ACT_CONFIRM_RESTORATION = "confirm_restoration"
ACT_SPLIT_INCIDENT = "split_incident"
ACT_DECLARE_STORM = "declare_storm"
ACT_EXTEND_STORM = "extend_storm"
ACT_STAND_DOWN_STORM = "stand_down_storm"
ACT_COMPUTE_STORM_REVIEW = "compute_storm_review"
ACT_ASSEMBLE_HANDOVER = "assemble_handover"
ACT_ACCEPT_HANDOVER = "accept_handover"
ACT_GENERATE_REPORT = "generate_report"
ACT_RELEASE_QUARANTINE = "release_quarantine"
ACT_STRIKE_INCIDENT_REVIEW = "strike_incident_review"
ACT_SEED_HISTORY = "seed_history"
ACTS = [
    ACT_INGEST,
    ACT_ASSEMBLE_BRIEF,
    ACT_CLASSIFY,
    ACT_ASSESS_SEVERITY,
    ACT_ACKNOWLEDGE_SEVERITY,
    ACT_ESTIMATE_ETR,
    ACT_SEAL_PACK,
    ACT_CLOSE_INCIDENT,
    ACT_GATED_CLOSE,
    ACT_DRAFT_WORK_ORDER,
    ACT_REVISE_WORK_ORDER,
    ACT_ASSIGN_WORK_ORDER,
    ACT_DECIDE_WORK_ORDER,
    ACT_COMPLETE_WORK_ORDER,
    ACT_POST_FIELD_UPDATE,
    ACT_DECLARE_TOPOLOGY,
    ACT_PROPOSE_TOPOLOGY,
    ACT_DECIDE_TOPOLOGY,
    ACT_RECORRELATE,
    ACT_RECORD_SIGNAL,
    ACT_CONFIRM_RESTORATION,
    ACT_SPLIT_INCIDENT,
    ACT_DECLARE_STORM,
    ACT_EXTEND_STORM,
    ACT_STAND_DOWN_STORM,
    ACT_COMPUTE_STORM_REVIEW,
    ACT_ASSEMBLE_HANDOVER,
    ACT_ACCEPT_HANDOVER,
    ACT_GENERATE_REPORT,
    ACT_RELEASE_QUARANTINE,
    ACT_STRIKE_INCIDENT_REVIEW,
    ACT_SEED_HISTORY,
]

# The surface each act is recorded against in the denial log.
ACT_SURFACES = {
    ACT_INGEST: SURFACE_ALARMS,
    ACT_ASSEMBLE_BRIEF: SURFACE_INCIDENT,
    ACT_CLASSIFY: SURFACE_INCIDENT,
    ACT_ASSESS_SEVERITY: SURFACE_SEVERITY,
    ACT_ACKNOWLEDGE_SEVERITY: SURFACE_SEVERITY,
    ACT_ESTIMATE_ETR: SURFACE_ETR,
    ACT_SEAL_PACK: SURFACE_PACKS,
    ACT_CLOSE_INCIDENT: SURFACE_INCIDENT,
    ACT_GATED_CLOSE: SURFACE_RESTORATION,
    ACT_DRAFT_WORK_ORDER: SURFACE_WORK_ORDERS,
    ACT_REVISE_WORK_ORDER: SURFACE_WORK_ORDERS,
    ACT_ASSIGN_WORK_ORDER: SURFACE_WORK_ORDERS,
    ACT_DECIDE_WORK_ORDER: SURFACE_WORK_ORDERS,
    ACT_COMPLETE_WORK_ORDER: SURFACE_WORK_ORDERS,
    ACT_POST_FIELD_UPDATE: SURFACE_FIELD_UPDATES,
    ACT_DECLARE_TOPOLOGY: SURFACE_TOPOLOGY,
    ACT_PROPOSE_TOPOLOGY: SURFACE_TOPOLOGY,
    ACT_DECIDE_TOPOLOGY: SURFACE_TOPOLOGY,
    ACT_RECORRELATE: SURFACE_INCIDENT,
    ACT_RECORD_SIGNAL: SURFACE_RESTORATION,
    ACT_CONFIRM_RESTORATION: SURFACE_RESTORATION,
    ACT_SPLIT_INCIDENT: SURFACE_RESTORATION,
    ACT_DECLARE_STORM: SURFACE_STORM,
    ACT_EXTEND_STORM: SURFACE_STORM,
    ACT_STAND_DOWN_STORM: SURFACE_STORM,
    ACT_COMPUTE_STORM_REVIEW: SURFACE_STORM,
    ACT_ASSEMBLE_HANDOVER: SURFACE_CONSOLE,
    ACT_ACCEPT_HANDOVER: SURFACE_CONSOLE,
    ACT_GENERATE_REPORT: SURFACE_OVERSIGHT,
    ACT_RELEASE_QUARANTINE: SURFACE_QUARANTINE,
    ACT_STRIKE_INCIDENT_REVIEW: SURFACE_INCIDENT_REVIEW,
    ACT_SEED_HISTORY: SURFACE_MEMORY,
}

# Denial reasons — the closed vocabulary of the denial log.
REASON_PRINCIPAL_REQUIRED = "principal_required"
REASON_MALFORMED_PRINCIPAL = "malformed_principal"
REASON_UNKNOWN_ROLE = "unknown_role"
REASON_SURFACE_NOT_READABLE = "surface_not_readable"
REASON_ACT_NOT_PERMITTED = "act_not_permitted"
REASON_READ_ONLY_ROLE = "read_only_role"
REASON_INCIDENT_NOT_VISIBLE = "incident_not_visible"
REASON_ACTOR_MISMATCH = "actor_mismatch"
REASON_ROLE_MISMATCH = "role_mismatch"

# The reasons a surface answers as the resource's own not-found
# response: the incident (or the resource naming one) is hidden,
# and the answer must not distinguish hidden from absent.
HIDING_REASONS = {REASON_INCIDENT_NOT_VISIBLE}

# The name the assembler drafts under. An act naming it names no
# human, so a carried principal is not id-bound to it.
SYSTEM_ACTOR = "system"


def fold_name(name: str) -> str:
    """The identity folding actor binding compares under: case
    and whitespace variants of a name are the same person."""
    return " ".join((name or "").split()).casefold()


class AccessDeniedError(RuntimeError):
    """An access refusal — the message names the gap (the
    malformed pair, the unknown role, the role the act
    requires). The denial artefact is recorded before this is
    raised, so catching it never erases the record. ``hidden``
    marks an incident-visibility refusal: the surface must
    answer it as it answers a non-existent incident, never as
    a refusal."""

    def __init__(self, reason: str, message: str, hidden: bool = False):
        super().__init__(message)
        self.reason = reason
        self.hidden = hidden


class Principal(BaseModel):
    """A caller, as the surfaces carry it: a stable id (the
    person's control-room handle, in this repo's name-string
    identity model) and one pack role."""

    id: str
    role: str


class RoleGrant(BaseModel):
    """One role's standing in the pack: the surfaces it may read
    and the acts it may perform."""

    role: str
    read_surfaces: list[str]
    acts: list[str]


class AccessPack(BaseModel):
    """The access pack as data: role grants. The roles each act
    takes are derived from the grants by inversion, so the
    refusal messages and the grants can never drift apart."""

    version: str
    roles: list[RoleGrant]

    def grant(self, role: str) -> RoleGrant | None:
        for grant in self.roles:
            if grant.role == role:
                return grant
        return None

    def can_read(self, role: str, surface: str) -> bool:
        grant = self.grant(role)
        return grant is not None and surface in grant.read_surfaces

    def can_act(self, role: str, act: str) -> bool:
        grant = self.grant(role)
        return grant is not None and act in grant.acts

    def required_roles(self, act: str) -> list[str]:
        return [g.role for g in self.roles if act in g.acts]


# The operational reads: the whole working record except the
# denial log, which is the oversight surface. Round 12 adds the
# oversight reports themselves: aggregate management figures
# are working record for the shift's authorities (and, like
# everything, readable by the auditor) — they are not a
# dispatch or field-crew surface.
_OPERATIONAL_READS = [
    SURFACE_INCIDENT,
    SURFACE_ALARMS,
    SURFACE_SEVERITY,
    SURFACE_ETR,
    SURFACE_WORK_ORDERS,
    SURFACE_FIELD_UPDATES,
    SURFACE_RESTORATION,
    SURFACE_STORM,
    SURFACE_CONSOLE,
    SURFACE_MEMORY,
    SURFACE_PACKS,
    SURFACE_TOPOLOGY,
    SURFACE_ACCESS_PACK,
    SURFACE_OVERSIGHT,
    SURFACE_MODEL_LEDGER,
    SURFACE_QUARANTINE,
    SURFACE_INCIDENT_REVIEW,
    SURFACE_POLICY,
]

# The control-room acts an operator performs. The duty manager
# holds the same set except the two the engine packs reserve:
# restoration signals are recorded by dispatcher/operator
# (restoration pack), and field updates are reported by
# field_crew/dispatcher/operator (field-update pack).
_OPERATOR_ACTS = [
    ACT_INGEST,
    ACT_ASSEMBLE_BRIEF,
    ACT_CLASSIFY,
    ACT_ASSESS_SEVERITY,
    ACT_ACKNOWLEDGE_SEVERITY,
    ACT_ESTIMATE_ETR,
    ACT_SEAL_PACK,
    ACT_CLOSE_INCIDENT,
    ACT_GATED_CLOSE,
    ACT_DRAFT_WORK_ORDER,
    ACT_REVISE_WORK_ORDER,
    ACT_ASSIGN_WORK_ORDER,
    ACT_DECIDE_WORK_ORDER,
    ACT_COMPLETE_WORK_ORDER,
    ACT_POST_FIELD_UPDATE,
    ACT_DECLARE_TOPOLOGY,
    ACT_PROPOSE_TOPOLOGY,
    ACT_DECIDE_TOPOLOGY,
    ACT_RECORRELATE,
    ACT_RECORD_SIGNAL,
    ACT_CONFIRM_RESTORATION,
    ACT_SPLIT_INCIDENT,
    ACT_DECLARE_STORM,
    ACT_EXTEND_STORM,
    ACT_STAND_DOWN_STORM,
    ACT_COMPUTE_STORM_REVIEW,
    ACT_ASSEMBLE_HANDOVER,
    ACT_ACCEPT_HANDOVER,
    ACT_GENERATE_REPORT,
    ACT_RELEASE_QUARANTINE,
    ACT_STRIKE_INCIDENT_REVIEW,
    ACT_SEED_HISTORY,
]
_DUTY_MANAGER_ACTS = [
    act
    for act in _OPERATOR_ACTS
    if act not in (ACT_RECORD_SIGNAL, ACT_POST_FIELD_UPDATE)
]

ACCESS_PACK_V1 = AccessPack(
    version=ACCESS_PACK_VERSION,
    roles=[
        RoleGrant(
            role=ROLE_OPERATOR,
            read_surfaces=list(_OPERATIONAL_READS),
            acts=list(_OPERATOR_ACTS),
        ),
        RoleGrant(
            role=ROLE_DUTY_MANAGER,
            # The shift authority works the same record as the
            # operator, and also reads the denial log: refusals
            # on their watch are theirs to review.
            read_surfaces=list(_OPERATIONAL_READS) + [SURFACE_DENIAL_LOG],
            acts=list(_DUTY_MANAGER_ACTS),
        ),
        RoleGrant(
            role=ROLE_DISPATCHER,
            # Dispatch works the live picture — incidents,
            # alarms, the board, restoration signals — not the
            # evidence archive (packs, memory) or the denial
            # log.
            read_surfaces=[
                SURFACE_INCIDENT,
                SURFACE_ALARMS,
                SURFACE_SEVERITY,
                SURFACE_ETR,
                SURFACE_WORK_ORDERS,
                SURFACE_FIELD_UPDATES,
                SURFACE_RESTORATION,
                SURFACE_STORM,
                SURFACE_CONSOLE,
                SURFACE_TOPOLOGY,
                SURFACE_ACCESS_PACK,
            ],
            acts=[
                ACT_INGEST,
                ACT_POST_FIELD_UPDATE,
                ACT_RECORD_SIGNAL,
                ACT_CONFIRM_RESTORATION,
            ],
        ),
        RoleGrant(
            role=ROLE_FIELD_CREW,
            # A crew holds its work orders — the orders carry
            # the scope of work and the safety notes the crew
            # needs — and nothing else. The incident file is
            # not a crew surface: an incident id in a crew's
            # hands is not a licence to read the incident.
            read_surfaces=[SURFACE_WORK_ORDERS, SURFACE_ACCESS_PACK],
            acts=[ACT_POST_FIELD_UPDATE, ACT_COMPLETE_WORK_ORDER],
        ),
        RoleGrant(
            role=ROLE_AUDITOR,
            # Everything readable, nothing performable: oversight
            # is a read. The denial log is on this list — the
            # auditor audits refusals too.
            read_surfaces=list(SURFACES),
            acts=[],
        ),
    ],
)


def load_access_pack(version: str | None = None) -> AccessPack:
    """The loader, fail-closed like every pack loader in this
    repo: an unknown version is an error, never a silent fall
    back to v1."""
    if version is None or version == ACCESS_PACK_V1.version:
        return ACCESS_PACK_V1
    raise ValueError(f"unknown access pack version: {version!r}")


def access_pack_fingerprint(pack: AccessPack | None = None) -> str:
    """A content fingerprint of the pack: sha256 over its
    canonical JSON. Two packs with the same version string and
    different content fingerprint differently."""
    pack = pack or ACCESS_PACK_V1
    return hashlib.sha256(
        canonical_json(pack.model_dump(mode="json"))
    ).hexdigest()


class AccessDenial(BaseModel):
    """One recorded refusal. Insert-only: a denial is history,
    never state to rewrite. `target` is the identifier the
    requester supplied (an incident id, an order id, an act
    name) — for a hidden incident it is the requested string
    and nothing in the record says whether the incident
    exists."""

    denial_id: str
    seq: int  # the log's append order (the count at write time)
    principal_id: str
    role: str
    surface: str
    target: str
    reason: str
    detail: str
    pack_version: str
    denied_at: datetime


class AccessControl:
    """The guard, bound over the store's denial log. The wiring
    holds one and binds it onto the act-recording services; the
    surfaces reach it through `svc.access`. Every check that
    refuses records its denial BEFORE raising, through the one
    `deny` path — a refusal that is not on the log does not
    exist as far as this layer is concerned."""

    def __init__(self, store, pack: AccessPack | None = None):
        self._store = store
        self.pack = pack or load_access_pack()

    # -- authentication ---------------------------------------------
    def authenticate(
        self,
        raw: Mapping[str, Any] | None,
        *,
        surface: str,
        target: str = "",
        at: datetime | None = None,
    ) -> Principal:
        """Validate a carried principal against the pack. A
        missing pair, a malformed pair and an unknown role are
        each refused with their named reason — there is no
        default principal and no silent downgrade. The pair is
        judged AS CARRIED: the role vocabulary is exact strings
        and an id is the caller's own identifier, not a string
        to normalise into validity."""
        if raw is None:
            self.deny(
                principal_id="",
                role="",
                surface=surface,
                target=target,
                reason=REASON_PRINCIPAL_REQUIRED,
                detail=(
                    "no principal on the call — a principal (id + role)"
                    " is required; there is no default principal"
                ),
                at=at,
            )
        principal_id = raw.get("id") if isinstance(raw, Mapping) else None
        role = raw.get("role") if isinstance(raw, Mapping) else None
        if (
            not isinstance(principal_id, str)
            or not principal_id.strip()
            or principal_id != principal_id.strip()
            or not isinstance(role, str)
            or not role.strip()
            or role != role.strip()
        ):
            self.deny(
                principal_id=str(principal_id or ""),
                role=str(role or ""),
                surface=surface,
                target=target,
                reason=REASON_MALFORMED_PRINCIPAL,
                detail=(
                    "a principal is an (id, role) pair with both parts"
                    " named exactly — no surrounding whitespace"
                ),
                at=at,
            )
        if self.pack.grant(role) is None:
            self.deny(
                principal_id=principal_id,
                role=role,
                surface=surface,
                target=target,
                reason=REASON_UNKNOWN_ROLE,
                detail=(
                    f"unknown role {role!r} — access pack"
                    f" {self.pack.version} knows: {', '.join(ROLES)}"
                ),
                at=at,
            )
        return Principal(id=principal_id, role=role)

    # -- authorisation ------------------------------------------------
    def require_read(
        self,
        principal: Principal,
        surface: str,
        *,
        target: str = "",
        at: datetime | None = None,
    ) -> None:
        if not self.pack.can_read(principal.role, surface):
            self.deny(
                principal_id=principal.id,
                role=principal.role,
                surface=surface,
                target=target,
                reason=REASON_SURFACE_NOT_READABLE,
                detail=(
                    f"surface {surface} is not readable by role"
                    f" {principal.role}"
                ),
                at=at,
            )

    def require_incident_read(
        self,
        principal: Principal,
        surface: str,
        *,
        incident_id: str,
        at: datetime | None = None,
    ) -> None:
        """An incident-scoped read. Refused as
        INCIDENT_NOT_VISIBLE — hidden — whenever the role may
        not read incidents at all or may not read this surface:
        the surface answers it exactly as it answers a
        non-existent incident. The denial is still recorded
        (the log is not an incident surface; it names only the
        id the requester supplied)."""
        if not self.pack.can_read(
            principal.role, SURFACE_INCIDENT
        ) or not self.pack.can_read(principal.role, surface):
            self.deny(
                principal_id=principal.id,
                role=principal.role,
                surface=surface,
                target=incident_id,
                reason=REASON_INCIDENT_NOT_VISIBLE,
                detail=(
                    f"incident reads are not in role"
                    f" {principal.role}'s scope (surface {surface})"
                    " — answered as a non-existent incident"
                ),
                at=at,
                hidden=True,
            )

    def require_act(
        self,
        principal: Principal,
        act: str,
        *,
        surface: str | None = None,
        target: str = "",
        at: datetime | None = None,
    ) -> None:
        surface = surface or ACT_SURFACES[act]
        if self.pack.can_act(principal.role, act):
            return
        if principal.role == ROLE_AUDITOR:
            self.deny(
                principal_id=principal.id,
                role=principal.role,
                surface=surface,
                target=target,
                reason=REASON_READ_ONLY_ROLE,
                detail=(
                    f"role {ROLE_AUDITOR} is read-only: it performs"
                    f" no acts (attempted act: {act})"
                ),
                at=at,
            )
        required = self.pack.required_roles(act)
        self.deny(
            principal_id=principal.id,
            role=principal.role,
            surface=surface,
            target=target,
            reason=REASON_ACT_NOT_PERMITTED,
            detail=(
                f"act {act} requires role"
                f" {' or '.join(required) if required else '(none in the pack)'};"
                f" principal {principal.id} holds {principal.role}"
            ),
            at=at,
        )

    def require_actor(
        self,
        principal: Principal,
        actor: str,
        *,
        act: str,
        surface: str | None = None,
        target: str = "",
        at: datetime | None = None,
    ) -> None:
        """A carried principal binds the act's named human: the
        principal's id must be the actor the act names, compared
        under the repo's name folding. The assembler's own
        drafting (actor ``system``) names no human and is not
        id-bound — the act permission governs it."""
        if actor == SYSTEM_ACTOR:
            return
        if fold_name(principal.id) == fold_name(actor or ""):
            return
        self.deny(
            principal_id=principal.id,
            role=principal.role,
            surface=surface or ACT_SURFACES[act],
            target=target,
            reason=REASON_ACTOR_MISMATCH,
            detail=(
                f"act {act} names {actor!r} as its human; principal"
                f" {principal.id} may not sign another person's act"
            ),
            at=at,
        )

    def require_role_match(
        self,
        principal: Principal,
        act_role: str,
        *,
        act: str,
        surface: str | None = None,
        target: str = "",
        at: datetime | None = None,
    ) -> None:
        """Where an act carries a role, a carried principal's
        role must be that role: holding an act's permission does
        not let a caller record the act under a different role
        than their own."""
        if principal.role == act_role:
            return
        self.deny(
            principal_id=principal.id,
            role=principal.role,
            surface=surface or ACT_SURFACES[act],
            target=target,
            reason=REASON_ROLE_MISMATCH,
            detail=(
                f"act {act} is recorded under role {act_role!r};"
                f" principal {principal.id} holds"
                f" {principal.role} and may not record an act"
                " under another role"
            ),
            at=at,
        )

    # -- the one recording path ---------------------------------------
    def deny(
        self,
        *,
        principal_id: str,
        role: str,
        surface: str,
        target: str,
        reason: str,
        detail: str,
        at: datetime | None = None,
        hidden: bool = False,
    ) -> None:
        """Record the denial, then raise it. Always raises."""
        denied_at = at or datetime.now(timezone.utc)
        seq = self._store.count_access_denials()
        blob = canonical_json(
            {
                "seq": seq,
                "principal_id": principal_id,
                "role": role,
                "surface": surface,
                "target": target,
                "reason": reason,
                "denied_at": denied_at.isoformat(),
                "pack_version": self.pack.version,
            }
        )
        denial = AccessDenial(
            denial_id="AD-" + hashlib.sha256(blob).hexdigest()[:16],
            seq=seq,
            principal_id=principal_id,
            role=role,
            surface=surface,
            target=target,
            reason=reason,
            detail=detail,
            pack_version=self.pack.version,
            denied_at=denied_at,
        )
        self._store.add_access_denial(denial)
        raise AccessDeniedError(
            reason, f"access denied: {detail}", hidden=hidden
        )

    def denials(self) -> list[AccessDenial]:
        return self._store.list_access_denials()


def control_for(service) -> AccessControl:
    """The access control a service judges with: the one the
    wiring bound onto it, or — for a service constructed bare,
    as some unit tests do — one built over its store on first
    use and kept. Either way the denial log is the service's
    own store's."""
    control = getattr(service, "access", None)
    if control is None:
        control = AccessControl(service.store)
        service.access = control
    return control


def guard_act(
    service,
    principal: Principal | None,
    act: str,
    *,
    target: str = "",
    actor: str | None = None,
    act_role: str | None = None,
    at: datetime | None = None,
) -> None:
    """The one-line judge the act-recording services run when a
    call carries a principal: the role must hold the act, the
    principal must be the human the act names (where it names
    one), and the principal's role must be the role the act is
    recorded under (where it carries one). A principal-less call
    is the trusted local path and passes untouched."""
    if principal is None:
        return
    control = control_for(service)
    control.require_act(principal, act, target=target, at=at)
    if actor is not None:
        control.require_actor(
            principal, actor, act=act, target=target, at=at
        )
    if act_role is not None:
        control.require_role_match(
            principal, act_role, act=act, target=target, at=at
        )
