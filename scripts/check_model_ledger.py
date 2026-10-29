"""Model ledger check (R13).

Builds a planted record — the reference flood worked through
brief assembly on the standard wiring, so every brief the
assembler stores has routed through the gateway — then grades
the ledger claims from raw store reads (never via the gateway
under test):

- every stored brief has exactly one completed ledger record,
  and the records' fold equals an independent accumulation
  computed here from the raw payloads;
- every record's token estimates recompute from its stored
  sizes under the pack's stated rule, and its cost recomputes
  from the pack's rates;
- a tampered record (a stored cost edited in place) is named
  by the gateway's verifier;
- a scratch gateway with a two-call budget refuses the third
  call before its port is touched, and the refusal is on the
  ledger.

Run: uv run python scripts/check_model_ledger.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from grid_triage.agent import OfflineComposer  # noqa: E402
from grid_triage.config import Settings  # noqa: E402
from grid_triage.model_gateway import (  # noqa: E402
    MODEL_PACK,
    ModelBudgetRefusal,
    ModelGateway,
    verify_ledger,
)
from grid_triage.seed import FLOOD_ALARM_IDS, T0  # noqa: E402
from grid_triage.wiring import Services  # noqa: E402


def main() -> int:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        svc = Services(
            Settings(
                sqlite_path=str(tmp / "grid.db"),
                object_store_dir=str(tmp / "objects"),
            )
        )
        svc.intake.ingest_feed(svc.feed.fetch_alarms())
        created = svc.correlator.correlate_new(svc.topology)
        flood_id = next(
            i.incident_id
            for i in created
            if i.anchor_alarm_id == FLOOD_ALARM_IDS[0]
        )
        svc.assembler.assemble(flood_id, T0 + timedelta(minutes=30))
        svc.assembler.assemble(flood_id, T0 + timedelta(minutes=45))

        briefs = svc.store.list_briefs(flood_id)
        records = svc.store.list_model_calls(flood_id)
        completed = [r for r in records if r.status == "completed"]
        if len(completed) != len(briefs):
            problems.append(
                f"{len(completed)} completed records for {len(briefs)} briefs"
            )
        # Independent accumulation from the raw payloads.
        rows = svc.store._run(
            "SELECT payload FROM model_calls ORDER BY act_seq", fetch="all"
        )
        total_cost = 0
        for (payload,) in rows:
            raw = json.loads(payload)
            per = MODEL_PACK["bytes_per_token"]
            in_tok = -(-raw["input_bytes"] // per)
            out_tok = -(-raw["output_bytes"] // per)
            if in_tok != raw["input_tokens"] or out_tok != raw["output_tokens"]:
                problems.append(f"{raw['call_id']}: tokens do not recompute")
            rates = MODEL_PACK["rates_micro_per_token"][raw["profile"]]
            cost = in_tok * rates["input"] + out_tok * rates["output"]
            if cost != raw["cost_micro"]:
                problems.append(f"{raw['call_id']}: cost does not recompute")
            total_cost += raw["cost_micro"]
        folded = svc.model_gateway.ledger_view(flood_id)
        if folded["cost_micro"] != total_cost:
            problems.append("the ledger fold disagrees with the raw fold")
        if verify_ledger(svc.store):
            problems.append("the clean ledger does not verify clean")

        # A tampered stored cost must be named.
        first = completed[0]
        svc.store._run(
            "UPDATE model_calls SET payload = ? WHERE call_id = ?",
            (
                first.model_copy(
                    update={"cost_micro": first.cost_micro + 1}
                ).model_dump_json(),
                first.call_id,
            ),
        )
        named = verify_ledger(svc.store)
        if not any(first.call_id in p for p in named):
            problems.append("a tampered record cost was not named")

        # The budget refuses before the port, on the record.
        svc2 = Services(
            Settings(
                sqlite_path=str(tmp / "grid2.db"),
                object_store_dir=str(tmp / "objects2"),
            )
        )
        pack = dict(MODEL_PACK, max_calls_per_incident=2)
        gateway = ModelGateway(svc2.store, OfflineComposer(), pack=pack)
        facts = [{"item_id": "X", "field": "kind", "value": "trip"}]
        gateway.propose_brief("INC-CHECK", facts, at=T0)
        gateway.propose_brief("INC-CHECK", facts, at=T0)
        try:
            gateway.propose_brief("INC-CHECK", facts, at=T0)
            problems.append("the third call was not refused at the budget")
        except ModelBudgetRefusal:
            pass
        rows2 = svc2.store.list_model_calls("INC-CHECK")
        if [r.status for r in rows2] != [
            "completed",
            "completed",
            "refused_budget",
        ]:
            problems.append("the refusal is not the third ledger record")

    if problems:
        print("MODEL LEDGER CHECK: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("MODEL LEDGER CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
