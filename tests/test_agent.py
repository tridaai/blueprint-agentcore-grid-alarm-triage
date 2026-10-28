"""The agent step: cited brief assembly, and refusal in code when
a citation does not resolve."""

from datetime import datetime, timezone

import pytest

from grid_triage.agent import BedrockProfile, CitationRefusal, ModelNotConfigured
from grid_triage.models import BriefStatement, Citation

NOW = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)


def test_brief_assembles_fully_cited(fed):
    brief = fed.assembler.assemble("INC-ALM-FLD-001", NOW)
    assert brief.statements
    assert all(s.citations for s in brief.statements)
    assert fed.assembler.resolver.problems("INC-ALM-FLD-001", brief.statements) == []
    assert brief.model_profile == "offline"


class _BadModel:
    profile = "offline"

    def propose_brief(self, facts):
        return [
            BriefStatement(
                text="A statement citing an alarm that is not a member.",
                citations=[Citation(evidence_item_id="EV-ALM-NUI-001", field="kind")],
            )
        ]


def test_unresolvable_citation_refuses_the_whole_brief(fed):
    from grid_triage.agent import IncidentAssembler

    assembler = IncidentAssembler(fed.store, fed.chain, _BadModel())
    with pytest.raises(CitationRefusal):
        assembler.assemble("INC-ALM-FLD-001", NOW)
    assert fed.store.list_briefs("INC-ALM-FLD-001") == []


def test_bedrock_profile_refuses_until_configured():
    with pytest.raises(ModelNotConfigured):
        BedrockProfile(region=None, model_id=None).propose_brief([])


def test_assemble_unknown_incident_raises(fed):
    with pytest.raises(KeyError):
        fed.assembler.assemble("INC-NOPE", NOW)
