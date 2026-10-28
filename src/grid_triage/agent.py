"""The agent step, in Strands shape, behind a model port.

The blueprint's thesis is the AgentCore-native path: in production
this module's loop is the Strands Agents SDK ``Agent`` (BedrockModel)
running inside the AgentCore Runtime entrypoint. The loop here keeps
the same shape — a system prompt, a set of plain-function tools, and
a model behind a port — so the swap is the model/tool wiring, not a
rewrite.

R1's agent work is deliberately narrow and real: given an incident,
assemble its brief from the correlated alarms and the topology
slice. Every statement the model proposes carries citations to
alarm evidence; a citation that does not resolve to a member alarm
of this incident is **refused** in code — the whole brief, not just
the sentence.

Model profiles:
- ``offline`` (default): a deterministic composer. No key, no
  network; used by tests, evals and the demo.
- ``bedrock``: the production profile — a Bedrock Converse call via
  the Strands ``BedrockModel``. It refuses loudly until configured
  (region + model id) and is stub-tested in R1; no live AWS call is
  made by the test or eval gates.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Protocol

from grid_triage.evidence import EvidenceChain
from grid_triage.models import BriefStatement, Citation, IncidentBrief
from grid_triage.severity import SeverityService

_BAND_TOKEN = re.compile(r"\bSEV-[1-4]\b|\bUNRATED\b")


class ModelNotConfigured(RuntimeError):
    pass


class CitationRefusal(RuntimeError):
    """Raised when a proposed brief cites an alarm that is not a
    member of the incident, or a field the alarm payload lacks."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("unresolvable citations: " + "; ".join(problems))


