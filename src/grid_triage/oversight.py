"""Operations oversight reporting — Round 12.

Rounds 1–11 built the working record and the surfaces that
read it live: the board, the console, the handover. What the
record did not have was the artefact management reads later —
a periodic statement of how the operation performed over a
stated window, that can be re-performed figure for figure and
that still says the same thing next quarter. The sibling
blueprints grew that artefact in their later rounds; this is
the grid's, over its own artefacts of record:

- **incident volumes by state** — incidents founded in the
  window, counted once each as superseded, closed or
  live-open (in that precedence, so the states sum to the
  total exactly), with the closure rate;
- **severity mix** — the same incidents by their head severity
  band of record, the unassessed named as their own row;
- **ETR accuracy** — the closure outcomes recorded in the
  window, in the accuracy ledger's own statistics shape,
  overall and by operating mode. Storm and normal are
  separate rows, never blended — the ledger's rule, kept;
- **restoration outcomes** — confirmations computed in the
  window by status, the signals observed, the post-close
  contradictions recorded, and the completion rate;
- **storm activity** — declaration acts in the window by
  kind, and the windows standing open at the window's end;
- **handover position** — handovers assembled and acceptances
  recorded in the window, and whether the position at the
  window's end stood accepted.

Discipline, stated once and graded:

- **Artefacts only, aggregate only.** Every figure is a count
  or a statistic over stored artefacts. No section names an
  incident, an alarm or a person — the report is position and
  proportion, never a case file — and a figure the artefacts
  do not carry does not appear.
- **Rates carry their population.** Every rate is
  ``{value, numerator, denominator}``; over an empty
  population the value is ``None`` and the section's
  ``population`` reads ``no_data`` — a rate over nothing is
  never printed as 0% or 100%.
- **Insert-only and content-addressed.** A report is
  fingerprinted over its pack version, its window and its
  sections. Re-generating over a record that has not moved
  returns the standing artefact; over a moved record it
  produces the next artefact for that window, superseding by
  link. A stored report is never edited.
- **Verification is a second path.** ``verify_report``
  re-performs every figure with its own walk over the store
  and names the sections that do not recompute; a planted
  tamper is named, not averaged away. ``scripts/check_reports.py``
  runs it over every stored report.
- **The act is judged like every act here.** Generation is a
  named operator or duty manager act under the access pack
  (Round 11): a carried principal must hold the
  ``generate_report`` act and be the human it names; the
  auditor reads reports and generates none, and its refusal
  is recorded like any other. Reads sit on the pack's
  ``oversight`` surface.
- **The artefact says what it is.** An internal oversight
  artefact over aggregate operational figures — not a
  regulatory filing, not an external report.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from grid_triage.access import (
    ACT_GENERATE_REPORT,
    Principal,
    guard_act,
)
from grid_triage.board import _superseded_ids, is_closed
from grid_triage.evidence import canonical_json
from grid_triage.models import OversightReport
from grid_triage.storm import StormService

OVERSIGHT_PACK = {
    "version": "2026.10.1",
    # The control-room roles that may generate a report — the
    # shift's own authorities. The auditor reads; it does not
    # generate (the access pack enforces the same line).
    "actor_roles": ["operator", "duty_manager"],
    "sections": [
        "incident_volumes",
        "severity_mix",
        "etr_accuracy",
        "restoration_outcomes",
        "storm_activity",
        "handover_position",
    ],
}

BASIS = (
    "internal oversight artefact — aggregate operational figures "
    "computed from artefacts of record; it names no incident and "
    "is not a regulatory filing or an external report"
)


def _hash_id(prefix: str, body: dict) -> str:
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    return f"{prefix}-{digest[:16]}"


def _rate(numerator: int, denominator: int) -> dict:
    """A rate with its population on its face. Over an empty
    population the value is None — never 0, never 1."""
    return {
        "value": round(numerator / denominator, 4) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def _population(*denominators: int) -> str:
    return "ok" if any(denominators) else "no_data"


def fingerprint_of(pack_version: str, window_start, window_end, sections) -> str:
    """The record fingerprint: over the pack, the window and
    the sections exactly as stored, canonicalised."""
    return hashlib.sha256(
        canonical_json(
            {
                "pack_version": pack_version,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "sections": sections,
            }
        )
    ).hexdigest()


class OversightRefusal(RuntimeError):
    """A report act the record does not allow."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class OversightService:
    """Store-facing oversight work: the generation act, the
    reads, and the independent verifier."""

    def __init__(self, store, storm: StormService, pack: dict = OVERSIGHT_PACK):
        self.store = store
        self.storm = storm
        self.pack = pack

    # -- reads ------------------------------------------------------
    def reports(self) -> list[OversightReport]:
        return self.store.list_oversight_reports()

    def get(self, report_id: str) -> OversightReport | None:
        return self.store.get_oversight_report(report_id)

    def head_for_window(
        self, window_start: datetime, window_end: datetime
    ) -> OversightReport | None:
        """The standing report for a window: the latest
        generated over exactly that window, if any."""
        head = None
        for report in self.store.list_oversight_reports():
            if (
                report.window_start == window_start
                and report.window_end == window_end
            ):
                head = report
        return head

    # -- the generation act ------------------------------------------
    def generate(
        self,
        generated_by: str,
        role: str,
        window_start: datetime,
        window_end: datetime,
        generated_at: datetime,
        principal: Principal | None = None,
    ) -> OversightReport:
        """Freeze the window's oversight figures into a report
        artefact. The window is half-open [start, end) over the
        artefacts' own timestamps and may not reach past the
        generation moment — a report over a window that has not
        finished happening is not an oversight artefact."""
        guard_act(
            self,
            principal,
            ACT_GENERATE_REPORT,
            actor=generated_by,
            act_role=role,
            at=generated_at,
        )
        problems: list[str] = []
        if not generated_by or not generated_by.strip():
            problems.append("a report generation names the operator performing it")
        if role not in self.pack["actor_roles"]:
            problems.append(
                f"role {role!r} may not generate an oversight report "
                f"(allowed: {', '.join(self.pack['actor_roles'])})"
            )
        if not window_start < window_end:
            problems.append("the report window must start before it ends")
        if window_end > generated_at:
            problems.append(
                "the report window ends after the generation moment — "
                "a report covers a window that has finished happening"
            )
        if problems:
            raise OversightRefusal(problems)
        sections = compute_sections(
            self.store, self.storm, window_start, window_end
        )
        fingerprint = fingerprint_of(
            self.pack["version"], window_start, window_end, sections
        )
        head = self.head_for_window(window_start, window_end)
        if head is not None and head.fingerprint == fingerprint:
            return head  # the record has not moved: a no-op
        body = {
            "pack_version": self.pack["version"],
            "generated_by": generated_by,
            "role": role,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "generated_at": generated_at.isoformat(),
            "supersedes": head.report_id if head else None,
            "sections": sections,
            "fingerprint": fingerprint,
        }
        report = OversightReport(
            report_id=_hash_id("REPORT", body), **body
        )
        self.store.add_oversight_report(report)
        return report

    # -- verification --------------------------------------------------
    def verify_report(self, report_id: str) -> list[str]:
        """Re-perform a stored report figure for figure by the
        independent path and name every drift. Empty means the
        artefact still recomputes exactly."""
        report = self.store.get_oversight_report(report_id)
        if report is None:
            raise KeyError(f"no such oversight report: {report_id}")
        problems: list[str] = []
        if (
            fingerprint_of(
                report.pack_version,
                report.window_start,
                report.window_end,
                report.sections,
            )
            != report.fingerprint
        ):
            problems.append(
                "the stored fingerprint does not cover the stored sections"
            )
        recomputed = reperform_sections(
            self.store, self.storm, report.window_start, report.window_end
        )
        for name in self.pack["sections"]:
            if recomputed.get(name) != report.sections.get(name):
                problems.append(f"the {name} section does not recompute")
        return problems

    def view(self, report: OversightReport) -> dict:
        body = report.model_dump(mode="json")
        body["basis"] = BASIS
        return body


