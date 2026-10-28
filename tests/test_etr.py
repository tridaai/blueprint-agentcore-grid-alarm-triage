"""Round 4: the ETR engine — hand-computed estimates with their
basis on the record, not-computable naming its missing inputs,
superseding revisions, the brief's basis discipline, and the
closure act feeding the accuracy ledger."""

from datetime import datetime, timedelta, timezone

import pytest

from grid_triage.agent import EtrBasisRefusal, IncidentAssembler
from grid_triage.closure import ClosureRefusal
from grid_triage.etr import ETR_PACK, compute_estimate
from grid_triage.models import (
    Alarm,
    BriefStatement,
    CauseClassification,
    Citation,
    Incident,
)
from grid_triage.roster import RosterView, load_roster_file
from grid_triage.seed import T0
from grid_triage.topology import TopologyView, load_topology_file

FLOOD = "INC-ALM-FLD-001"
LONE = "INC-ALM-LONE-001"
LONE_T0 = datetime(2026, 10, 14, 9, 41, 0, tzinfo=timezone.utc)


# -- the planted fixtures, hand-computed --------------------------------
# Flood (Feeder 7), estimated at T0+30min (02:43 UTC):
#   cause vegetation_contact (lockout rule), anchor BRK-070, a
#   feeder_breaker at SUB-ALDER. Required skill vegetation_clearing:
#   only CREW-NORTH-VEG carries it — night shift 18:00-06:00 (02:43
#   is inside), but committed until 03:28 -> wait 45. Depot
#   SUB-ALDER -> SUB-ALDER travel 25. Base rate
#   vegetation_contact|feeder_breaker = 150. Total 220 ->
#   restored 06:23; duration from the 02:13 anchor = 250.
# Re-estimated at T0+120min (04:13): the crew is free and still on
#   shift -> wait 0; total 175 -> restored 07:08; the total moves
#   -45 and the one changed input is crew_wait.


def test_flood_etr_exact(fed):
    e = fed.etr.estimate(FLOOD, T0 + timedelta(minutes=30))
    assert e.status == "computed"
    assert e.basis == "computed_from_rates"
    assert e.rate_pack_version == ETR_PACK["version"]
    assert e.roster_version == "v1"
    assert e.supersedes is None
    assert e.missing_inputs == []
    assert {c["name"]: c["minutes"] for c in e.components} == {
        "crew_wait": 45,
        "travel_access": 25,
        "base_repair": 150,
    }
    assert e.total_minutes == 220
    assert e.estimated_restored_at == T0 + timedelta(minutes=250)
    assert e.estimated_duration_minutes == 250.0
    assert e.inputs["cause_class"] == "vegetation_contact"
    assert e.inputs["crew_id"] == "CREW-NORTH-VEG"
    assert e.inputs["rate_key"] == "vegetation_contact|feeder_breaker"
    assert e.inputs["elapsed_minutes"] == 30.0
    classification = fed.cause.current(FLOOD)
    assert e.classification_id == classification.classification_id


def test_etr_revision_supersedes_with_delta_and_changed_inputs(fed):
    svc = fed.etr
    e1 = svc.estimate(FLOOD, T0 + timedelta(minutes=30))
    again = svc.estimate(FLOOD, T0 + timedelta(minutes=30))
    assert again.estimate_id == e1.estimate_id  # identical: a no-op
    assert len(svc.history(FLOOD)) == 1

    e2 = svc.estimate(FLOOD, T0 + timedelta(minutes=120))
    assert e2.estimate_id != e1.estimate_id
    assert e2.supersedes == e1.estimate_id
    assert e2.total_minutes == 175
    assert e2.estimated_restored_at == T0 + timedelta(minutes=295)
    assert e2.revision["total_minutes_delta"] == -45
    assert e2.revision["estimated_restored_at_delta_minutes"] == 45.0
    assert e2.revision["changed_inputs"] == ["crew_wait"]
    assert svc.current(FLOOD).estimate_id == e2.estimate_id
    # The superseded estimate is unchanged, byte for byte.
    assert svc.store.get_etr_estimate(e1.estimate_id) == e1
    assert [e.estimate_id for e in svc.history(FLOOD)] == [
        e1.estimate_id,
        e2.estimate_id,
    ]


def test_etr_lands_on_the_evidence_chain(fed):
    e = fed.etr.estimate(FLOOD, T0 + timedelta(minutes=30))
    kinds = [i.kind for i in fed.store.list_evidence(FLOOD)]
    assert "etr_estimate" in kinds
    item = next(
        i for i in fed.store.list_evidence(FLOOD) if i.item_id == f"EV-{e.estimate_id}"
    )
    assert fed.chain.payload_of(item)["basis"] == "computed_from_rates"
    assert fed.chain.verify_chain(FLOOD) is True


