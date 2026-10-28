"""Storm mode — Round 8, Law 8: storm mode is a declared state.

The design brief's eighth law says the system declares storm
mode above a computed threshold. This round keeps the declared
state and drops the automation: **nothing enters storm mode on
its own**. Alarm volume, incident rate, a bad night — none of it
flips the state. Storm mode exists only as named operator acts:

- ``declare`` opens a storm window, naming the operator, the
  stated reason and the affected scope (a feeder list; empty
  means the whole territory, plus an optional region label);
- ``extend`` re-states an open window's scope or reason from
  that moment on;
- ``stand_down`` closes the window, again as a named act with a
  stated reason.

The acts are insert-only records whose ids are content hashes,
linked by ``supersedes`` into one chain per window — the same
evidence discipline as the per-incident hash chains, applied to
a territory-level act. The storm state at any moment **is the
fold of those acts**: a read names the declaration that put the
state there, and a window's earlier hours keep the scope the
fold gives them at that hour (an extension widens the future,
never the past).

What storm mode changes is stated once, in the versioned storm
pack below, with the normal-mode and storm-mode values side by
side. Nothing changes that the pack does not name:

- the correlation window widens (600s → 1800s), and
  ``meter_ping_failure`` — excluded in normal mode — may *join*
  a storm incident (AMI silence during a storm is outage
  evidence), though it still never anchors one;
- severity's duration and breadth terms take the pack's storm
  values (elapsed time escalates more slowly when everything is
  slow; alarm breadth counts for more);
- an ETR computed under storm mode scales its base repair
  minutes by the pack's stated factor, recorded as its own
  component line.

An incident correlated while storm mode covers its feeder is
stamped on its face — mode, storm pack version, declaration id —
at creation, and the stamp is never rewritten: standing the
storm down does not relabel history, and work queued under
storm parameters stays as created. The accuracy ledger records
the mode on every outcome so storm performance is reported
separately from normal performance, and after stand-down a
**post-storm review** artefact (see :meth:`StormService.
compute_review`) assembles the window's record from artefacts
only.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from grid_triage.evidence import canonical_json
from grid_triage.models import StormDeclaration, StormReview

STORM_PACK = {
    "version": "2026.10.1",
    # The control-room roles that may declare, extend or stand
    # down a storm window — the same vocabulary the work pack
    # uses for approvals.
    "actor_roles": ["operator", "duty_manager"],
    # Correlation under storm mode. The normal values restate
    # the correlation pack's, side by side with the storm
    # values — the drift between this table and the live packs
    # is graded by the tests, so the side-by-side can never
    # quietly stop describing the system.
    "correlation": {
        "window_seconds": {"normal": 600, "storm": 1800},
        # Excluded kinds that may join (never anchor) an
        # incident while its feeder is under storm mode.
        "joinable_in_storm": ["meter_ping_failure"],
    },
    # Severity under storm mode: the four parameters that
    # change, normal and storm side by side. Every other
    # severity parameter is untouched by the mode.
    "severity": {
        "minutes_per_duration_point": {"normal": 30, "storm": 60},
        "duration_points_cap": {"normal": 10, "storm": 6},
        "alarms_per_breadth_point": {"normal": 10, "storm": 5},
        "breadth_points_cap": {"normal": 5, "storm": 8},
    },
    # ETR under storm mode: base repair minutes scale by this
    # fraction (crews work slower in storm conditions), and the
    # added minutes are recorded as a component line with this
    # name — the adjustment is on the estimate's face, never
    # folded silently into the repair line.
    "etr": {
        "base_repair_factor": {"numerator": 6, "denominator": 5},
        "component_name": "storm_conditions",
    },
}

DECLARE = "declare"
EXTEND = "extend"
STAND_DOWN = "stand_down"


def _hash_id(prefix: str, body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"{prefix}-{digest[:16]}"


class StormRefusal(RuntimeError):
    """A storm act, or a review, that the record does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


# -- the pack's effects, as pure helpers ------------------------------


def storm_window_seconds(pack: dict = STORM_PACK) -> int:
    return int(pack["correlation"]["window_seconds"]["storm"])


def storm_joinable_kinds(pack: dict = STORM_PACK) -> set[str]:
    return set(pack["correlation"]["joinable_in_storm"])