# ---------------------------------------------------------------------
# The generation path: figures over the artefacts, via the board's
# own state definitions where the board has already stated them.
# ---------------------------------------------------------------------


def _outcome_stats(outcomes: list) -> dict:
    compared = [o for o in outcomes if o.status == "compared"]
    signed = [o.signed_error_minutes for o in compared]
    absolute = [o.absolute_error_minutes for o in compared]
    return {
        "compared_count": len(compared),
        "mean_signed_error_minutes": (
            round(sum(signed) / len(signed), 3) if signed else None
        ),
        "mean_absolute_error_minutes": (
            round(sum(absolute) / len(absolute), 3) if absolute else None
        ),
        "max_absolute_error_minutes": max(absolute) if absolute else None,
    }


def compute_sections(store, storm: StormService, start: datetime, end: datetime) -> dict:
    def in_window(moment: datetime) -> bool:
        return start <= moment < end

    founded = [i for i in store.list_incidents() if in_window(i.created_at)]
    superseded = _superseded_ids(store)
    by_state = {"live_open": 0, "closed": 0, "superseded": 0}
    for incident in founded:
        if incident.incident_id in superseded:
            by_state["superseded"] += 1
        elif is_closed(store, incident.incident_id):
            by_state["closed"] += 1
        else:
            by_state["live_open"] += 1
    total = len(founded)

    by_band: dict[str, int] = {}
    for incident in founded:
        history = store.list_severity_assessments(incident.incident_id)
        band = history[-1].band if history else "UNASSESSED"
        by_band[band] = by_band.get(band, 0) + 1

    outcomes = [
        o for o in store.list_etr_outcomes() if in_window(o.recorded_at)
    ]
    compared_total = sum(1 for o in outcomes if o.status == "compared")
    by_mode = {
        mode: _outcome_stats([o for o in outcomes if o.operating_mode == mode])
        for mode in ("normal", "storm")
    }

    confirmations = [
        c
        for c in _all_confirmations(store)
        if in_window(c.computed_at)
    ]
    by_status: dict[str, int] = {}
    for confirmation in confirmations:
        by_status[confirmation.status] = by_status.get(confirmation.status, 0) + 1
    signals = [
        s
        for incident in store.list_incidents()
        for s in store.list_restoration_signals(incident.incident_id)
        if in_window(s.observed_at)
    ]
    contradictions = [
        c
        for c in store.list_post_close_contradictions()
        if in_window(c.recorded_at)
    ]

    declarations = [
        d for d in store.list_storm_declarations() if in_window(d.declared_at)
    ]
    by_kind: dict[str, int] = {}
    for declaration in declarations:
        by_kind[declaration.kind] = by_kind.get(declaration.kind, 0) + 1

    handovers = [
        h for h in store.list_shift_handovers() if in_window(h.handed_over_at)
    ]
    acceptances = [
        a
        for a in store.list_handover_acceptances()
        if in_window(a.accepted_at)
    ]
    position = _handover_position_at(store, end)

    return {
        "incident_volumes": {
            "population": _population(total),
            "total": total,
            "by_state": by_state,
            "closure_rate": _rate(by_state["closed"], total),
        },
        "severity_mix": {
            "population": _population(total),
            "total": total,
            "by_band": dict(sorted(by_band.items())),
            "sev1_rate": _rate(by_band.get("SEV-1", 0), total),
        },
        "etr_accuracy": {
            "population": _population(len(outcomes)),
            "outcomes_recorded": len(outcomes),
            "unestimated_count": len(outcomes) - compared_total,
            "compared_rate": _rate(compared_total, len(outcomes)),
            "overall": _outcome_stats(outcomes),
            "by_operating_mode": by_mode,
        },
        "restoration_outcomes": {
            "population": _population(len(confirmations)),
            "confirmations": len(confirmations),
            "by_status": dict(sorted(by_status.items())),
            "signals_observed": len(signals),
            "post_close_contradictions": len(contradictions),
            "completion_rate": _rate(
                by_status.get("complete", 0), len(confirmations)
            ),
        },
        "storm_activity": {
            "population": _population(len(declarations)),
            "declarations": len(declarations),
            "by_kind": dict(sorted(by_kind.items())),
            "windows_open_at_window_end": len(storm.active_windows(end)),
        },
        "handover_position": {
            "population": _population(len(handovers), len(acceptances)),
            "handovers_assembled": len(handovers),
            "acceptances_recorded": len(acceptances),
            "position_at_window_end": position,
        },
    }


