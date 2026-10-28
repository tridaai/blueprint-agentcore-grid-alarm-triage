# Architecture — Grid Alarm Triage & Field Work Agent

## Thesis

This blueprint takes the **AgentCore-native path**: the agent is an
Amazon Bedrock AgentCore Runtime application, its tools are shaped
for AgentCore Gateway, its guardrail law (no control of the grid)
is designed to be enforced twice — once by construction in the
agent's tool catalogue, and once at the platform layer by an
AgentCore Policy pack on the Gateway (Round 11). Everything the
domain needs that AgentCore does not provide — deterministic
correlation, hash-chained evidence, citation-verified briefs —
lives in the blueprint's own code, behind ports, so the local
doubles used in tests are the same shapes the production services
fill.

## The AgentCore mapping, honestly

| AgentCore service | Production role | In this repo (R1) |
| --- | --- | --- |
| **Runtime** | The agent's home: one microVM session per triage session, HTTP contract on port 8080 (`POST /invocations`, `GET /ping`), ARM64 container, sessions to the 8-hour envelope (storm events; hand-off at the edge is a documented pattern, not yet built) | **Real.** `grid_triage/runtime_app.py` serves the contract via the installed `bedrock-agentcore` SDK's `BedrockAgentCoreApp`; graded over ASGI transport in `tests/test_runtime_contract.py`. |
| **Gateway** | SCADA-adjacent historian, OMS and AMI/meter systems exposed as read-only MCP targets — the single tool path | **Port-shaped.** The tool catalogue (`grid_triage/tools.py`) is the contract the Gateway targets will implement; the MCP wiring lands in Round 11. No Gateway is called in R1. |
| **Policy** | Cedar pack on the Gateway: allow read/draft/notify, deny every control-shaped call, deny out-of-area reads; denials arrive as HTTP 200 + JSON-RPC −32002 and must be detected in code | **Designed, not yet shipped.** R1 enforces Law 1 by construction + registry scan. The pack itself is Round 11. |
| **Memory** | Episodic memory of prior incidents per feeder/device, feeding briefs (and, later, correlation) as recorded inputs | **Port + local double (R3).** `grid_triage/memory.py` defines the port in the managed service's event-memory shape — closed incidents as records of fact — served locally by a double over the store. Records are insert-only, retrieval is weighted and deterministic, and briefs cite records directly. No live AgentCore Memory call is made. |
| **Identity** | Operator / dispatcher / field / auditor roles; control-room caller identity on every act | **Not in R1.** Access control is Round 16; the acts R1 records (ingest, correlation, brief) are system acts by design. |
| **Evaluations** | AgentCore Evaluations on live traces (correctness/faithfulness), sampled online | **Repo-side only in R1.** The deterministic eval gate (`evals/run_evals.py`) is the primary gate; AgentCore evaluator configs join the infra artefacts at release hardening (R20). |
| **Observability** | OpenTelemetry traces to CloudWatch as the incident audit trail | **Shaped.** Structured JSON logs with session/incident ids (`grid_triage/logging_config.py`); the console trace timeline is Round 13. |
| **Bedrock models** | The model behind the agent step (Strands `BedrockModel`) | **Port.** Offline deterministic composer by default; the Bedrock profile refuses loudly until a region + model id are configured. No live call in R1's gates. |

## Domain design

**Alarms are evidence (Law 7).** The `alarms` table is an
append-only ingest log with a sequence number; the store port
exposes inserts and reads only. Re-delivery of an alarm id is
refused whole — history is never overwritten.

**Correlation is a pure function (Law 2)** of (uncorrelated alarms,
pinned topology, versioned pack). It runs in
`grid_triage/correlation.py` with no store access, which is what
makes the planted-truth grading exact: feed the planted streams,
compare membership sets. Pass 1 places anchors (protection/fault
kinds); an anchor inside the window of an open same-feeder incident
joins it (`anchor_joins_open_incident`) instead of opening a
competing incident. Pass 2 joins consequence alarms, preferring
incidents whose anchor device is in an ancestor/descendant relation
(`hierarchy_window`) over a plain same-feeder window match
(`feeder_window`). Comms/telemetry kinds are excluded from both
anchoring and joining in pack v1 and stay uncorrelated with that
recorded reason.

**Topology is versioned data (Law 6).** `data/topology/v1.json` is
loaded verbatim into `topology_versions` and indexed into
`topology_*` tables. An incident stores its `topology_version`;
loading is insert-once — a version, once loaded, is immutable.