# -- not computable: named inputs, never a guessed time -------------------


def test_lone_etr_not_computable_unknown_cause(fed):
    e = fed.etr.estimate(LONE, LONE_T0 + timedelta(minutes=15))
    assert e.status == "not_computable"
    assert e.basis is None
    assert e.missing_inputs == ["cause_class"]
    assert e.components == []
    assert e.total_minutes is None
    assert e.estimated_restored_at is None
    assert e.estimated_duration_minutes is None


def _ingest_pair(services, alarms):
    services.intake.ingest(alarms)
    created = services.correlator.correlate_new(services.topology)
    assert len(created) == 1
    return created[0]


def test_lightning_etr_not_computable_no_qualified_crew(services):
    incident = _ingest_pair(
        services,
        [
            Alarm(
                alarm_id="ALM-LT-001", stream_id="STREAM-SCADA-NORTH",
                device_id="BRK-030", feeder_id="FDR-03", kind="breaker_trip",
                severity="critical",
                raised_at=datetime(2026, 10, 15, 1, 5, tzinfo=timezone.utc),
                message="Feeder 3 breaker tripped",
            ),
            Alarm(
                alarm_id="ALM-LT-002", stream_id="STREAM-SCADA-NORTH",
                device_id="XF-0311", feeder_id="FDR-03", kind="lightning_strike",
                severity="major",
                raised_at=datetime(2026, 10, 15, 1, 7, tzinfo=timezone.utc),
                message="Lightning strike recorded near T11",
            ),
        ],
    )
    # The class classifies (surge signature) and a base rate exists
    # for it — what the roster cannot supply is a line-inspection
    # crew, and that is the input the estimate names.
    e = services.etr.estimate(
        incident.incident_id, datetime(2026, 10, 15, 1, 30, tzinfo=timezone.utc)
    )
    assert services.cause.current(incident.incident_id).cause_class == "lightning"
    assert e.status == "not_computable"
    assert e.missing_inputs == ["qualified_crew"]
    assert e.estimated_restored_at is None


def test_equipment_etr_exact(services):
    incident = _ingest_pair(
        services,
        [
            Alarm(
                alarm_id="ALM-EQ-001", stream_id="STREAM-SCADA-NORTH",
                device_id="BRK-030", feeder_id="FDR-03", kind="breaker_trip",
                severity="critical",
                raised_at=datetime(2026, 10, 15, 9, 41, tzinfo=timezone.utc),
                message="Feeder 3 breaker tripped on protection",
            ),
            Alarm(
                alarm_id="ALM-EQ-002", stream_id="STREAM-SCADA-NORTH",
                device_id="XF-0311", feeder_id="FDR-03",
                kind="transformer_temp_high", severity="major",
                raised_at=datetime(2026, 10, 15, 9, 44, tzinfo=timezone.utc),
                message="Transformer T11 temperature high",
            ),
        ],
    )
    # equipment_failure (thermal, no lockout). At 10:00 the Alder
    # day crew is on shift and free: wait 0, travel SUB-ALDER ->
    # SUB-ALDER 25; the South crew would travel 55 and loses.
    # Base equipment_failure|feeder_breaker = 200. Total 225 ->
    # restored 13:45; duration from 09:41 = 244.
    e = services.etr.estimate(
        incident.incident_id, datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    )
    assert e.status == "computed"
    assert e.inputs["cause_class"] == "equipment_failure"
    assert e.inputs["crew_id"] == "CREW-ALDER-LINE"
    assert {c["name"]: c["minutes"] for c in e.components} == {
        "crew_wait": 0,
        "travel_access": 25,
        "base_repair": 200,
    }
    assert e.total_minutes == 225
    assert e.estimated_restored_at == datetime(
        2026, 10, 15, 13, 45, tzinfo=timezone.utc
    )
    assert e.estimated_duration_minutes == 244.0


def _synthetic(classification_class, anchor_device, feeder, substation):
    incident = Incident(
        incident_id="INC-SYNTH",
        feeder_id=feeder,
        substation_id=substation,
        topology_version="v1",
        correlation_pack_version="2026.10.1",
        anchor_alarm_id="ALM-SYNTH",
        created_at=datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc),
    )
    anchor = Alarm(
        alarm_id="ALM-SYNTH", stream_id="S", device_id=anchor_device,
        feeder_id=feeder, kind="breaker_trip", severity="critical",
        raised_at=datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc),
        message="synthetic",
    )
    classification = CauseClassification(
        classification_id="CAUSE-SYNTH",
        incident_id="INC-SYNTH",
        pack_version="2026.10.1",
        cause_class=classification_class,
        rule_id=None,
        inputs={},
        classified_at=datetime(2026, 10, 15, 8, 5, tzinfo=timezone.utc),
    )
    return incident, anchor, classification