def _all_confirmations(store) -> list:
    out = []
    for incident in store.list_incidents():
        out.extend(store.list_restoration_confirmations(incident.incident_id))
    return out


def _handover_position_at(store, at: datetime) -> dict:
    """The handover position standing at ``at``: the latest
    handover assembled before it, and whether an acceptance of
    record binds it. Counts and state only — no artefact ids
    leave this section."""
    prior = [h for h in store.list_shift_handovers() if h.handed_over_at < at]
    if not prior:
        return {"handover_of_record": False, "accepted": False}
    latest = prior[-1]
    accepted = bool(store.list_handover_acceptances(latest.handover_id))
    return {"handover_of_record": True, "accepted": accepted}


# ---------------------------------------------------------------------
# The verification path: the same figures, re-performed by an
# independent walk over the store. It shares no helper with the
# generation path — a defect in one path is caught by the other,
# which is the point of having two.
# ---------------------------------------------------------------------


def _stats_again(outcomes: list) -> dict:
    errors_signed: list[float] = []
    errors_absolute: list[float] = []
    for outcome in outcomes:
        if outcome.status != "compared":
            continue
        errors_signed.append(outcome.signed_error_minutes)
        errors_absolute.append(outcome.absolute_error_minutes)
    count = len(errors_signed)
    return {
        "compared_count": count,
        "mean_signed_error_minutes": (
            round(sum(errors_signed) / count, 3) if count else None
        ),
        "mean_absolute_error_minutes": (
            round(sum(errors_absolute) / count, 3) if count else None
        ),
        "max_absolute_error_minutes": (
            max(errors_absolute) if errors_absolute else None
        ),
    }


