"""The AgentCore Runtime contract, exercised over ASGI transport
against the real ``BedrockAgentCoreApp``: ``GET /ping`` is Healthy,
``POST /invocations`` runs the agent step, and an alarm batch comes
back correlated with briefs."""

import asyncio

import httpx
import pytest

import grid_triage.runtime_app as runtime_app
from grid_triage.seed import reference_feed

SESSION = {"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "s" * 40}


@pytest.fixture()
def client(fed):
    runtime_app._services = fed
    transport = httpx.ASGITransport(app=runtime_app.app)

    async def _call(method, url, **kw):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.request(method, url, **kw)

    return lambda method, url, **kw: asyncio.run(_call(method, url, **kw))


def test_ping_is_healthy(client):
    response = client("GET", "/ping")
    assert response.status_code == 200
    assert response.json()["status"] == "Healthy"


def test_invocations_assembles_brief(client):
    response = client(
        "POST",
        "/invocations",
        json={"action": "assemble_brief", "incident_id": "INC-ALM-FLD-001"},
        headers=SESSION,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "assembled"
    assert body["brief"]["incident_id"] == "INC-ALM-FLD-001"
    assert body["brief"]["statements"]


def test_invocations_ingest_and_brief(services):
    runtime_app._services = services
    transport = httpx.ASGITransport(app=runtime_app.app)
    alarms = [a.model_dump(mode="json") for a in reference_feed()]

    async def _call():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post(
                "/invocations",
                json={"action": "ingest_and_brief", "alarms": alarms},
                headers=SESSION,
            )

    body = asyncio.run(_call()).json()
    assert body["status"] == "correlated"
    assert body["incidents_created"] == ["INC-ALM-FLD-001", "INC-ALM-LONE-001"]
    assert len(body["briefs"]) == 2


def test_invocations_unknown_action(client):
    response = client("POST", "/invocations", json={"action": "teleport"}, headers=SESSION)
    assert response.json()["status"] == "unknown_action"


def test_invocations_unknown_incident(client):
    response = client(
        "POST",
        "/invocations",
        json={"action": "assemble_brief", "incident_id": "INC-NOPE"},
        headers=SESSION,
    )
    assert response.json()["status"] == "no_such_incident"