class BandMismatchRefusal(RuntimeError):
    """Raised when a proposed brief states a severity band other
    than the computed assessment's (Law 3: the model may summarise
    the band, never re-band)."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("band mismatch: " + "; ".join(problems))


def band_problems(statements: list[BriefStatement], assessed_band: str) -> list[str]:
    """Every band token mentioned anywhere in the brief must be
    the assessed band."""
    problems: list[str] = []
    for stmt in statements:
        for token in _BAND_TOKEN.findall(stmt.text):
            if token != assessed_band:
                problems.append(
                    f"brief states {token} but the assessment is {assessed_band}:"
                    f" {stmt.text[:40]!r}"
                )
    return problems


class ModelPort(Protocol):
    profile: str

    def propose_brief(self, facts: list[dict[str, Any]]) -> list[BriefStatement]:
        """Propose brief statements over the assembled facts. Each
        fact is a dict with ``item_id`` (the alarm's evidence item),
        ``field`` and ``value``; statements cite facts by evidence
        item + field."""
        ...


def _dig(payload: dict, field: str) -> Any:
    cur: Any = payload
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


class CitationResolver:
    """Checks citations against the incident's member-alarm
    evidence payloads."""

    def __init__(self, chain: EvidenceChain):
        self.chain = chain

    def problems(self, incident_id: str, statements: list[BriefStatement]) -> list[str]:
        items = {i.item_id: i for i in self.chain.store.list_evidence(incident_id)}
        problems: list[str] = []
        for stmt in statements:
            if not stmt.citations:
                problems.append(f"statement has no citation: {stmt.text[:40]!r}")
                continue
            for cit in stmt.citations:
                item = items.get(cit.evidence_item_id)
                if item is None:
                    problems.append(f"unknown evidence item {cit.evidence_item_id}")
                    continue
                payload = self.chain.payload_of(item)
                if _dig(payload, cit.field) is None:
                    problems.append(
                        f"field {cit.field!r} absent in {cit.evidence_item_id}"
                    )
        return problems


class OfflineComposer:
    """The deterministic offline profile: builds statements directly
    from the assembled facts, each citing the fact it came from."""

    profile = "offline"

    def propose_brief(self, facts: list[dict[str, Any]]) -> list[BriefStatement]:
        by_field: dict[tuple[str, str], dict] = {}
        for fact in facts:
            by_field.setdefault((fact["item_id"], fact["field"]), fact)
        statements: list[BriefStatement] = []

        anchors = [f for f in facts if f["field"] == "_anchor"]
        if anchors:
            anchor = anchors[0]
            statements.append(
                BriefStatement(
                    text=(
                        f"Anchor alarm: {anchor['kind']} on device "
                        f"{anchor['device_id']} at {anchor['raised_at']}."
                    ),
                    citations=[Citation(evidence_item_id=anchor["item_id"], field="kind")],
                )
            )
        kinds: dict[str, tuple[int, str]] = {}
        for fact in facts:
            if fact["field"] == "kind":
                count, item_id = kinds.get(fact["value"], (0, fact["item_id"]))
                kinds[fact["value"]] = (count + 1, item_id)
        for kind in sorted(kinds):
            count, item_id = kinds[kind]
            statements.append(
                BriefStatement(
                    text=f"Alarms of kind {kind}: {count}.",
                    citations=[Citation(evidence_item_id=item_id, field="kind")],
                )
            )
        devices = sorted({f["value"] for f in facts if f["field"] == "device_id"})
        if devices:
            first = next(f for f in facts if f["field"] == "device_id")
            statements.append(
                BriefStatement(
                    text=f"Devices reporting: {', '.join(devices)}.",
                    citations=[Citation(evidence_item_id=first["item_id"], field="device_id")],
                )
            )
        severity = next((f for f in facts if f["field"] == "_severity"), None)
        if severity is not None:
            item_id = severity["item_id"]
            if severity["band"] == "UNRATED":
                statements.append(
                    BriefStatement(
                        text=(
                            "Severity UNRATED — the data cannot supply: "
                            + ", ".join(severity["missing_inputs"])
                            + "."
                        ),
                        citations=[
                            Citation(evidence_item_id=item_id, field="band"),
                            Citation(evidence_item_id=item_id, field="missing_inputs"),
                        ],
                    )
                )
            else:
                loads = severity["critical_load_names"]
                loads_text = ", ".join(loads) if loads else "none"
                statements.append(
                    BriefStatement(
                        text=(
                            f"Severity {severity['band']} (score {severity['score']}): "
                            f"{severity['customers_affected']} customers affected, "
                            f"{severity['member_alarm_count']} alarms, "
                            f"{severity['duration_minutes']:g} minutes elapsed; "
                            f"critical loads affected: {loads_text}."
                        ),
                        citations=[
                            Citation(evidence_item_id=item_id, field="band"),
                            Citation(evidence_item_id=item_id, field="score"),
                            Citation(
                                evidence_item_id=item_id,
                                field="inputs.customers_affected",
                            ),
                        ],
                    )
                )
        return statements


class BedrockProfile:
    """The production model profile. Stub-tested in R1: it refuses
    loudly until configured, and no code path falls back to the
    offline composer silently."""

    profile = "bedrock"

    def __init__(self, region: str | None = None, model_id: str | None = None):
        self.region = region or os.environ.get("AWS_REGION")
        self.model_id = model_id or os.environ.get("BEDROCK_MODEL_ID")

    def propose_brief(self, facts: list[dict[str, Any]]) -> list[BriefStatement]:
        if not self.region or not self.model_id:
            raise ModelNotConfigured(
                "bedrock profile selected but AWS_REGION / BEDROCK_MODEL_ID "
                "are not set — refusing rather than falling back"
            )
        raise ModelNotConfigured(
            "bedrock profile is wired as a port in R1 and exercised against "
            "stub transports; live Converse calls land with the AWS account "
            "integration (see README honest status)"
        )


def model_for_profile(profile: str) -> ModelPort:
    if profile == "offline":
        return OfflineComposer()
    if profile == "bedrock":
        return BedrockProfile()
    raise ModelNotConfigured(f"unknown model profile: {profile!r}")


class IncidentAssembler:
    """The agent step: member alarms → facts → model proposal →
    citation check → band check → stored brief. Its tools are the
    reads below (the registry's read/draft shapes); its only write
    is the verified brief. Severity is computed by the severity
    engine (Law 3) before the model is consulted, recorded on the
    incident's evidence chain, and handed to the model as facts —
    the brief may restate the assessed band and no other."""

    def __init__(self, store, chain: EvidenceChain, model: ModelPort):
        self.store = store
        self.chain = chain
        self.model = model
        self.resolver = CitationResolver(chain)
        self.severity = SeverityService(store, chain)

    # -- tools (plain functions, Strands shape) ----------------------
    def read_incident_facts(self, incident_id: str) -> list[dict[str, Any]]:
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        facts: list[dict[str, Any]] = []
        for item in self.store.list_evidence(incident_id):
            if item.kind != "alarm":
                continue
            payload = self.chain.payload_of(item)
            for field in ("kind", "device_id", "feeder_id", "raised_at", "message"):
                value = _dig(payload, field)
                if value is not None:
                    facts.append(
                        {"item_id": item.item_id, "field": field, "value": value}
                    )
            if payload["alarm_id"] == incident.anchor_alarm_id:
                facts.append(
                    {
                        "item_id": item.item_id,
                        "field": "_anchor",
                        "kind": payload["kind"],
                        "device_id": payload["device_id"],
                        "raised_at": payload["raised_at"],
                    }
                )
        return facts

    def read_severity_facts(self, assessment) -> list[dict[str, Any]]:
        """The current assessment as model facts, citing its own
        evidence item on the incident's chain."""
        item_id = f"EV-{assessment.assessment_id}"
        inputs = assessment.inputs
        facts: list[dict[str, Any]] = [
            {
                "item_id": item_id,
                "field": "_severity",
                "band": assessment.band,
                "score": assessment.score,
                "customers_affected": inputs.get("customers_affected"),
                "critical_load_names": [
                    c["name"] for c in inputs.get("critical_loads", [])
                ],
                "duration_minutes": inputs.get("duration_minutes"),
                "member_alarm_count": inputs.get("member_alarm_count"),
                "missing_inputs": assessment.missing_inputs,
            },
            {"item_id": item_id, "field": "band", "value": assessment.band},
        ]
        if assessment.score is not None:
            facts.append(
                {"item_id": item_id, "field": "score", "value": assessment.score}
            )
        if inputs.get("customers_affected") is not None:
            facts.append(
                {
                    "item_id": item_id,
                    "field": "inputs.customers_affected",
                    "value": inputs["customers_affected"],
                }
            )
        return facts

    # -- the step ------------------------------------------------------
    def assemble(self, incident_id: str, now: datetime) -> IncidentBrief:
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"no such incident: {incident_id}")
        assessment = self.severity.assess(incident_id, now)
        facts = self.read_incident_facts(incident_id)
        facts += self.read_severity_facts(assessment)
        statements = self.model.propose_brief(facts)
        problems = self.resolver.problems(incident_id, statements)
        if problems:
            raise CitationRefusal(problems)
        mismatches = band_problems(statements, assessment.band)
        if mismatches:
            raise BandMismatchRefusal(mismatches)
        brief = IncidentBrief(
            brief_id=f"BRIEF-{incident_id}-{len(self.store.list_briefs(incident_id)) + 1:03d}",
            incident_id=incident_id,
            model_profile=self.model.profile,
            statements=statements,
            assembled_at=now,
        )
        self.store.add_brief(brief)
        return brief