def test_base_rate_missing_is_named():
    topology = TopologyView(load_topology_file("v1"))
    roster = RosterView(load_roster_file("v1"))
    incident, anchor, classification = _synthetic(
        "equipment_failure", "SEN-1201", "FDR-12", "SUB-SOUTH"
    )
    e = compute_estimate(
        incident, anchor, classification, topology, roster,
        datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc),
    )
    # equipment crews and their travel exist; what does not exist
    # is a rate for equipment_failure on a line sensor.
    assert e.status == "not_computable"
    assert e.missing_inputs == ["base_rate"]


def test_travel_missing_is_named():
    topology = TopologyView(load_topology_file("v1"))
    roster = RosterView(load_roster_file("v1"))
    incident, anchor, classification = _synthetic(
        "equipment_failure", "REG-1202", "FDR-12", "SUB-SOUTH"
    )
    pack = dict(ETR_PACK)
    pack["travel_minutes"] = {"SUB-ALDER": {"SUB-ALDER": 25}}
    e = compute_estimate(
        incident, anchor, classification, topology, roster,
        datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc),
        pack=pack,
    )
    assert e.status == "not_computable"
    assert e.missing_inputs == ["travel_minutes"]


def test_roster_shift_windows():
    roster = RosterView(load_roster_file("v1"))
    night = roster.crew("CREW-NORTH-VEG")
    day = roster.crew("CREW-ALDER-LINE")
    at = lambda h, m=0: datetime(2026, 10, 15, h, m, tzinfo=timezone.utc)
    # Night crew (18:00-06:00, no commitments on the 15th).
    assert roster.available_start(night, at(2, 43)) == at(2, 43)
    assert roster.available_start(night, at(12, 0)) == at(18, 0)
    # Day crew (06:00-18:00).
    assert roster.available_start(day, at(10, 0)) == at(10, 0)
    assert roster.available_start(day, at(20, 0)) == datetime(
        2026, 10, 16, 6, 0, tzinfo=timezone.utc
    )


# -- the brief's basis discipline ------------------------------------------


def test_brief_carries_etr_with_basis(fed):
    brief = fed.assembler.assemble(FLOOD, T0 + timedelta(minutes=30))
    etr_statements = [
        s for s in brief.statements if s.text.startswith("Estimated restoration")
    ]
    assert len(etr_statements) == 1
    stmt = etr_statements[0]
    assert "basis: computed_from_rates" in stmt.text
    estimate = fed.etr.current(FLOOD)
    assert estimate.estimated_restored_at.isoformat() in stmt.text
    assert any(
        c.evidence_item_id == f"EV-{estimate.estimate_id}" for c in stmt.citations
    )
    assert fed.assembler.resolver.problems(FLOOD, brief.statements) == []


def test_brief_for_not_computable_names_missing_and_states_no_time(fed):
    brief = fed.assembler.assemble(LONE, LONE_T0 + timedelta(minutes=15))
    etr_statements = [
        s for s in brief.statements if s.text.startswith("No restoration estimate")
    ]
    assert len(etr_statements) == 1
    assert "cause_class" in etr_statements[0].text


class _BareTime:
    profile = "offline"

    def propose_brief(self, facts):
        return [
            BriefStatement(
                text="Restoration is expected by 2026-10-14T06:23:00+00:00.",
                citations=[Citation(evidence_item_id="EV-ALM-FLD-001", field="kind")],
            )
        ]


class _WrongTimeWithBasis:
    profile = "offline"

    def propose_brief(self, facts):
        return [
            BriefStatement(
                text=(
                    "Estimated restoration 2026-10-14T09:23:00+00:00 "
                    "(basis: computed_from_rates)."
                ),
                citations=[Citation(evidence_item_id="EV-ALM-FLD-001", field="kind")],
            )
        ]


def test_brief_bare_restoration_time_refused(fed):
    assembler = IncidentAssembler(
        fed.store, fed.chain, _BareTime(), cause=fed.cause, etr=fed.etr
    )
    with pytest.raises(EtrBasisRefusal):
        assembler.assemble(FLOOD, T0 + timedelta(minutes=30))
    assert fed.store.list_briefs(FLOOD) == []


def test_brief_wrong_time_with_basis_refused(fed):
    assembler = IncidentAssembler(
        fed.store, fed.chain, _WrongTimeWithBasis(), cause=fed.cause, etr=fed.etr
    )
    with pytest.raises(EtrBasisRefusal):
        assembler.assemble(FLOOD, T0 + timedelta(minutes=30))
    assert fed.store.list_briefs(FLOOD) == []