def storm_severity_pack(base_pack: dict, pack: dict = STORM_PACK) -> dict:
    """The severity pack with the storm pack's stated overrides
    applied. Only the parameters the storm pack names change;
    the merged pack keeps the severity pack's own version — the
    storm labelling travels on the assessment's inputs."""
    merged = dict(base_pack)
    for key, pair in pack["severity"].items():
        merged[key] = pair["storm"]
    return merged


def severity_adjustments(pack: dict = STORM_PACK) -> dict:
    """The severity parameters storm mode changes, normal and
    storm values side by side — the record every storm
    assessment carries."""
    return {
        key: {"normal": pair["normal"], "storm": pair["storm"]}
        for key, pair in pack["severity"].items()
    }


def storm_base_repair(base_minutes: int, pack: dict = STORM_PACK) -> int:
    """Base repair minutes under storm mode: the stated factor
    applied in integer arithmetic (the rate pack's bases are
    multiples of the denominator, so nothing is rounded)."""
    factor = pack["etr"]["base_repair_factor"]
    return base_minutes * int(factor["numerator"]) // int(factor["denominator"])


# -- the service --------------------------------------------------------


class StormService:
    """Store-facing storm work: the declaration lifecycle, the
    state fold every other service reads, the incident stamp,
    and (after stand-down) the post-storm review."""

    def __init__(self, store, pack: dict = STORM_PACK):
        self.store = store
        self.pack = pack

    # -- reads over the act record ------------------------------------
    def declarations(self) -> list[StormDeclaration]:
        return self.store.list_storm_declarations()

    def get_declaration(self, declaration_id: str) -> StormDeclaration | None:
        for act in self.store.list_storm_declarations():
            if act.declaration_id == declaration_id:
                return act
        return None

    def _windows(self) -> list[dict]:
        """Group the acts into windows: one declare plus the
        extends and stand-down that name it, in act order."""
        windows: dict[str, dict] = {}
        order: list[str] = []
        for act in self.store.list_storm_declarations():
            if act.kind == DECLARE:
                windows[act.declaration_id] = {
                    "declare": act,
                    "acts": [act],
                    "stand_down": None,
                }
                order.append(act.declaration_id)
            elif act.opens_declaration_id in windows:
                window = windows[act.opens_declaration_id]
                window["acts"].append(act)
                if act.kind == STAND_DOWN:
                    window["stand_down"] = act
        return [windows[key] for key in order]

    def window_acts(self, declaration_id: str) -> list[StormDeclaration]:
        """Every act of one window, oldest first — the chain a
        review or an auditor reads."""
        return [
            act
            for act in self.store.list_storm_declarations()
            if act.declaration_id == declaration_id
            or act.opens_declaration_id == declaration_id
        ]

    def _fold_at(self, window: dict, at: datetime) -> StormDeclaration | None:
        """The window's head act at ``at`` — the latest act whose
        declared time is not after ``at`` — or None when the
        window has not opened yet or has already stood down."""
        head: StormDeclaration | None = None
        # Act order is record order; the sort by declared time is
        # stable, so acts sharing a timestamp fold in the order
        # they were recorded.
        for act in sorted(window["acts"], key=lambda a: a.declared_at):
            if act.declared_at > at:
                break
            if act.kind == STAND_DOWN:
                return None
            head = act
        return head

    def _open_window_at(self, window: dict, at: datetime) -> bool:
        return self._fold_at(window, at) is not None

    # -- the state fold --------------------------------------------------
    def state_at(self, feeder_id: str, at: datetime) -> dict:
        """The operating state for one feeder at one moment,
        folded from the declaration acts. A storm answer names
        the declare act that opened the window and the head act
        that currently states its scope — the read never reports
        a state no act put there."""
        for window in self._windows():
            head = self._fold_at(window, at)
            if head is None:
                continue
            if head.feeders and feeder_id not in head.feeders:
                continue
            declare = window["declare"]
            return {
                "mode": "storm",
                "feeder_id": feeder_id,
                "at": at.isoformat(),
                "declaration_id": declare.declaration_id,
                "head_act_id": head.declaration_id,
                "pack_version": self.pack["version"],
                "declared_by": declare.declared_by,
                "declared_at": declare.declared_at.isoformat(),
                "feeders": list(head.feeders),
                "region": head.region,
                "reason": head.reason,
            }
        return {"mode": "normal", "feeder_id": feeder_id, "at": at.isoformat()}

    def active_windows(self, at: datetime) -> list[dict]:
        """Every storm window open at ``at``, folded from the
        acts: the declaration that opened it, the head act that
        currently states its scope, and that scope. The
        territory-level read the operations board and the shift
        handover assemble from (Round 9) — the same fold
        :meth:`state_at` reads per feeder, never a second
        mechanism."""
        windows: list[dict] = []
        for window in self._windows():
            head = self._fold_at(window, at)
            if head is None:
                continue
            declare = window["declare"]
            windows.append(
                {
                    "declaration_id": declare.declaration_id,
                    "head_act_id": head.declaration_id,
                    "declared_by": declare.declared_by,
                    "declared_at": declare.declared_at.isoformat(),
                    "pack_version": self.pack["version"],
                    "feeders": list(head.feeders),
                    "region": head.region,
                    "reason": head.reason,
                }
            )
        return windows

    def stamp_for(self, feeder_id: str, at: datetime) -> dict:
        """The incident-stamp fields for an incident arising on
        ``feeder_id`` at ``at`` — the mode in force, and under
        storm mode the pack version and the declaration that put
        it there."""
        state = self.state_at(feeder_id, at)
        if state["mode"] != "storm":
            return {
                "operating_mode": "normal",
                "storm_pack_version": None,
                "storm_declaration_id": None,
            }
        return {
            "operating_mode": "storm",
            "storm_pack_version": state["pack_version"],
            "storm_declaration_id": state["declaration_id"],
        }

    def lookup(self, feeder_id: str, at: datetime) -> dict | None:
        """The correlation pack's view of the fold: the stamp for
        an anchor on ``feeder_id`` at ``at``, or None in normal
        mode."""
        stamp = self.stamp_for(feeder_id, at)
        if stamp["operating_mode"] != "storm":
            return None
        return {
            "declaration_id": stamp["storm_declaration_id"],
            "pack_version": stamp["storm_pack_version"],
        }

    # -- the acts ---------------------------------------------------------
    def _actor_problems(
        self, declared_by: str, role: str, reason: str, verb: str
    ) -> list[str]:
        problems: list[str] = []
        if not declared_by or not declared_by.strip():
            problems.append(f"a storm {verb} names the operator performing it")
        if role not in self.pack["actor_roles"]:
            problems.append(
                f"role {role!r} may not {verb} storm mode (allowed: "
                f"{', '.join(self.pack['actor_roles'])})"
            )
        if not reason or not reason.strip():
            problems.append(f"a storm {verb} states its reason")
        return problems

    @staticmethod
    def _scopes_overlap(
        a_feeders: list[str], b_feeders: list[str]
    ) -> bool:
        # An empty feeder list is the whole territory: it
        # overlaps every scope, including another empty one.
        if not a_feeders or not b_feeders:
            return True
        return bool(set(a_feeders) & set(b_feeders))

    def declare(
        self,
        declared_by: str,
        role: str,
        reason: str,
        declared_at: datetime,
        feeders: list[str] | None = None,
        region: str | None = None,
    ) -> StormDeclaration:
        feeders = list(feeders or [])
        problems = self._actor_problems(declared_by, role, reason, "declaration")
        for window in self._windows():
            if not self._open_window_at(window, declared_at):
                continue
            head = self._fold_at(window, declared_at)
            assert head is not None
            if self._scopes_overlap(feeders, head.feeders):
                problems.append(
                    f"storm mode is already declared over this scope by "
                    f"{window['declare'].declared_by} (declaration "
                    f"{window['declare'].declaration_id}) — extend that "
                    "declaration or stand it down first"
                )
        if problems:
            raise StormRefusal(problems)
        body = {
            "kind": DECLARE,
            "pack_version": self.pack["version"],
            "declared_by": declared_by,
            "role": role,
            "reason": reason,
            "feeders": feeders,
            "region": region,
            "declared_at": declared_at.isoformat(),
            "opens_declaration_id": None,
            "supersedes": None,
        }
        declaration = StormDeclaration(
            declaration_id=_hash_id("STORM", body), **body
        )
        self.store.add_storm_declaration(declaration)
        return declaration

    def _window_for_act(
        self, declaration_id: str, verb: str
    ) -> tuple[dict, list[str]]:
        """Resolve the window an extend / stand-down names, with
        the problems that make the act impossible."""
        problems: list[str] = []
        target = self.get_declaration(declaration_id)
        if target is None:
            problems.append(f"no such storm declaration: {declaration_id}")
            return {}, problems
        if target.kind != DECLARE:
            problems.append(
                f"act {declaration_id} is a {target.kind} — a storm {verb} "
                "names the declaration that opened the window"
            )
            return {}, problems
        for window in self._windows():
            if window["declare"].declaration_id == declaration_id:
                return window, problems
        return {}, problems

    def extend(
        self,
        declaration_id: str,
        declared_by: str,
        role: str,
        reason: str,
        declared_at: datetime,
        feeders: list[str] | None = None,
        region: str | None = None,
    ) -> StormDeclaration:
        problems = self._actor_problems(declared_by, role, reason, "extension")
        window, found = self._window_for_act(declaration_id, "extension")
        problems.extend(found)
        head: StormDeclaration | None = None
        if window:
            if window["stand_down"] is not None and (
                window["stand_down"].declared_at <= declared_at
            ):
                problems.append(
                    f"storm window {declaration_id} was stood down at "
                    f"{window['stand_down'].declared_at.isoformat()} — a "
                    "closed window is not extended; declare a new one"
                )
            latest = max(a.declared_at for a in window["acts"])
            if declared_at < latest:
                problems.append(
                    "an extension is recorded in the window's act order — "
                    f"its time {declared_at.isoformat()} precedes the "
                    f"window's latest act at {latest.isoformat()}"
                )
            head = self._fold_at(window, declared_at)
            if head is None and not problems:
                problems.append(
                    f"storm window {declaration_id} is not open at "
                    f"{declared_at.isoformat()}"
                )
        if problems:
            raise StormRefusal(problems)
        assert head is not None
        body = {
            "kind": EXTEND,
            "pack_version": self.pack["version"],
            "declared_by": declared_by,
            "role": role,
            "reason": reason,
            "feeders": list(feeders) if feeders is not None else list(head.feeders),
            "region": region if region is not None else head.region,
            "declared_at": declared_at.isoformat(),
            "opens_declaration_id": declaration_id,
            "supersedes": head.declaration_id,
        }
        act = StormDeclaration(
            declaration_id=_hash_id("STORM", body), **body
        )
        self.store.add_storm_declaration(act)
        return act

    def stand_down(
        self,
        declaration_id: str,
        declared_by: str,
        role: str,
        reason: str,
        declared_at: datetime,
    ) -> StormDeclaration:
        problems = self._actor_problems(declared_by, role, reason, "stand-down")
        window, found = self._window_for_act(declaration_id, "stand-down")
        problems.extend(found)
        head: StormDeclaration | None = None
        if window:
            if window["stand_down"] is not None:
                problems.append(
                    f"storm window {declaration_id} is already stood down "
                    f"(act {window['stand_down'].declaration_id})"
                )
            latest = max(a.declared_at for a in window["acts"])
            if declared_at < latest:
                problems.append(
                    "a stand-down is recorded in the window's act order — "
                    f"its time {declared_at.isoformat()} precedes the "
                    f"window's latest act at {latest.isoformat()}"
                )
            head = self._fold_at(window, declared_at)
            if head is None and not problems:
                problems.append(
                    f"storm window {declaration_id} is not open at "
                    f"{declared_at.isoformat()}"
                )
        if problems:
            raise StormRefusal(problems)
        assert head is not None
        body = {
            "kind": STAND_DOWN,
            "pack_version": self.pack["version"],
            "declared_by": declared_by,
            "role": role,
            "reason": reason,
            "feeders": list(head.feeders),
            "region": head.region,
            "declared_at": declared_at.isoformat(),
            "opens_declaration_id": declaration_id,
            "supersedes": head.declaration_id,
        }
        act = StormDeclaration(
            declaration_id=_hash_id("STORM", body), **body
        )
        self.store.add_storm_declaration(act)
        return act

    # -- the post-storm review -------------------------------------------
    def _review_figures(self, declaration_id: str) -> tuple[list[str], dict]:
        """The review's whole computation, from artefacts only:
        the incidents stamped with the declaration, their current
        severity bands, their closures and their ETR outcomes.
        Shared by compute and verify so the verifier re-performs
        exactly what the computer performed."""
        incidents = [
            i
            for i in self.store.list_incidents()
            if i.storm_declaration_id == declaration_id
        ]
        by_band: dict[str, int] = {}
        closed = 0
        for incident in incidents:
            history = self.store.list_severity_assessments(incident.incident_id)
            band = history[-1].band if history else "UNASSESSED"
            by_band[band] = by_band.get(band, 0) + 1
            if self.store.get_incident_closure(incident.incident_id) is not None:
                closed += 1
        outcomes = [
            o
            for o in self.store.list_etr_outcomes()
            if o.storm_declaration_id == declaration_id
        ]
        compared = [o for o in outcomes if o.status == "compared"]
        signed = [o.signed_error_minutes for o in compared]
        absolute = [o.absolute_error_minutes for o in compared]
        figures = {
            "incident_count": len(incidents),
            "incidents_by_band": dict(sorted(by_band.items())),
            "closed_incident_count": closed,
            "etr_compared_count": len(compared),
            "etr_unestimated_closures": len(outcomes) - len(compared),
            "etr_mean_signed_error_minutes": (
                round(sum(signed) / len(signed), 3) if signed else None
            ),
            "etr_mean_absolute_error_minutes": (
                round(sum(absolute) / len(absolute), 3) if absolute else None
            ),
            "etr_max_absolute_error_minutes": max(absolute) if absolute else None,
        }
        return sorted(i.incident_id for i in incidents), figures

    def compute_review(
        self, declaration_id: str, computed_by: str, computed_at: datetime
    ) -> StormReview:
        """Assemble the post-storm review of one window. Only
        after stand-down: a review of a window still open would
        grade a record that is still being written."""
        problems: list[str] = []
        if not computed_by or not computed_by.strip():
            problems.append("a post-storm review names who computes it")
        window, found = self._window_for_act(declaration_id, "review")
        problems.extend(found)
        if window and window["stand_down"] is None:
            problems.append(
                f"storm window {declaration_id} is still open — the "
                "review is computed after stand-down, from the "
                "window's closed record"
            )
        if problems:
            raise StormRefusal(problems)
        declare = window["declare"]
        stand_down = window["stand_down"]
        assert stand_down is not None
        incident_ids, figures = self._review_figures(declaration_id)
        body = {
            "declaration_id": declaration_id,
            "pack_version": self.pack["version"],
            "supersedes": None,
            "declared_at": declare.declared_at.isoformat(),
            "stood_down_at": stand_down.declared_at.isoformat(),
            "feeders": list(stand_down.feeders),
            "region": stand_down.region,
            "incident_ids": incident_ids,
            "figures": figures,
            "computed_by": computed_by,
            "computed_at": computed_at.isoformat(),
        }
        prior = self.review_for(declaration_id)
        if prior is not None:
            # Identical recomputation against the same head: a
            # no-op, not a new link (the severity discipline).
            link_free = {"review_id", "supersedes"}
            candidate = StormReview(review_id="STORMREV-candidate", **body)
            if candidate.model_dump(exclude=link_free) == prior.model_dump(
                exclude=link_free
            ):
                return prior
            body["supersedes"] = prior.review_id
        review = StormReview(review_id=_hash_id("STORMREV", body), **body)
        self.store.add_storm_review(review)
        return review

    def review_for(self, declaration_id: str) -> StormReview | None:
        history = self.store.list_storm_reviews(declaration_id)
        return history[-1] if history else None

    def reviews(self, declaration_id: str) -> list[StormReview]:
        return self.store.list_storm_reviews(declaration_id)

    def get_review(self, review_id: str) -> StormReview | None:
        return self.store.get_storm_review(review_id)

    def verify_review(self, review_id: str) -> list[str]:
        """Re-perform a review from the store and name every
        drift. Empty means the review recomputes exactly."""
        review = self.store.get_storm_review(review_id)
        if review is None:
            raise KeyError(f"no such storm review: {review_id}")
        problems: list[str] = []
        declare = self.get_declaration(review.declaration_id)
        if declare is None or declare.kind != DECLARE:
            problems.append(
                f"the review's declaration {review.declaration_id} is not on record"
            )
            return problems
        window, _ = self._window_for_act(review.declaration_id, "review")
        stand_down = window["stand_down"] if window else None
        if stand_down is None:
            problems.append("the review's window has no stand-down act on record")
            return problems
        if review.declared_at != declare.declared_at:
            problems.append("the review's window start does not recompute")
        if review.stood_down_at != stand_down.declared_at:
            problems.append("the review's window end does not recompute")
        if review.feeders != stand_down.feeders or review.region != stand_down.region:
            problems.append("the review's window scope does not recompute")
        incident_ids, figures = self._review_figures(review.declaration_id)
        if review.incident_ids != incident_ids:
            problems.append("the review's incident set does not recompute")
        if review.figures != figures:
            problems.append("the review's figures do not recompute")
        return problems
