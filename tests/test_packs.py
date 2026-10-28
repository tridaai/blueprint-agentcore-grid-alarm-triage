"""Round 3: sealed evidence packs and the brief's use of priors —
manifests over every artefact of record, supersession by version,
tamper detected by recomputation, and misattributed priors
refused."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from grid_triage.agent import (
    CitationRefusal,
    IncidentAssembler,
    PriorAttributionRefusal,
)
from grid_triage.models import BriefStatement, Citation
from grid_triage.packs import PackRefusal, manifest_hash
from grid_triage.seed import T0

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
SEAL_AT = T0 + timedelta(minutes=35)


@pytest.fixture()
def briefed(fed):
    fed.memory.load_history()
    fed.assembler.assemble(FLOOD, T0 + timedelta(minutes=30))
    return fed


def test_seal_builds_the_full_manifest(briefed):
    pack = briefed.packs.seal(FLOOD, "op.dana", SEAL_AT)
    assert pack.version == 1
    assert pack.supersedes is None
    assert pack.sealed_by == "op.dana"
    types = [m["type"] for m in pack.members]
    assert types.count("alarm") == 40
    for expected in (
        "correlation_decisions",
        "severity_assessment",
        "topology_slice",
        "brief",
        "priors",
    ):
        assert types.count(expected) == 1
    corr = next(m for m in pack.members if m["type"] == "correlation_decisions")
    assert corr["correlation_pack_version"] == "2026.10.1"
    priors = next(m for m in pack.members if m["type"] == "priors")
    seeded = {r.incident_id: r.record_id for r in briefed.memory_port.list_records()}
    assert [e["record_id"] for e in priors["records"]] == [
        seeded["INC-HIST-0042"],
        seeded["INC-HIST-0057"],
    ]
    assert pack.manifest_hash == manifest_hash(FLOOD, pack.members)
    assert briefed.packs.verify_pack(pack.pack_id) == []
    assert briefed.chain.verify_chain(FLOOD)


def test_seal_requires_a_brief_first(fed):
    fed.memory.load_history()
    with pytest.raises(PackRefusal) as excinfo:
        fed.packs.seal(FLOOD, "op.dana", SEAL_AT)
    assert any("brief" in p for p in excinfo.value.problems)


def test_seal_names_its_sealer(briefed):
    with pytest.raises(PackRefusal):
        briefed.packs.seal(FLOOD, "  ", SEAL_AT)


def test_reseal_supersedes_and_the_old_pack_still_verifies(briefed):
    first = briefed.packs.seal(FLOOD, "op.dana", SEAL_AT)
    later = briefed.severity.assess(FLOOD, T0 + timedelta(hours=3))
    assert later.assessment_id != next(
        m["assessment_id"]
        for m in first.members
        if m["type"] == "severity_assessment"
    )
    second = briefed.packs.seal(FLOOD, "op.dana", T0 + timedelta(hours=3, minutes=5))
    assert second.version == 2
    assert second.supersedes == first.pack_id
    sev2 = next(
        m["assessment_id"]
        for m in second.members
        if m["type"] == "severity_assessment"
    )
    assert sev2 == later.assessment_id
    # The first pack is byte-identical and still verifies against
    # the artefacts it sealed — supersession is not mutation.
    assert briefed.store.get_evidence_pack(first.pack_id) == first
    assert briefed.packs.verify_pack(first.pack_id) == []
    assert briefed.packs.verify_pack(second.pack_id) == []


def test_tampered_stored_member_is_detected(briefed, tmp_path):
    pack = briefed.packs.seal(FLOOD, "op.dana", SEAL_AT)
    db = sqlite3.connect(tmp_path / "grid.db")
    payload = json.loads(
        db.execute(
            "SELECT payload FROM evidence_packs WHERE pack_id = ?", (pack.pack_id,)
        ).fetchone()[0]
    )
    sev = next(m for m in payload["members"] if m["type"] == "severity_assessment")
    sev["content_hash"] = "0" * 64
    db.execute(
        "UPDATE evidence_packs SET payload = ? WHERE pack_id = ?",
        (json.dumps(payload), pack.pack_id),
    )
    db.commit()
    db.close()
    problems = briefed.packs.verify_pack(pack.pack_id)
    assert problems  # manifest + member drift both surface
    assert any("manifest" in p for p in problems)


def test_tampered_alarm_payload_is_detected(briefed):
    pack = briefed.packs.seal(FLOOD, "op.dana", SEAL_AT)
    member = next(m for m in pack.members if m["type"] == "alarm")
    item = next(
        i
        for i in briefed.store.list_evidence(FLOOD)
        if i.item_id == member["evidence_item_id"]
    )
    path = briefed.objects.root / item.object_key
    path.write_bytes(b'{"alarm_id": "forged"}')
    problems = briefed.packs.verify_pack(pack.pack_id)
    assert any("payload hash drifted" in p for p in problems)
    assert briefed.chain.verify_chain(FLOOD) is False


def test_pack_for_the_lone_incident_seals_with_no_priors(fed):
    fed.memory.load_history()
    fed.assembler.assemble(LONE, datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc))
    pack = fed.packs.seal(LONE, "op.dana", datetime(2026, 10, 14, 10, 5, tzinfo=timezone.utc))
    priors = next(m for m in pack.members if m["type"] == "priors")
    assert priors["records"] == []
    assert fed.packs.verify_pack(pack.pack_id) == []


def test_brief_quotes_priors_as_recorded(briefed):
    briefs = briefed.store.list_briefs(FLOOD)
    prior_statements = [s for s in briefs[-1].statements if "Prior incident" in s.text]
    assert len(prior_statements) == 2
    assert "INC-HIST-0042" in prior_statements[0].text
    assert "vegetation_contact" in prior_statements[0].text
    assert briefed.assembler.resolver.problems(FLOOD, briefs[-1].statements) == []


def test_lone_brief_has_no_prior_statements(fed):
    fed.memory.load_history()
    brief = fed.assembler.assemble(
        LONE, datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    )
    assert not any("Prior incident" in s.text for s in brief.statements)


class _MisattributingModel:
    profile = "offline"

    def __init__(self, record_id):
        self.record_id = record_id

    def propose_brief(self, facts):
        return [
            BriefStatement(
                text="A prior incident on this feeder was caused by lightning.",
                citations=[
                    Citation(evidence_item_id=self.record_id, field="cause_class")
                ],
            )
        ]


def test_misattributed_prior_refuses_the_brief(briefed):
    record = next(
        r
        for r in briefed.memory_port.list_records()
        if r.incident_id == "INC-HIST-0042"
    )
    assembler = IncidentAssembler(
        briefed.store,
        briefed.chain,
        _MisattributingModel(record.record_id),
        cause=briefed.cause,
        memory=briefed.memory,
    )
    before = len(briefed.store.list_briefs(FLOOD))
    with pytest.raises(PriorAttributionRefusal):
        assembler.assemble(FLOOD, T0 + timedelta(minutes=40))
    assert len(briefed.store.list_briefs(FLOOD)) == before


class _UnknownRecordModel:
    profile = "offline"

    def propose_brief(self, facts):
        return [
            BriefStatement(
                text="A prior incident is cited from nowhere.",
                citations=[
                    Citation(evidence_item_id="MEM-doesnotexist", field="cause_class")
                ],
            )
        ]


def test_citation_to_an_unknown_record_refuses(briefed):
    assembler = IncidentAssembler(
        briefed.store,
        briefed.chain,
        _UnknownRecordModel(),
        cause=briefed.cause,
        memory=briefed.memory,
    )
    with pytest.raises(CitationRefusal):
        assembler.assemble(FLOOD, T0 + timedelta(minutes=40))