# -- closure and the accuracy ledger ----------------------------------------

CLOSE_AT = T0 + timedelta(minutes=300)
RESTORED_AT = T0 + timedelta(minutes=270)  # 06:43 UTC


def test_closure_compares_against_estimate_of_record(fed):
    estimate = fed.etr.estimate(FLOOD, T0 + timedelta(minutes=30))
    closure, outcome, record = fed.closure.close_incident(
        FLOOD,
        "op.dana",
        "operator",
        RESTORED_AT,
        "Vegetation crew cleared the tree contact at REC-071; Feeder 7 restored.",
        CLOSE_AT,
    )
    assert closure.closed_by == "op.dana"
    assert closure.restored_at == RESTORED_AT
    assert outcome.status == "compared"
    assert outcome.estimate_id == estimate.estimate_id
    assert outcome.estimated_duration_minutes == 250.0
    assert outcome.actual_duration_minutes == 270.0
    assert outcome.signed_error_minutes == 20.0
    assert outcome.absolute_error_minutes == 20.0
    # The closure fed the memory port: the flood's record exists,
    # closed at the recorded restoration, facts as classified.
    assert record.incident_id == FLOOD
    assert record.closed_at == RESTORED_AT
    assert record.cause_class == "vegetation_contact"
    assert fed.memory_port.get_record(record.record_id) == record
    # Closure and outcome are on the evidence chain.
    kinds = [i.kind for i in fed.store.list_evidence(FLOOD)]
    assert "closure" in kinds and "etr_outcome" in kinds
    assert fed.chain.verify_chain(FLOOD) is True

    ledger = fed.closure.accuracy_ledger()
    assert ledger["by_cause_class"] == [
        {
            "cause_class": "vegetation_contact",
            "compared_count": 1,
            "mean_signed_error_minutes": 20.0,
            "mean_absolute_error_minutes": 20.0,
            "max_absolute_error_minutes": 20.0,
        }
    ]
    assert ledger["overall"]["compared_count"] == 1
    assert ledger["unestimated_closures"] == 0


def test_closure_ignores_estimates_made_after_restoration(fed):
    fed.etr.estimate(FLOOD, T0 + timedelta(minutes=30))  # duration 250
    # A later estimate (as_of after the restoration) claims 175 —
    # it is not the estimate the incident was run against.
    fed.etr.estimate(FLOOD, T0 + timedelta(minutes=120) + timedelta(days=2))
    _closure, outcome, _record = fed.closure.close_incident(
        FLOOD, "op.dana", "operator", RESTORED_AT,
        "Vegetation crew cleared the tree contact; Feeder 7 restored.",
        CLOSE_AT,
    )
    assert outcome.status == "compared"
    assert outcome.estimated_duration_minutes == 250.0
    assert outcome.signed_error_minutes == 20.0


def test_closure_without_computed_estimate_records_the_gap(fed):
    fed.etr.estimate(LONE, LONE_T0 + timedelta(minutes=15))  # not_computable
    _closure, outcome, _record = fed.closure.close_incident(
        LONE,
        "op.dana",
        "operator",
        LONE_T0 + timedelta(minutes=60),
        "Breaker reclosed after inspection; no fault found.",
        LONE_T0 + timedelta(minutes=75),
    )
    assert outcome.status == "no_computed_estimate"
    assert outcome.estimate_id is None
    assert outcome.actual_duration_minutes == 60.0
    assert outcome.signed_error_minutes is None
    ledger = fed.closure.accuracy_ledger()
    assert ledger["by_cause_class"] == []
    assert ledger["unestimated_closures"] == 1


def test_closure_refusals(fed):
    with pytest.raises(ClosureRefusal):
        fed.closure.close_incident(
            FLOOD, "  ", "operator", RESTORED_AT, "Restored.", CLOSE_AT
        )
    with pytest.raises(ClosureRefusal):
        fed.closure.close_incident(
            FLOOD, "op.dana", "operator", T0 - timedelta(hours=1),
            "Restored before it broke.", CLOSE_AT,
        )
    fed.closure.close_incident(
        FLOOD, "op.dana", "operator", RESTORED_AT, "Restored.", CLOSE_AT
    )
    with pytest.raises(ClosureRefusal) as excinfo:
        fed.closure.close_incident(
            FLOOD, "op.dana", "operator", RESTORED_AT, "Restored again.", CLOSE_AT
        )
    assert any("already closed" in p for p in excinfo.value.problems)
    with pytest.raises(KeyError):
        fed.closure.close_incident(
            "INC-NOPE", "op.dana", "operator", RESTORED_AT, "Restored.", CLOSE_AT
        )
