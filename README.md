<p align="center">
  <img src="https://trida.ai/logos-rounded/trida-rounded.png" alt="Trida AI" width="96">
</p>

# Blueprint — Grid Alarm Triage & Field Work Agent (AWS Bedrock AgentCore)

A working prototype of the agent a grid operations team needs
between its alarm streams and its control room: SCADA/OMS alarms
arrive in floods, and the agent **correlates them into incidents by
deterministic rules over the network topology, assembles the
evidence into a cited incident brief, and stops**. A named operator
will authorise every field action in later rounds. The system can
read, draft and notify — **it holds no tool that can operate the
grid, and that is enforced mechanically, not by prompt**.

The blueprint's thesis is the **AWS Bedrock AgentCore-native path**:
the agent runs behind the real `BedrockAgentCoreApp` from the
`bedrock-agentcore` SDK (the Runtime contract — `POST /invocations`,
`GET /ping`, port 8080, ARM64 container), its triage work is a
Strands-shaped agent step behind a model port (offline deterministic
profile by default; a Bedrock profile that refuses loudly until an
AWS account is wired), and the AgentCore services it will grow into
— Gateway, Policy, Memory, Identity, Evaluations — are mapped
honestly in `docs/architecture.md`, as ports and local doubles where
they are not yet live.

The promises are precise, and they are enforced in code:

- **No control, ever.** The tool catalogue contains read, draft and
  notify shapes only. A source-scanning test proves no
  control-shaped tool exists in the registry — a switching,
  reclosing, shedding or setpoint tool cannot be added without
  failing the gate. (Round 11 adds the AgentCore Policy pack as the
  platform layer of the same law.)
- **Correlation is rules, not vibes.** Alarm-to-incident grouping
  runs a versioned correlation pack over versioned topology data.
  Every incident records the exact alarm ids **and the rule ids**
  that formed it; every incident pins the topology version and the
  pack version it was correlated under.
- **Every alarm is evidence.** Ingest is append-only. Alarms that
  correlate to nothing stay visibly uncorrelated — correlation
  suppresses nothing, deletes nothing. Correlated alarms are
  hash-chained per incident, so altered history fails verification
  mechanically.
- **Every brief statement carries a citation** to a specific member
  alarm and field, and a citation that does not resolve is refused
  in code — the whole brief, not just the sentence.

This is an independent Trida AI blueprint built around a fictional
utility, Kestrel Power & Light. It is **not affiliated with or
endorsed by Kestrel, Amazon Web Services, or any regulator**; it
shows how a forward-deployed team would put a grid alarm triage
agent into production on AgentCore inside a utility operations
programme.

> **Work in progress** — this blueprint is built and passing its automated test and eval gates, but it has not yet finished hands-on local testing, and its companion blog post is not yet published. Expect changes. This notice comes off when both are done.

---

## What Round 1 builds

- **Alarm/incident domain + versioned topology**
  (`grid_triage/models.py`, `grid_triage/topology.py`,
  `data/topology/v1.json`): the fictional Kestrel Power & Light
  territory — 2 substations, 3 feeders, 13 devices as a
  parent-linked tree, 3 critical loads — stored as data files and
  loaded into a relational index keyed by topology version.
- **Append-only ingest port + reference feed**
  (`grid_triage/services.py`, `grid_triage/seed.py`): three planted
  streams generated deterministically — a 40-alarm feeder flood from
  one root cause, an 8-alarm comms nuisance stream, and a lone
  breaker trip. Re-delivery of an alarm id is refused whole.
- **Correlation pack v1** (`grid_triage/correlation.py`): anchors
  open incidents; later protection operations join the open
  incident; consequence alarms join by hierarchy or feeder window;
  comms/telemetry kinds never anchor and never join. Planted truth:
  the flood is **exactly one incident with exactly the 40 planted
  alarms**; the nuisance stream creates **no incident**.
- **Hash-chained evidence** per incident over an S3-shaped object
  port (local directory double; Amazon S3 in production), with a
  `verify_chain` that detects altered payloads.
- **The agent step** (`grid_triage/agent.py`): incident brief
  assembly behind a model port — offline deterministic composer by
  default, Bedrock profile refusing loudly until configured — with a
  citation resolver that refuses any brief citing a non-member
  alarm.
- **AgentCore Runtime entrypoint** (`grid_triage/runtime_app.py`):
  `POST /invocations` + `GET /ping` via the real
  `BedrockAgentCoreApp`, plus an in-process local harness
  (`grid-agent-invoke`) and an operations API (`grid_triage/api.py`)
  for ingest, alarms, incidents, topology and chain verification.
- **Store port**: SQLite local default (`./data/grid.db`),
  PostgreSQL dialect behind the same implementation, numbered
  migrations in both dialects.

## Quick start

```bash
make install          # uv sync --extra dev
make demo             # planted streams: flood in, one incident out
make test             # pytest
make evals            # the eval gate (zero-tolerance counters)
make serve            # AgentCore Runtime app on :8080
make api              # operations API on :8000
make invoke           # local in-process invoke of the agent step
```

## Honest status

What is built and what is not, plainly:

- **Severity is Round 2.** Incidents carry no severity band yet;
  the formula (customers × critical loads × duration) lands next
  round. Critical-load data is already in the topology.
- **ETR is Round 4, work orders Round 5, the operator approval
  gate Round 6, storm mode Round 9.** In R1 the agent assembles
  briefs and stops; nobody is dispatched and nothing is approved.
- **The AgentCore Policy pack is Round 11.** In R1, Law 1 (no
  control) is enforced by the tool catalogue's construction and
  the registry scan; the Cedar policy pack at the Gateway is the
  platform layer, added with the Gateway port.
- **No real SCADA/OMS is connected.** Ingest is a port with a
  planted reference feed. The blueprint's production posture keeps
  a historian read-replica between the agent and any operational
  system; the agent never sits on a control network.
- **Topology and streams are fictional**, generated for this repo.
- **Live AWS calls are stub-tested until an account is wired.**
  The Runtime contract is exercised for real against the installed
  `bedrock-agentcore` SDK (ASGI transport, in tests); the Bedrock
  model profile refuses loudly rather than falling back; no live
  Bedrock or AgentCore call is made by the test or eval gates.
- **The Docker image build is not run in this sandbox** (no Docker
  daemon); the Dockerfile and compose contracts are graded
  statically by `tests/test_docker_contract.py`.

## Layout

```
src/grid_triage/   models, topology, correlation, services (ingest),
                   evidence, store, agent, tools (Law 1 registry),
                   runtime_app / runtime_main (AgentCore Runtime),
                   api, demo, cli, local_invoke, wiring, config
data/topology/     versioned territory data (v1.json)
migrations/        numbered SQL migrations, sqlite + postgres trees
tests/             pytest suite (36 tests in R1)
evals/             the eval gate (run_evals.py)
docs/              architecture.md — the honest AgentCore mapping
```

Apache 2.0. See `LICENSE`.