def _rate_again(numerator: int, denominator: int) -> dict:
    value = None
    if denominator:
        value = round(numerator / denominator, 4)
    return {"value": value, "numerator": numerator, "denominator": denominator}


def reperform_sections(store, storm: StormService, start: datetime, end: datetime) -> dict:
    # Incident states, re-derived from the raw artefact lists:
    # a revision prior or a split source is superseded; an
    # incident with a closure or a gated close act is closed;
    # anything else founded in the window stands live-open.
    superseded_ids = set()
    for revision in store.list_incident_revisions():
        superseded_ids.add(revision.prior_incident_id)
    for split in store.list_incident_splits():
        superseded_ids.add(split.incident_id)
    closed_ids = set()
    for closure in store.list_incident_closures():
        closed_ids.add(closure.incident_id)
    for incident in store.list_incidents():
        if store.get_incident_close_act(incident.incident_id) is not None:
            closed_ids.add(incident.incident_id)

    total = 0
    state_counts = {"live_open": 0, "closed": 0, "superseded": 0}
    band_counts: dict[str, int] = {}
    for incident in store.list_incidents():
        if not (start <= incident.created_at < end):
            continue
        total += 1
        if incident.incident_id in superseded_ids:
            state_counts["superseded"] += 1
        elif incident.incident_id in closed_ids:
            state_counts["closed"] += 1
        else:
            state_counts["live_open"] += 1
        assessments = store.list_severity_assessments(incident.incident_id)
        band = assessments[-1].band if assessments else "UNASSESSED"
        band_counts[band] = band_counts.get(band, 0) + 1

    window_outcomes = []
    for outcome in store.list_etr_outcomes():
        if start <= outcome.recorded_at < end:
            window_outcomes.append(outcome)
    compared_n = 0
    for outcome in window_outcomes:
        if outcome.status == "compared":
            compared_n += 1
    mode_stats = {}
    for mode in ("normal", "storm"):
        mode_stats[mode] = _stats_again(
            [o for o in window_outcomes if o.operating_mode == mode]
        )

    confirmation_total = 0
    status_counts: dict[str, int] = {}
    signal_total = 0
    for incident in store.list_incidents():
        for confirmation in store.list_restoration_confirmations(
            incident.incident_id
        ):
            if start <= confirmation.computed_at < end:
                confirmation_total += 1
                status_counts[confirmation.status] = (
                    status_counts.get(confirmation.status, 0) + 1
                )
        for signal in store.list_restoration_signals(incident.incident_id):
            if start <= signal.observed_at < end:
                signal_total += 1
    contradiction_total = 0
    for contradiction in store.list_post_close_contradictions():
        if start <= contradiction.recorded_at < end:
            contradiction_total += 1

    kind_counts: dict[str, int] = {}
    declaration_total = 0
    for declaration in store.list_storm_declarations():
        if start <= declaration.declared_at < end:
            declaration_total += 1
            kind_counts[declaration.kind] = (
                kind_counts.get(declaration.kind, 0) + 1
            )

    assembled = 0
    for handover in store.list_shift_handovers():
        if start <= handover.handed_over_at < end:
            assembled += 1
    accepted_n = 0
    for acceptance in store.list_handover_acceptances():
        if start <= acceptance.accepted_at < end:
            accepted_n += 1
    standing = None
    for handover in store.list_shift_handovers():
        if handover.handed_over_at < end:
            standing = handover
    if standing is None:
        position = {"handover_of_record": False, "accepted": False}
    else:
        position = {
            "handover_of_record": True,
            "accepted": bool(
                store.list_handover_acceptances(standing.handover_id)
            ),
        }

    return {
        "incident_volumes": {
            "population": "ok" if total else "no_data",
            "total": total,
            "by_state": state_counts,
            "closure_rate": _rate_again(state_counts["closed"], total),
        },
        "severity_mix": {
            "population": "ok" if total else "no_data",
            "total": total,
            "by_band": dict(sorted(band_counts.items())),
            "sev1_rate": _rate_again(band_counts.get("SEV-1", 0), total),
        },
        "etr_accuracy": {
            "population": "ok" if window_outcomes else "no_data",
            "outcomes_recorded": len(window_outcomes),
            "unestimated_count": len(window_outcomes) - compared_n,
            "compared_rate": _rate_again(compared_n, len(window_outcomes)),
            "overall": _stats_again(window_outcomes),
            "by_operating_mode": mode_stats,
        },
        "restoration_outcomes": {
            "population": "ok" if confirmation_total else "no_data",
            "confirmations": confirmation_total,
            "by_status": dict(sorted(status_counts.items())),
            "signals_observed": signal_total,
            "post_close_contradictions": contradiction_total,
            "completion_rate": _rate_again(
                status_counts.get("complete", 0), confirmation_total
            ),
        },
        "storm_activity": {
            "population": "ok" if declaration_total else "no_data",
            "declarations": declaration_total,
            "by_kind": dict(sorted(kind_counts.items())),
            "windows_open_at_window_end": len(storm.active_windows(end)),
        },
        "handover_position": {
            "population": (
                "ok" if (assembled or accepted_n) else "no_data"
            ),
            "handovers_assembled": assembled,
            "acceptances_recorded": accepted_n,
            "position_at_window_end": position,
        },
    }
