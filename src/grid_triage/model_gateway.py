"""The model gateway and its cost ledger — Round 13.

Until this round the assembler's model port was called bare:
one call site, no record of what was asked, what came back, or
what it cost. The sibling AgentCore blueprints governed that
call in their middle rounds; this round gives Grid the same
governance in its own idiom.

- **One door.** Every model call in the build routes through
  `ModelGateway.propose_brief`. The gateway wraps the configured
  port — the offline deterministic composer by default, the
  fail-closed Bedrock profile when selected — and the wrapped
  port's behaviour is unchanged: a hosted profile that is not
  configured refuses loudly, and the gateway records that error
  and lets it propagate. Nothing falls back silently.
- **Every call is a record.** Completed, refused and errored
  calls each land as one insert-only `ModelCallRecord`
  (migration 0012) before control returns to the caller. A
  record carries the measured canonical sizes of the facts
  handed over and the statements returned, the pack's token
  estimates derived from those sizes, the pack's rates, and the
  exact integer cost in micro-dollars.
- **Budgets refuse, on the record.** The pack states a
  per-incident call budget and a per-incident cost budget. A
  call that would cross either is refused before the port is
  touched; the refusal is itself a ledger record naming the
  budget it hit, and `ModelBudgetRefusal` propagates.
- **Totals fold, and re-perform.** No total is ever stored.
  `ledger_view` folds an incident's records; `verify_ledger`
  re-derives every record's tokens and cost from its stored
  sizes and the pack, and re-folds — a record whose arithmetic
  does not recompute, or a fold that disagrees, is named.
  `scripts/check_model_ledger.py` runs the same reconciliation
  over a planted record from raw store reads.

The ledger is a read surface under the Round 11 access pack
(`model_ledger`): an incident-scoped guarded read, hidden
exactly as the other incident surfaces are hidden. The gateway
holds no tool shapes at all — it routes a draft-shaped model
call and writes its own ledger; Law 1 is untouched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from grid_triage.access import (
    SURFACE_MODEL_LEDGER,
    Principal,
    control_for,
)
from grid_triage.agent import BriefStatement, ModelPort
from grid_triage.models import ModelCallRecord

MODEL_PACK = {
    "version": "2026.10.1",
    # The token estimate is a stated measurement rule, not a
    # claim about any provider's tokenizer: canonical JSON
    # bytes divided by this many bytes per token, rounded up.
    "bytes_per_token": 4,
    # Per-incident budgets. The call budget binds every profile;
    # the cost budget binds priced profiles (the offline profile
    # is priced at zero, so its cost can never cross).
    "max_calls_per_incident": 25,
    "max_cost_micro_per_incident": 2_500_000,
    # Rates in integer micro-dollars per token, stated by the
    # pack. The bedrock figures are the pack's reference rates
    # for a frontier hosted model; a deployment states its own
    # contracted rates by superseding the pack. Integer rates
    # keep every cost an exact integer fold.
    "rates_micro_per_token": {
        "offline": {"input": 0, "output": 0},
        "bedrock": {"input": 3, "output": 15},
    },
}

STATUS_COMPLETED = "completed"
STATUS_REFUSED = "refused_budget"
STATUS_ERROR = "error"


class ModelBudgetRefusal(RuntimeError):
    """A call the incident's budget refuses. The refusal is a
    ledger record before this propagates."""

    def __init__(self, incident_id: str, budget: str, detail: str):
        super().__init__(detail)
        self.incident_id = incident_id
        self.budget = budget


class ModelGatewayError(RuntimeError):
    """The gateway's own grading found the ledger wanting."""


