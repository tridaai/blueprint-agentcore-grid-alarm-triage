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

## What Round 2 builds

- **The severity engine** (`grid_triage/severity.py`): a versioned
  formula pack computes each incident's band from customers
  affected (topology data over the anchor's outage slice),
  critical-load weights, duration and alarm breadth. Assessments
  are content-addressed, record every input and weight, and
  supersede by link — never edited. A band the data cannot
  support is `UNRATED` with the missing inputs named. Bands at
  escalation thresholds require a named operator's
  acknowledgement, and the brief may restate only the assessed
  band — a mismatched band refuses the brief in code.

## What Round 3 builds

- **Root-cause classification** (`grid_triage/cause.py`): a
  versioned rule pack over the anchor alarm's pattern assigns a
  probable-cause class (vegetation contact, equipment failure,
  lightning) with the rule that fired, recorded on the incident's
  evidence chain. A pattern no rule matches stays `unknown` —
  never forced into the nearest class.
- **The memory port** (`grid_triage/memory.py`): closed incidents
  live on as episodic memory records — feeder, devices, cause
  class, computed band, and the resolution as recorded at close.
  In production this port is Amazon Bedrock AgentCore Memory; here
  it is a **local double over the store port, labelled as such**.
  Records are insert-only: the port has no update and no delete,
  and the store refuses a re-insert that would change a record's
  facts. Retrieval for a live incident is deterministic — stated
  weights for same class (+4), same feeder (+3), same anchor
  device (+2), device overlap (+1) and shared terms (capped +3) —
  and a record qualifies only on a structural basis, never on
  wording alone.
- **Briefs that cite priors**: the assembler classifies the
  incident, retrieves its priors, and hands them to the model as
  facts. A prior may be quoted only as recorded — citations
  resolve against the memory records themselves, and a statement
  attributing a cause class to a prior that its record does not
  state **refuses the whole brief**.
- **Sealed evidence packs** (`grid_triage/packs.py`): a named
  operator seals everything an incident rests on — member alarms
  with their chain references, the correlation decisions, the
  current severity assessment, the topology slice at the pinned
  version, the latest brief, and the priors retrieved — into a
  manifest whose hash covers every member. Packs are never
  edited: resealing after a member is superseded produces the
  next version, linked to the last, and the old pack still
  verifies. Verification recomputes every member hash from its
  source of record, plus the evidence chain; drift comes back as
  named problems.
- Migration 0003 (both dialects), and classification / memory /
  priors / pack surfaces at parity across the operations API, the
  Runtime invocation handler and the CLI.

## What Round 4 builds

- **The ETR engine** (`grid_triage/etr.py`): restoration estimates
  computed in code from a stated formula — crew wait (next
  available qualified crew from the roster) + travel minutes
  (rate pack, per substation pair) + repair base minutes (rate
  pack, per cause class × device type). The rate pack is a
  versioned artefact, as is the **crew roster**
  (`data/roster/v1.json`: crews, skills, shift windows, current
  assignments), and every estimate is content-addressed, pinned
  to (incident, rate pack version, roster version, as_of), with
  each component a recorded line.
- **Basis labelling (the law)**: every ETR carries its basis —
  `computed_from_rates` when every input exists. When an input is
  missing (unknown cause class, no qualified crew on roster, no
  base rate, no travel pair), the ETR is `not_computable` naming
  the input, and the system never emits a guessed clock time. The
  brief verifier enforces it: a brief stating a restoration time
  without the basis label — or quoting a time that is not the
  estimate's own — is refused whole.
- **Revisions and the accuracy ledger**: re-estimating as facts
  land supersedes by link, with the total delta and the changed
  inputs named; an identical recomputation is a no-op. A minimal
  named **closure act** records the actual restored time (and
  feeds the Round 3 memory record); the closure compares the
  actual duration against the estimate of record and the
  per-cause-class accuracy ledger is derived from those recorded
  outcomes — the ETR track record a brief may quote is only the
  one the ledger recorded.
- Migration 0004 (both dialects), and ETR / roster / closure /
  accuracy surfaces at parity across the operations API, the
  Runtime invocation handler and the CLI. The demo now walks the
  flood through estimate → closure → ledger.

## What Round 5 builds

- **Work-order drafting** (`grid_triage/work_orders.py`): a
  draft is derived in code from the records — scope from the
  classification of record and the affected devices on the
  incident's pinned topology, safety notes and materials from a
  versioned **work pack** (2026.10.1), the crew qualification
  stated in the roster's skill vocabulary, and the R4 ETR
  estimate pinned by id. What the data cannot supply is named
  in `gaps`: an unclassified incident's draft states no
  qualification, because there is no honest skill for "not yet
  diagnosed". A draft authorises nothing, and drafts are
  content-addressed versions — a change is a new version
  superseding by link, never an edit.
- **Assignment as a recorded act**: an operator assigns a named
  crew and a work window; the checks run in code — the crew
  must carry the draft's stated qualification on the roster,
  and must not already be committed, on the roster artefact or
  on another open work order, over the window (a refusal names
  the conflicting commitment or assignment). The draft the act
  names stays byte-identical.
- **The operator approval gate (Law 5)**: approval is a named
  operator's act in a pack-stated role, against the current
  version only, and it pins that exact version — a later
  revision stands in draft and needs its own assignment and
  approval, while the approval stays on the version it decided.
  The operator who drafted or last revised a version cannot
  approve it (a system draft has no human drafter, so any named
  operator may). Approval requires a crew assignment; rejection
  requires its reason, and a rejected version cannot be approved
  afterwards — the way forward is a new version.
- **Notifications are drafts (Law 1)**: approval drafts the
  field-crew and customer notifications as records whose own
  text marks them unsent, quoting the pinned ETR only with its
  basis label. There is no send surface — the eval gate scans
  the routes, the Runtime actions and every function name for
  one.
- Migration 0005 (both dialects), and work-order surfaces at
  parity across the operations API, the Runtime invocation
  handler and the CLI. The demo now walks the flood's order
  through draft → refused assignment → revision → assignment →
  refused self-approval → approval → drafted notifications.

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

- **Severity is computed (Round 2)** by a versioned formula pack
  — customers × critical loads × duration × breadth — with
  content-addressed assessments and an operator acknowledgement
  queue for escalation bands. **Classification, memory and
  evidence packs are Round 3** (above).
- **Memory here is a local double.** The memory port's production
  shape is Amazon Bedrock AgentCore Memory; nothing in this build
  has called it. The double exists so the record shape, the
  insert-only discipline and the retrieval arithmetic are built
  and graded offline.
- **ETR is computed (Round 4)** from a versioned rate pack and a
  versioned roster, and every ETR carries its basis —
  `computed_from_rates`, or `not_computable` naming the missing
  input; closures grade the estimates into an accuracy ledger.
- **Work orders are drafts with an operator gate (Round 5)**:
  the system drafts, assigns and records; only a named
  operator's approval act — role-checked, never the drafter of
  a human-touched version — moves a version to approved, and
  the notifications it produces are drafts, never sent. Field
  updates and re-correlation are Round 7, restoration
  confirmation Round 8, storm mode Round 9. Nothing here
  dispatches a crew in the world; it records who decided what.
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
                   severity (R2), cause / memory / packs (R3),
                   roster / etr / closure (R4), work_orders (R5),
                   runtime_app / runtime_main (AgentCore Runtime),
                   api, demo, cli, local_invoke, wiring, config
data/topology/     versioned territory data (v1.json)
data/roster/       versioned crew roster artefact (v1.json)
migrations/        numbered SQL migrations, sqlite + postgres trees
tests/             pytest suite (102 tests through R4)
evals/             the eval gate (run_evals.py)
docs/              architecture.md — the honest AgentCore mapping
```

Apache 2.0. See `LICENSE`.