**Evidence chains.** When correlation forms an incident, each
member alarm's payload is appended to the incident's hash chain
(`grid_triage/evidence.py`) over the S3-shaped object port. The
chain is what the brief's citations resolve against: a citation
names an evidence item (one member alarm) and a field inside its
payload.

**The agent step** is a Strands-shaped loop behind a model port:
facts are read from evidence, the model proposes statements, a
citation resolver in code checks every citation, and only a fully
resolving brief is stored. The loop's tools are the registry's
read/draft shapes (`grid_triage/tools.py`); the registry is also
the Law 1 enforcement point — its shapes are an enum with no
control value, and a test scans it for control verbs.

**Severity is a formula, classification is a pack (Laws 3 + R3).**
The severity engine (R2) computes the band from recorded inputs
under a versioned formula pack; assessments are content-addressed
and supersede by link. The classification pack (R3) assigns a
probable-cause *class* from the anchor alarm's pattern under
ordered rules — first match wins, the rule id is recorded, and a
non-matching pattern is `unknown`, never forced — because the
class is consumed downstream as a fact (retrieval weighting,
reporting), and a fabricated class would contaminate both.

**Memory is records of fact, insert-only (R3).** A closed
incident becomes a memory record assembled from its stored facts
only — classification of record, current severity assessment,
membership — plus the resolution summary recorded at close. The
closure act itself is R4 (`grid_triage/closure.py`): named
operator, restored timestamp, resolution summary, exactly one per
incident. The
port exposes put/get/list/retrieve and nothing else: no update,
no delete, and the store refuses a re-insert under an existing
record id with different content. Retrieval scores candidates
under stated weights (class 4, feeder 3, anchor device 2, device
overlap 1, shared terms capped 3) and requires a structural basis
— same feeder, same anchor device, or a shared device — so term
overlap ranks but never qualifies. Brief citations may name a
memory record id; the resolver checks the cited field against the
record as stored, and a brief attributing a class to a prior that
the record does not state is refused whole.

**Evidence packs are sealed manifests (R3).** Sealing (a named
act) snapshots the incident's artefacts of record — member alarms
with chain references, correlation decisions, the current
severity assessment, the topology slice at the pinned version,
the latest brief, the retrieved priors — each member carrying the
hash of its content, under a manifest hash. Packs supersede by
version, never by edit; verification recomputes every member from
its source of record and the evidence chain whole, so an old pack
still verifies after its members are superseded — the artefacts
it points at are themselves immutable.

**ETR is a stated formula over versioned packs (R4).** The
estimate (`grid_triage/etr.py`) is a pure computation of the
estimate-of-record inputs: the cause class of record, the anchor
alarm's device kind, a **rate pack** (repair base minutes per
cause × device-kind, travel minutes per substation pair) and a
**roster artefact** (`data/roster/v1.json`: crews, skills, shift
windows, current assignments). The crew chosen is the qualified
crew minimising wait + travel; the total is wait + travel + base,
each component a recorded line on a content-addressed estimate
pinned to (incident, pack version, roster version, as_of).
Estimates supersede by link with the revision delta and the
changed inputs named, and an identical recomputation is a no-op.
**The basis label is the law:** a computed estimate carries
`computed_from_rates`; when any input is missing — unknown cause,
no qualified crew, no base rate, no travel pair — the estimate is
`not_computable` naming the input, and it carries no clock time at
all. The brief verifier enforces the label at the sentence level:
a restoration statement in a brief must quote the estimate's own
time with its basis, or the brief is refused. **Closure closes
the loop:** the closure act records the actual restored time, the
outcome compares it to the estimate of record (latest estimate
with as_of ≤ restored_at), and the accuracy ledger derives its
per-class means from the recorded outcomes — the track record a
brief may quote is the one the ledger recorded, nothing else.

## Stores

One store port, two dialects (`grid_triage/store.py`): SQLite
locally (`./data/grid.db`), PostgreSQL in production, JSON-text
payloads in both, numbered migrations under `migrations/{sqlite,
postgres}/` applied by a ledger-recording runner. Object payloads
sit behind an S3-shaped port (local directory double; Amazon S3 in
production). The compose profile runs the production shape:
pgvector/pgvector:pg16 + a SeaweedFS S3 gateway (service name
`minio`, the portfolio convention).

## Deployment shape

The Dockerfile is a multi-stage, non-root, ARM64-noted build
serving `grid-agent-serve` on 8080 — the AgentCore Runtime
container contract. Locally, `make serve` binds the same contract;
`make api` runs the operations API on 8000; `grid-agent-invoke`
runs the exact Runtime handler in-process against the planted feed.
