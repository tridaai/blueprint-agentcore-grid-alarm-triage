# Release notes — 1.0.0

Grid Alarm Triage & Field Work Agent on AWS Bedrock AgentCore
(Trida AI Blueprint #19), for the fictional utility Kestrel
Power & Light. This is the frozen twenty-round build: every
gate re-run on the finished tree at release (see CHANGELOG.md
for the round-by-round account and the README's Honest status
for the per-round evidence).

## What a deployer gets

A governed grid alarm triage agent. Alarm streams arrive; the
agent correlates them into incidents by deterministic rules
over the network topology, assembles the evidence into a cited
incident brief, and stops — it holds no tool that can operate
the grid, and that is enforced mechanically, not by prompt.
In the box:

- The full incident lifecycle: append-only ingest →
  correlation under a versioned pack over versioned topology →
  severity by formula → root-cause classification and priors
  from memory → ETR computed in code, always basis-labelled →
  work-order drafting, assignment and the named-operator
  approval gate → field updates → restoration confirmed from
  signals → the gated close → partial-restoration splits,
  post-close contradictions, and the incident event log.
- Declared storm mode: named declare / extend / stand-down
  acts, every changed parameter stated in the storm pack, the
  stamp permanent on each incident's face, storm accuracy
  reported separately, and a post-storm review computed from
  artefacts.
- The governance layers: an access pack judging every
  principal-carrying call, with existence-hidden incident
  reads and an insert-only denial log; a model gateway with a
  cost ledger and per-incident budgets that refuse on the
  record; an injection quarantine over the two outside text
  channels, held text treated as absent; topology declarations
  under dual control — since Round 17 the only route; shift
  handovers bound to the record hash; operations oversight
  reports and post-incident reviews, each verified by a second
  path; and the read-only operator console.
- The AgentCore Policy pack (Round 18): the tool table and
  Cedar permits as data, a decision engine that re-performs
  them in code, drift verification against the live catalogue
  in both directions, and Gateway denial detection (HTTP 200 +
  JSON-RPC −32002) in code.
- The gates that grade it all: the pytest suite, an eval gate
  with zero-tolerance counters, eight check scripts that plant
  forgeries, tampers and trapped text against the record, and
  a demo that walks the planted streams end to end.

## How to run it

```bash
make install          # uv sync --extra dev (pinned, frozen constraints)
make demo             # the planted streams walked end to end
make test             # pytest (SQLite by default)
make evals            # the eval gate (zero-tolerance counters)
make reports-check    # the oversight report check (plants forgeries)
make ledger-check     # the model ledger check (plants a tamper)
make quarantine-check # the quarantine check (plants trapped text)
make reviews-check    # the incident review check
make topology-check   # the topology dual-control check (plants forgeries)
make policy-check     # the AgentCore Policy pack check
make red-team-check   # the second red team check (plants the RT-19…24 attacks)
make serve            # the AgentCore Runtime app on :8080
make api              # the operations API on :8000
make invoke           # local in-process invoke of the agent step
```

The console check runs as `uv run python scripts/check_console.py`
(the graded page behind `GET /console`).

No `uv`? `make install-pip` is the two-step fallback: install
the frozen `constraints.txt`, then the project with
`--no-deps -e .`.

**Postgres:** set `DATABASE_URL` and the same code runs on
PostgreSQL — migrations 0001–0015 ship in both dialects through
the store's runner. Stated plainly: the release's verified
store is SQLite. No Postgres parity leg was run for this repo
(see the honest limits), so a deployment that runs Postgres
should run the suite against it before trusting the profile.

**Configuration** is environment-only; see `.env.example`. The
offline deterministic model profile is the default; the Bedrock
profile refuses loudly until a region and model id are
configured — it never falls back silently.

`docker compose up` brings up the agent, Postgres+pgvector and
the SeaweedFS S3 gateway in the portfolio's standard shape; the
Dockerfile is multi-stage and non-root, and AgentCore Runtime
deploys want an ARM64 image
(`docker buildx build --platform linux/arm64`).

## The freeze

Every gate below was run on the release tree at 1.0.0; the
figures are the gate outputs, not summaries of earlier rounds.

- **pytest:** 358 passed.
- **Eval gate:** PASS — 110 zero-tolerance counters, every one
  at 0.
- **Checks:** console, reports, model ledger, quarantine,
  reviews, topology, policy and red-team checks — each PASS,
  each grading its claims from planted records (forgeries, a
  tamper, trapped text, the RT-19…24 attacks) rather than from
  the surfaces under test.
- **Demo:** exit 0 — the planted streams walked end to end,
  flood to gated close, storm, dual control and the Gateway
  policy beat included.

## The honest limits

- **No live AWS call of any kind has been made from this
  build.** No live AgentCore, Bedrock or Gateway call: the
  Runtime contract is exercised for real against the installed
  `bedrock-agentcore` SDK over ASGI transport in tests, and
  everything beyond it is a port, a local double, or a pack
  waiting on a deployment.
- **The hosted model profiles are stub-tested and
  fail-closed.** The offline deterministic profile is the
  graded path; the Bedrock profile refuses loudly until
  configured, the gateway records the refusal as an errored
  call, and nothing proceeds unpriced or unrecorded.
- **The Policy pack is unattached to a live Gateway.** It is
  shipped and graded in-repo — table, permits, decision
  engine, drift verification, denial detection. Attaching it
  to a real Gateway, binding the Cedar action namespace the
  deployment's Gateway target publishes, and standing up the
  targets themselves are the deployment's acts.
- **The quarantine screen has stated vocabulary limits
  (RT-25).** Instruction shape is a vocabulary, not a proof:
  sub-threshold encoded splits, hex encoding and
  sentence-break evasions are outside the pack's stated rules,
  pinned in tests so widening the vocabulary is a deliberate
  act. The pack will also hold innocent text that quotes an
  instruction — a held note is a routing event, not an
  accusation.
- **No Postgres parity leg.** The suite's verified store is
  SQLite; migrations 0001–0015 ship in both dialects and the
  runner's Postgres boot bug was found and fixed in Round 7,
  but cross-dialect parity was not re-performed for this
  release. The payload contract is the port's stated shape in
  both dialects; a Postgres deployment runs the suite against
  its database first.
- **Access enforcement governs calls that carry a
  principal.** Principal-less calls (the CLI, the local
  harness) remain the trusted-local operations path they have
  been since Round 1; in production, AgentCore Identity
  terminates caller identity in front of these surfaces and
  every call carries one. The pack governs authorisation; who
  a principal id belongs to is the identity layer's question.
- **No real SCADA/OMS is connected.** Ingest is a port with a
  planted reference feed, and restoration signals arrive
  through a recording port with planted local readings — a
  confirmation is only ever as good as the signals recorded
  against it. The production posture keeps a historian
  read-replica between the agent and any operational system;
  the agent never sits on a control network.
- **Model costs are pack-stated, not provider-billed.** The
  ledger's token figures come from the pack's stated
  measurement rule over canonical sizes and its rates are
  stated reference rates (the offline profile at zero). A
  deployment states its own contracted rates by superseding
  the pack.
- **The Docker image build is reviewed statically**, not
  run — the build sandbox has no Docker daemon; the Dockerfile
  and compose contracts are graded by
  `tests/test_docker_contract.py`.
- **Session hand-off at the Runtime's 8-hour edge is not
  built**; it is a documented deployment pattern. Storm
  reporting cadence and customer communications are likewise
  not part of this build — notifications here are drafts,
  never sent, and there is no send surface.
- **The README's work-in-progress notice stays on.** The
  automated gates are green; hands-on developer testing and
  the companion blog post are still outstanding, and the
  notice comes off when both are done — not before.

## What a deployment still has to do

- Wire an AWS account: configure the Bedrock profile (region +
  model id), build the ARM64 image (the build itself, not the
  static review), and deploy the Runtime entrypoint.
- Attach the Policy pack to a real AgentCore Gateway, bind its
  action namespace, and stand up the Gateway targets (historian,
  OMS, AMI/meter reads) behind the tool catalogue's contracts —
  including the target's duty of resolving each call's target
  feeders, since the area condition judges what the target
  states and an unresolvable scoped call fails closed.
- Terminate caller identity (AgentCore Identity) in front of
  the API and Runtime surfaces so every call carries a
  principal.
- Connect the real feeds behind the ports: alarm ingest from
  the historian read-replica, restoration signals from the
  meter/OMS sources.
- Point `DATABASE_URL` at PostgreSQL 16 if that is the chosen
  store, and run the full suite and eval gate against it —
  the parity leg this release states it did not run.
- State contracted model rates by superseding the model pack,
  and set per-incident budgets to the operation's own numbers.
- Decide the session hand-off pattern if Runtime sessions are
  expected to approach the 8-hour envelope during storm
  events.