def _canonical_bytes(payload) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def estimate_tokens(nbytes: int, pack: dict) -> int:
    per = int(pack["bytes_per_token"])
    return -(-int(nbytes) // per)  # ceil division, exact


def cost_micro(profile: str, input_tokens: int, output_tokens: int, pack: dict) -> int:
    rates = pack["rates_micro_per_token"][profile]
    return input_tokens * int(rates["input"]) + output_tokens * int(rates["output"])


class ModelGateway:
    """The one door for model calls. Wraps a ModelPort; the
    assembler calls the gateway exactly where it called the
    port, and the brief it stores still names the wrapped
    profile."""

    def __init__(self, store, port: ModelPort, pack: dict = MODEL_PACK):
        self.store = store
        self.port = port
        self.pack = pack

    @property
    def profile(self) -> str:
        return self.port.profile

    # -- the door ------------------------------------------------

    def propose_brief(
        self,
        incident_id: str,
        facts: list[dict],
        *,
        at: datetime | None = None,
    ) -> list[BriefStatement]:
        moment = at or datetime.now(timezone.utc)
        records = self.store.list_model_calls(incident_id)
        completed = [r for r in records if r.status == STATUS_COMPLETED]
        calls_used = len(completed)
        cost_used = sum(r.cost_micro for r in completed)
        if calls_used >= int(self.pack["max_calls_per_incident"]):
            detail = (
                f"incident {incident_id} has used {calls_used} of "
                f"{self.pack['max_calls_per_incident']} budgeted model "
                "calls; the call is refused before the port is touched"
            )
            self._record(
                incident_id, STATUS_REFUSED, "call_budget", detail, moment
            )
            raise ModelBudgetRefusal(incident_id, "call_budget", detail)
        if cost_used >= int(self.pack["max_cost_micro_per_incident"]):
            detail = (
                f"incident {incident_id} has spent {cost_used} of "
                f"{self.pack['max_cost_micro_per_incident']} budgeted "
                "micro-dollars; the call is refused before the port "
                "is touched"
            )
            self._record(
                incident_id, STATUS_REFUSED, "cost_budget", detail, moment
            )
            raise ModelBudgetRefusal(incident_id, "cost_budget", detail)
        input_bytes = len(_canonical_bytes(facts))
        try:
            statements = self.port.propose_brief(facts)
        except Exception as exc:  # recorded, then propagated as-is
            self._record(
                incident_id,
                STATUS_ERROR,
                type(exc).__name__,
                str(exc)[:400],
                moment,
            )
            raise
        output_bytes = len(
            _canonical_bytes([s.model_dump(mode="json") for s in statements])
        )
        in_tok = estimate_tokens(input_bytes, self.pack)
        out_tok = estimate_tokens(output_bytes, self.pack)
        record = ModelCallRecord(
            call_id=self._next_call_id(incident_id, records),
            incident_id=incident_id,
            profile=self.port.profile,
            purpose="brief",
            status=STATUS_COMPLETED,
            input_bytes=input_bytes,
            output_bytes=output_bytes,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_micro=cost_micro(self.port.profile, in_tok, out_tok, self.pack),
            pack_version=self.pack["version"],
            called_at=moment,
        )
        self.store.add_model_call(record)
        return statements

    def _record(
        self,
        incident_id: str,
        status: str,
        detail_kind: str,
        detail: str,
        at: datetime,
    ) -> ModelCallRecord:
        records = self.store.list_model_calls(incident_id)
        record = ModelCallRecord(
            call_id=self._next_call_id(incident_id, records),
            incident_id=incident_id,
            profile=self.port.profile,
            purpose="brief",
            status=status,
            detail=f"{detail_kind}: {detail}"[:400],
            pack_version=self.pack["version"],
            called_at=at,
        )
        self.store.add_model_call(record)
        return record

    @staticmethod
    def _next_call_id(incident_id: str, records: list[ModelCallRecord]) -> str:
        return f"MCALL-{incident_id}-{len(records) + 1:03d}"

    # -- the ledger read surface ---------------------------------

    def ledger_view(
        self,
        incident_id: str,
        principal: Principal | None = None,
        *,
        at: datetime | None = None,
    ) -> dict:
        """The incident's ledger, folded from its records. A
        principal-carrying read is judged on the model_ledger
        surface with the incident surfaces' existence hiding: a
        role that may not read it — or an incident that does not
        exist — answers identically."""
        if principal is not None:
            control = control_for(self)
            control.require_incident_read(
                principal,
                SURFACE_MODEL_LEDGER,
                incident_id=incident_id,
                at=at,
            )
        records = self.store.list_model_calls(incident_id)
        return fold_records(records)


def fold_records(records: list[ModelCallRecord]) -> dict:
    """The one fold: counts by status and exact totals over the
    completed records. Refused and errored calls count as
    attempts and contribute no tokens and no cost."""
    completed = [r for r in records if r.status == STATUS_COMPLETED]
    return {
        "calls_recorded": len(records),
        "calls_completed": len(completed),
        "calls_refused_budget": sum(
            1 for r in records if r.status == STATUS_REFUSED
        ),
        "calls_errored": sum(1 for r in records if r.status == STATUS_ERROR),
        "input_tokens": sum(r.input_tokens for r in completed),
        "output_tokens": sum(r.output_tokens for r in completed),
        "cost_micro": sum(r.cost_micro for r in completed),
        "records": [r.model_dump(mode="json") for r in records],
    }


def verify_ledger(store, pack: dict = MODEL_PACK) -> list[str]:
    """The independent path: re-derive every record's tokens and
    cost from its stored sizes under the pack, and re-fold every
    incident's totals a second way (a running accumulation in
    record order, not the fold's comprehensions). Any record
    whose arithmetic does not recompute, any non-completed
    record carrying tokens or cost, and any fold disagreement
    is named. An empty list is a clean ledger."""
    problems: list[str] = []
    records = store.list_model_calls()
    for record in records:
        if record.pack_version != pack["version"]:
            problems.append(
                f"{record.call_id}: pack version {record.pack_version} "
                f"is not the grading pack {pack['version']}"
            )
            continue
        if record.status == STATUS_COMPLETED:
            in_tok = estimate_tokens(record.input_bytes, pack)
            out_tok = estimate_tokens(record.output_bytes, pack)
            if in_tok != record.input_tokens or out_tok != record.output_tokens:
                problems.append(
                    f"{record.call_id}: token estimates do not recompute "
                    f"from the stored sizes ({in_tok}/{out_tok} vs "
                    f"{record.input_tokens}/{record.output_tokens})"
                )
            if record.profile not in pack["rates_micro_per_token"]:
                # A record the pack cannot price is named, not
                # crashed on: before Round 19 the recompute
                # below raised KeyError on such a record and
                # the verifier died instead of grading (RT-23
                # in docs/red-team.md).
                problems.append(
                    f"{record.call_id}: profile {record.profile!r} "
                    "is not priced by the pack — its cost cannot "
                    "recompute"
                )
                continue
            expected = cost_micro(
                record.profile, in_tok, out_tok, pack
            )
            if expected != record.cost_micro:
                problems.append(
                    f"{record.call_id}: cost does not recompute from the "
                    f"pack rates ({expected} vs {record.cost_micro})"
                )
        else:
            if (
                record.input_tokens
                or record.output_tokens
                or record.cost_micro
            ):
                problems.append(
                    f"{record.call_id}: a {record.status} record carries "
                    "tokens or cost — nothing was produced"
                )
    by_incident: dict[str, list[ModelCallRecord]] = {}
    for record in records:
        by_incident.setdefault(record.incident_id, []).append(record)
    for incident_id, rows in by_incident.items():
        folded = fold_records(rows)
        total_cost = 0
        total_in = 0
        total_out = 0
        n_completed = 0
        for row in rows:
            if row.status == STATUS_COMPLETED:
                n_completed += 1
                total_in += row.input_tokens
                total_out += row.output_tokens
                total_cost += row.cost_micro
        if (
            folded["cost_micro"] != total_cost
            or folded["input_tokens"] != total_in
            or folded["output_tokens"] != total_out
            or folded["calls_completed"] != n_completed
        ):
            problems.append(
                f"incident {incident_id}: the ledger fold disagrees "
                "with the second accumulation"
            )
    return problems
