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
  failing the gate. (A later round adds the AgentCore Policy pack as
  the platform layer of the same law.)
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

## What Round 6 builds

- **Field updates** (`grid_triage/revisions.py`): crews and
  dispatchers post updates against an incident — a new alarm
  observation, a crew note, a damage finding — as insert-only
  artefacts under a versioned update pack, each naming its
  reporter and role. An update carrying a new alarm re-runs
  correlation for that alarm under the incident's **pinned**
  topology version, and the outcome is recorded on the update:
  joined (with the rule that placed it) or a named non-join. An
  alarm from a device that exists only in a newer topology is
  `unresolvable_under_pinned_topology` — never silently
  re-correlated under the newer version.
- **Topology v2 as data + a declaration act**: v2 (a Feeder 7
  split onto a new feeder FDR-07B, a new line sensor, a replaced
  regulator, a new hospital load) ships as a version payload
  with its change set stated in the data. It takes effect only
  through a named operator's declaration act with an effective
  time — there is no silent migration, and v1 stays fully
  readable.
- **Re-correlation is a revision, not an edit (Law 6)**: a
  named operator's act re-correlates an open incident under v2,
  producing a new incident linked by a revision record that
  states the diff — alarms and devices added and removed
  (membership follows the v2 device→feeder mapping, so the
  split section leaves the flood's group: 13 alarms out, the
  new sensor's alarm in), the severity recomputed on both sides
  at the same instant with the moved formula inputs named
  (the flood: SEV-1 85 → SEV-2 64), and the ETR estimates on
  both sides linked by id. The prior incident's artefacts stay
  byte-identical. Closed incidents are never re-correlated —
  the refusal names the closure.
- Migration 0006 (both dialects), and update / declaration /
  re-correlation surfaces at parity across the operations API,
  the Runtime invocation handler and the CLI. The demo now
  walks the flood through a field update, the v2 declaration,
  and its re-correlation diff.

## What Round 7 builds

- **Restoration is confirmed from signals, in code**
  (`grid_triage/restoration.py`): restoration signals are
  readings recorded against an incident — per-device readings
  from the meter/OMS-shaped sources, a feeder supply reading
  from the topology-shaped layer. The **confirmation** is a
  computed artefact under a versioned restoration pack: every
  required evidence class (meter restoration, feeder supply
  reading, crew confirmation from the incident's field updates)
  is named present or absent with the artefact ids behind the
  verdict, and restored vs outstanding customers are summed
  device by device over the incident's outage set. It reads
  `complete` only when every class is present and every device
  is restored; anything less reads `partial`. A device's current
  state is its latest signal's state — a later `still_out`
  reading supersedes an earlier `restored` one by record, never
  by edit.
- **The gated close**: an incident closes only through a named
  operator's close act binding (a) a complete confirmation,
  (b) the Round 4 closure and its ETR-vs-actual outcome — the
  accuracy-ledger entry, linked by id and never duplicated —
  and (c) the work-order position: the current work order's
  **completion act** (new in this round — an approved order
  finally has a "completed" fact) recorded, or the order
  explicitly waived by the closer with a reason. Closing with
  open items is refused with the items named. The close is an
  insert-only act; the incident row is never mutated. The R4
  minimal closure remains for the recorded facts — the gate is
  what earns it.
- **Partial restoration is split, never closed whole**: when
  part of an incident's customer set is restored, a named act
  divides it into a restored part and a continuing incident
  following the Round 6 revision pattern — new incident rows,
  memberships partitioned by the confirmation's device sets,
  the partition and its customer counts stated on the split
  record, the source untouched. A split part inherits the
  source's signals and crew updates through the split link, so
  the restored part can be confirmed and closed on its own
  evidence while the rest stays open.
- **Post-close contradictions**: a closed incident is never
  reopened and never re-correlated. A post-close alarm on a
  closed incident's device set is recorded as a
  `post_close_contradiction` and opens a fresh incident linked
  back to the closed one; the contradiction is visible on both
  incidents' reads, and the closed incident's record does not
  change by a byte.
- **The incident event log**: a regulatory-style export of one
  incident's whole chain — alarms, correlation, severity and
  ETR history, work orders and their acts, field updates,
  restoration, close — assembled only from artefacts of record
  in timestamp order, each line carrying its artefact id, as
  JSON with a plain-text rendering. An independent verifier
  re-performs the export against the store; the eval gate
  counts non-re-performing lines and probes a tampered export.
- Migration 0007 (both dialects), two new read tools in the
  Law 1 registry, and surfaces at parity across the operations
  API, the Runtime invocation handler and the CLI. The demo
  now walks the flood to a confirmed gated close with its
  accuracy record linked, splits a partially restored
  next-morning incident, closes the restored part, and raises
  a post-close contradiction on the closed flood.

## What Round 8 builds

- **Storm mode is a declared state, never an automatic one**
  (`grid_triage/storm.py`). The design's Law 8 imagined the
  system declaring storm mode above a computed incident-rate
  threshold; this round keeps the declared state and drops the
  automation — alarm volume alone never flips anything. Storm
  mode exists only as named operator acts: **declare** opens a
  window with its operator, stated reason and scope (a feeder
  list, or the whole territory), **extend** re-states the scope
  from that moment on, and **stand-down** closes the window.
  The acts are insert-only, content-addressed and linked into a
  chain per window; the state at any moment is the fold of those
  acts, and a state read names the act that put it there.
- **What the mode changes is stated once, in the storm pack**,
  with normal and storm values side by side — and nothing
  changes that the pack does not name: the correlation window
  widens 600s → 1800s; `meter_ping_failure`, excluded in normal
  mode, may *join* a storm incident (AMI silence during a storm
  is outage evidence) though it still never anchors one;
  severity's duration terms slow (30 → 60 minutes per point,
  cap 10 → 6) and its breadth terms count for more (10 → 5
  alarms per point, cap 5 → 8); an ETR's base repair minutes
  scale by 6/5.
- **The stamp is on the incident's face, and it is permanent.**
  An incident correlated while a declaration covers its feeder
  carries the mode, the storm pack version and the declaration
  id, judged at its anchor alarm's own time. Its severity
  assessment records the mode and the pack's stated adjustments
  in its inputs; its ETR carries the added minutes as their own
  `storm_conditions` component line, never folded silently into
  the repair line. Standing the storm down relabels nothing:
  incidents, work and estimates created under the window stay
  exactly as created, and revisions, split parts and post-close
  incidents are stamped at the act that creates them.
- **Storm accuracy is reported separately, never blended.**
  Every ETR outcome records its incident's operating mode, and
  the accuracy ledger's `by_operating_mode` rows compute the
  storm record from storm outcomes only — the rows sum to the
  overall figures exactly, so the separation is itself
  checkable.
- **The post-storm review** is computed only after stand-down,
  assembled only from artefacts — the window's stamped
  incidents, their current severity bands, their closures and
  ETR outcomes — and re-performs exactly under its verifier; a
  recomputation over a moved record supersedes by link.
- **Law 1 is untouched**: storm mode adds no tool that operates
  anything — two read tools (`get_storm_state`,
  `get_storm_review`) join the registry, and notifications
  remain drafts. Migration 0008 (both dialects) and surfaces at
  parity across the operations API, the Runtime invocation
  handler and the CLI. The demo closes with a declared storm
  over the Feeder 7 burst: widened grouping, the storm ETR and
  severity, a refused early review, stand-down, and the
  verified post-storm review.

## What Round 9 builds

- **The operator console** (`src/grid_triage/static/console.html`,
  served at `GET /console`) is one static page whose every
  figure is the board's computation over the artefacts of
  record (`grid_triage/board.py`): open incidents by severity
  with their mode stamps, the feeder board, the ETR board with
  each row's basis on its face — a `not_computable` estimate
  names its missing input rather than borrowing a number — the
  work-order queue, the pending-approvals queue, unconfirmed
  restorations, the ETR accuracy snapshot, and the storm banner,
  which names the declaring operator and the declaration id
  while a window is live. The page fetches exactly one read
  endpoint (`GET /console/data`); it carries no form, no POST
  and no control verb. Work-order notifications render as what
  they are: drafted, none sent.
- **The console check** (`scripts/check_console.py`, the
  portfolio pattern) grades the page against a planted record:
  every figure in the JSON re-performs from raw store reads —
  never through the board service under test — the page's own
  JavaScript is executed under Node against the API's JSON and
  must render the same figures, and the static file is scanned
  for any control or send surface.
- **The shift handover** (`grid_triage/handover.py`) freezes
  the board's computation into an artefact of record at a named
  operator's handover act: open incidents with severity and
  mode, pending approvals, open work orders, unconfirmed
  restorations, storm state, and the ETR accuracy snapshot.
  The artefact is insert-only and content-addressed; its
  `record_hash` covers its sections exactly, and an independent
  verifier re-performs each section from the artefacts and
  names any that does not recompute — including a tampered
  stored figure.
- **Acceptance is a second named act**, bound to the
  handover's record hash. The handing-over operator cannot
  accept their own handover; a second acceptance is refused
  naming the standing act; and a handover over a moved record
  is **stale on its face** — the verifier names the drifted
  sections and acceptance of a stale handover is refused. The
  way forward is a fresh handover, superseding the stale one by
  link. Assembling and accepting mutate nothing underneath:
  the record's incidents, assessments, orders, estimates and
  closures are byte-identical before and after.
- **Law 1 holds**: two read tools join the registry
  (`get_console_board`, `get_shift_handover`); the console and
  the handover add no control tool and no send surface.
  Migration 0009 (both dialects) and surfaces at parity. The
  demo's storm now changes shift mid-flight: a handover
  assembled and accepted during the storm, then — after the
  storm incident closes and the record moves — a stale
  handover refused at acceptance and a fresh one accepted in
  its place.

## What Round 10 builds

Round 10 built nothing new — it attacked Rounds 1–9. The full
record is `docs/red-team.md`: seventeen attacks across alarm
intake, field updates, topology versions, storm declarations,
work-order acts, restoration signals, closure acts and the
console/handover surfaces. Six defects were found and fixed;
eight attacks held; three findings are recorded as open, with
severities, rather than silently absorbed. Every defect is
kept as a regression test (`tests/test_r10.py`) and a
zero-tolerance eval counter.

- **Recomputations move forward only.** Severity and ETR
  artefacts are history in recording order and the head is the
  last one recorded — so a recomputation dated *before* the
  standing head rewound it: the flood assessed at +300 minutes
  (SEV-1, 94), then again at +5, showed the stale, quieter
  picture on every surface, and a backdated ETR estimate
  became the estimate a closure graded against. Both engines
  now refuse a recomputation whose `as_of` precedes the head's;
  the estimate of record is the estimate that stood when the
  incident was run.
- **The closure act takes its roles from a pack** (closure
  pack `2026.10.1`: `operator`, `duty_manager`), like every
  other act in the system — it previously accepted any
  non-empty string, including `banana` — and a restoration
  dated after the closure's own recording is refused, where
  before a week-future `restored_at` wrote a 9,632-minute
  "actual" into the accuracy ledger.
- **Storm scope overlap is judged over the recorded
  timeline**, at the extension door as well as the declaration
  door: an extension could widen one open window onto another
  window's feeder, and a backdated declaration could slip in
  before an open window — both put two windows over one
  feeder and let the fold silently name the first recorded.
  Declarations and extensions are now checked against every
  recorded window segment they would collide with.
- **What held** is in the record too: Law 1 on every surface
  (registry, API routes, Runtime actions, CLI subcommands —
  scanned, zero control/send verbs; no send mechanism exists
  in the codebase), the correlation window edge at exactly
  the pack's 600s, the storm joinable-kinds discipline,
  act-order and lineage refusals, the post-close contradiction
  on a split part, topology insert-only versions, and the
  handover rules — including the staleness rule, stated in
  the record: staleness is judged over the sections the
  handover carries, so a figure-preserving artefact (a crew
  note) does not stale it and a section-moving one does.

## What Round 11 builds

Round 11 answers the question Rounds 1–10 never asked: *who is
calling?* The act packs always named their humans and roles, but
the surfaces took actor strings on trust, and an incident id was
a capability — anyone who could guess one could read the file.
This round adds the registry the rest of the system checks
against, in the repo's usual shape: a versioned pack, a
fail-closed judge, and every refusal recorded as an artefact.

- **The access pack (2026.10.1)** states, per control-room role,
  the surfaces the role may read and the acts it may perform.
  An act's grant never exceeds the engine pack that judges the
  act itself — restoration signals stay a dispatcher/operator
  act, field updates stay a field_crew/dispatcher/operator act —
  and where both layers judge, both must pass.

  | Role | Reads | Acts |
  | --- | --- | --- |
  | operator | the working record (all but the denial log) | all control-room acts |
  | duty_manager | the working record + the denial log | the operator's acts, except recording signals and posting field updates (the engine packs reserve those) |
  | dispatcher | incidents, alarms, severity, ETR, work orders, field updates, restoration, storm, console, topology | ingest, field updates, restoration signals, confirming restoration |
  | field_crew | work orders only | field updates, work-order completion |
  | auditor | everything, including the denial log | none — oversight is a read |

- **Principals are carried, and judged as carried.** The API
  takes `X-Principal-Id` / `X-Principal-Role` headers, the
  Runtime a `principal` object, the services a `principal=`
  argument. A padded or partial pair is malformed, an unknown
  role is refused by name — never trimmed into validity, never
  defaulted. A carried principal must also *be* the human the
  act names (case/whitespace variants are the same person) and
  must record the act under its own role.
- **Incident reads are existence-hidden.** A principal whose
  role has no incident scope — a field crew, whose surface is
  its work orders — gets, for any incident id on every
  incident-scoped read surface, exactly the response a
  non-existent incident earns, byte for byte (graded). List
  surfaces filter instead of refusing: an empty list, never a
  count.
- **Every refusal is an artefact.** Denials land in an
  insert-only denial log (migration 0010) *before* the error
  propagates — principal, role, surface, the target the
  requester supplied, the reason, the pack version. The log is
  itself a guarded surface (auditor, duty_manager), and a
  denial over a hidden incident records only the requested id
  string, never whether the incident exists.
- **RT-17 is closed by decision.** The Round 4 closure surface
  predates the Round 7 gate and was callable by anyone who
  reached it. Where a principal is carried, it now requires
  the pack's close act, the closure's named operator, and the
  gate's preconditions — a complete restoration confirmation
  of record and no open work-order position — or it refuses
  naming them. The gated close remains the way to close
  anything short of that position (it alone can waive an order
  with a reason). The principal-less path is unchanged, and
  the decision is recorded as an amendment in
  `docs/red-team.md`.

## What Round 12 builds

Round 12 closes the red team's two remaining findings and
gives the record the artefact management reads later.

- **RT-15 — the storm position since the handover, shown.**
  A storm declared after a handover still does not stale it —
  the storm section is the fold at the handover's own moment,
  by design, so a storm handover stays acceptable when the
  window stands down. But the handover's read surface (the
  view the API, CLI, Runtime and console all serve) now
  carries a **derived flag** alongside the artefact: the storm
  acts declared after its moment, listed as recorded, and a
  boolean naming that the storm position has moved. Derived
  means computed at read time — never stored on the artefact,
  never folded into its record hash, never part of its
  verification. The incoming operator sees the new storm
  before accepting; the artefact keeps saying exactly what
  was true at its moment. (An act *backdated* to before the
  moment is the other case, and needs no flag: it changes the
  fold at the moment, so the existing drift path names it.)
- **RT-16 — a span cap, adopted as a pack decision.**
  Correlation pack `2026.10.2` adds `max_span_seconds`: no
  alarm joins an incident — as a member or as a joining
  anchor — more than four hours from the incident's anchor,
  however tight the nearest-member chain runs. What the cap
  refuses stays visible: a non-anchor beyond it is
  uncorrelated with the named reason `beyond_span_cap`, and an
  anchor beyond it opens its own incident. The `2026.10.1`
  pack is kept verbatim, carries no cap key and re-performs
  uncapped — a correlation re-performs under the pack version
  its incident pins, and the loader fails closed on an unknown
  version. The planted fixtures are untouched (the flood spans
  six minutes); the eval gate grades both directions.
- **Operations oversight reporting.** A window report is an
  insert-only, content-addressed artefact (migration 0011)
  over a stated half-open window: incident volumes by state
  (superseded / closed / live-open, summing to the total),
  severity mix by head band, ETR accuracy in the ledger's own
  shape **by operating mode — storm and normal never
  blended**, restoration outcomes, storm activity, and the
  handover position. Every rate carries
  `{value, numerator, denominator}`; over an empty population
  the value is `None` and the section reads `no_data` — never
  0%, never 100%. No section names an incident: the report is
  position and proportion, not a case file, and it states on
  its face that it is an internal oversight artefact, not a
  regulatory filing. Re-generating over an unmoved record
  returns the standing artefact; over a moved record it
  supersedes by link. **Verification is a second path**: the
  verifier re-performs every figure with its own walk over
  the store and names the sections that do not recompute —
  `make reports-check` plants forgeries and grades that they
  are named. Generation is a named act under the access pack:
  operator and duty manager generate, the auditor reads and
  generates none, and every refusal is recorded first.

## What Round 13 builds

Round 13 surveys before it builds. The red team's book is
closed — RT-17 by Round 11's decision, RT-15 and RT-16 by
Round 12's amendments; no finding stands open. The genuine
gap the survey found is the one the sibling AgentCore
blueprints closed in their middle rounds: the model call was
the last ungoverned surface in the build. Brief assembly
called the model port bare — no record of what was asked,
what came back, or what it cost.

- **The model gateway.** Every model call now routes through
  one door (`ModelGateway`), which wraps the configured
  profile — the offline deterministic composer by default, the
  fail-closed Bedrock profile when selected — without changing
  its behaviour. A hosted profile that is not configured still
  refuses loudly; the gateway records that error and lets it
  propagate. Nothing falls back silently, and the brief a call
  produces still names the wrapped profile.
- **The cost ledger (migration 0012).** Completed, refused and
  errored calls each land as one insert-only record before
  control returns to the caller: the measured canonical sizes
  of the facts handed over and the statements returned, the
  token estimates the model pack's stated rule derives from
  those sizes (canonical bytes ÷ 4, rounded up — a measurement
  rule, not a claim about any provider's tokenizer), the
  pack's rates, and the exact integer cost in micro-dollars.
  Totals are never stored: they fold from the records, and
  `verify_ledger` re-derives every record's arithmetic and
  re-folds by a second accumulation — `make ledger-check`
  grades the same reconciliation from raw store reads,
  including a planted tamper it must name.
- **Budgets that refuse on the record.** The pack
  (`2026.10.1`) states a per-incident call budget (25) and a
  per-incident cost budget (2,500,000 micro-dollars). A call
  that would cross either is refused before the port is
  touched; the refusal is itself a ledger record naming the
  budget it hit, and the refusal propagates. The offline
  profile is priced at zero, so the call budget is the one
  that binds it; the cost budget binds priced profiles.
- **A guarded read surface.** The ledger is an incident-scoped
  read under the Round 11 access pack (`model_ledger`):
  operator, duty manager and auditor read it; dispatcher and
  field crew are refused with the incident surfaces' existence
  hiding — answered exactly as a non-existent incident is
  answered, and recorded on the denial log.

## What Round 14 builds

Rounds 10–13 hardened derivations, identity and the model
call. None of them screened the words. Two channels carry
free text written outside the control room into this record,
and both were consumed on trust: field update text — a crew
note's mere presence satisfies the restoration class the
gated close requires, and the text itself landed verbatim on
the evidence chain — and alarm messages, which the assembler
copies into the facts it hands the model. Round 14 is the
injection quarantine both channels now pass through,
recorded as RT-18 in `docs/red-team.md`.

- **The screen (quarantine pack `2026.10.1`).** Instruction
  shape is a closed, versioned vocabulary: override phrasing,
  privileged-channel impersonation, directives quoting the
  catalogue's own verbs at the record's objects, prompt
  extraction, borrowed authority, and encoded payloads — the
  rules run over the raw text and over any base64-decoded
  view of it, so an instruction cannot be smuggled past the
  screen in encoding. Ordinary operational prose trips none
  of them; the whole pre-R14 corpus grades that every run.
- **Held text is absent (migration 0013).** A tripped text
  lands in the quarantine book as an insert-only hold — its
  only home. The artefact it arrived on is stored with the
  field as absent and the hold named in its place: a held
  crew note does not satisfy the crew class, a held alarm
  message never enters the assembler's facts, and the raw
  text is on no evidence item. What is not held passes byte
  for byte, exactly as before.
- **Escaped views, named releases.** A standing hold is
  visible only as escaped, data-only text (its JSON string
  form) on the guarded `quarantine` surface — the shift
  authorities and the auditor read it; dispatch and field
  crews do not, and incident-scoped reads hide as every
  incident surface hides. Release is a named reviewer's act
  (operator or duty manager), never the text's own reporter,
  insert-only like every act here; on release a field note
  counts again in the restoration fold. An alarm message is
  the stated exception: screened machine text never becomes
  model context, released or not — release restores its
  human readability, not its readmission to a prompt.
- **The audit is a script.** `make quarantine-check` plants
  both traps on the standard wiring and grades the claims
  from the record: the holds, the escaped view, the release
  discipline, the crew class unmoved then restored, and —
  through a recording port behind the real gateway — that no
  held text reached a model prompt, before or after release.

## What Round 15 builds

Every surface before this round reads the record live (the
board, the console), freezes it in aggregate (the oversight
report, which names no incident by construction), or covers
one window (the post-storm review). None of them answers
the morning-after question — **show me this one incident,
whole** — as an artefact that can be re-performed later.
Round 15 is the post-incident review.

- **One incident's timeline, re-performed (review pack
  `2026.10.1`, migration 0014).** A named operator or duty
  manager strikes the review; it rebuilds the incident from
  its artefacts of record alone — foundation and membership
  with the rule that placed each alarm, the storm stamp and
  the declaration behind it, cause classification and the
  severity history, the ETR estimates and the closure
  outcome graded against the estimate of record, work-order
  versions with their decisions, assignments and
  completions, the field record, restoration signals and
  confirmations, the closure and the gated close act,
  post-close contradictions, and the revision/split lineage.
  It computes; it narrates nothing and amends nothing.
- **Completeness graded on its face.** The pack names the
  elements a review accounts for — classification, severity,
  ETR estimate, work order, field updates, restoration
  confirmation, closure record. Each stands `present` with
  its artefact ids or `absent` with a stated reason
  (`not_assessed`, `no_work_order_drafted`,
  `closed_without_computed_estimate`, …), and the verdict is
  `complete` only when every element the pack requires for
  the incident's state is present. A thin record reads as a
  thin record — an unestimated closure is named as the gap
  it is, never dressed as a compared outcome.
- **Held text stays held.** The field record names the
  incident's quarantine holds — hold id, channel, the rules
  tripped, held or released and by whom — and carries no
  text at all. Release vouches for a human reader; the
  review is not a republication channel.
- **Insert-only, verified by a second path.** Reviews are
  content-addressed and supersede by link: re-striking over
  an unmoved record returns the standing artefact, over a
  moved record the next one — and the superseded review then
  names its drift, as a struck oversight report does. The
  verifier re-performs every section by an independent walk;
  `make reviews-check` grades the claims from a planted
  record, forged figures included. Striking is judged under
  the access pack (`strike_incident_review`), and reads sit
  on the `incident_review` surface with the incident's own
  existence hiding: a field crew is answered exactly as it
  is answered for a non-existent incident.

## What Round 16 builds

Every consequential act in this record had a second pair of
eyes — the gated close earns its close through a computed
confirmation, a quarantine hold is released by a reviewer
who is not its reporter, a handover is accepted by the
incoming operator — except the one that changes the most:
the topology declaration. One authorised operator's act
made a new territory version effective and unlocked
re-correlation under it. Round 16 is the declaration's dual
control.

- **A proposal states the whole declaration, and changes
  nothing (migration 0015).** A named operator proposes a
  version: the proposal artefact carries the version, the
  proposer, the proposed effective time and the change set
  verbatim from the version payload. It loads no payload,
  strikes no declaration, and no incident can be
  re-correlated on its strength. One live proposal per
  version.
- **A decision is a second operator's act.** Confirm or
  reject, as an insert-only artefact — and never by the
  proposer: the comparison runs under the repo's name
  folding, so a case or whitespace variant of the
  proposer's name is the proposer. A rejection requires a
  stated reason and strikes nothing; a decided proposal is
  not decided twice; a decision cannot predate its
  proposal.
- **Confirmation strikes exactly what was proposed.** The
  declaration comes into the record through the same strike
  path the direct act uses, with the confirmer as its
  declarer, the proposal's effective time and change set
  untouched, and the proposal's provenance — proposer and
  proposal id — on its face. A standing is the fold of its
  decisions, never a stored field, and `verify_proposal`
  re-performs the whole standing: a forged self-confirmation
  inserted straight into the store, or a declaration
  standing under a pending proposal's name, is named.
- **The direct route cannot walk around a live proposal.**
  While a proposal for a version stands pending, the direct
  declaration of that version is refused, naming the
  proposal. The direct act itself remains for versions no
  proposal stands over — the pre-R16 route, stated plainly
  in the honest status below. Both acts are judged under
  the access pack (`propose_topology`, `decide_topology`)
  on the topology surface; `make topology-check` grades
  the claims from a planted record, forgeries included.

## What Round 17 builds

Round 16's dual control bound the route the room opened —
and left the direct act standing beside it, a limit the
honest status below stated rather than smoothed over.
Round 17 closes it, in the build's own posture:

- **The pack states the route; the direct act refuses
  (topology pack 2026.10.2).** The new pack version carries
  the posture as data — `declaration_route:
  dual_control_only` — and the direct declaration act
  refuses every attempt under it, naming the governed
  route (propose the version; a second operator decides),
  whether or not a proposal stands, whoever signs, and
  after a rejection as after a confirmation. A refused
  attempt strikes nothing and loads nothing. The access
  guard still judges the act first: an ungranted principal
  is denied and logged at that door; a granted one meets
  the closure behind it. The 2026.10.1 pack is kept
  verbatim — it carries no route key and keeps the pre-R17
  posture, and a declaration is judged by the pack it was
  struck under, as the correlation pack has always ruled.
- **The closure governs new declarations.** A standing
  direct declaration struck under the earlier pack stays
  exactly what it was: read by the current services,
  verified, and re-correlated under, unchanged. Nothing
  about its standing is rewritten — the tightening binds
  what this build strikes now, not what the record holds.
- **Every standing declaration now verifies.**
  `verify_proposal` re-performed a proposal and the
  declaration its confirmation struck; a declaration
  naming no proposal had no verifier, so the remaining
  bypass was writing the artefact straight into the
  store. `verify_declarations` re-performs them all: the
  id recomputes from the stated fields, the change set
  matches the version payload, the role and times are
  legal under the declaration's own pack, there is one
  declaration per version — and a direct declaration must
  carry a pack version from before the closure, so one
  forged into the store under the current pack's stamp is
  named as struck by no route the record allows. The
  verifier reads on the API, the CLI and the Runtime
  beside the declarations themselves, and
  `make topology-check` grades the closure claims from a
  planted record.

## What Round 18 builds

Round 11 shipped the access pack, and the architecture doc
kept saying the other platform layer was "a later round":
the AgentCore Policy pack on the Gateway, designed since
Round 1 — allow read/draft/notify, deny every control-shaped
call, deny out-of-area reads — and never shipped. Round 18
ships it, as a pack in this repo's one pattern:

- **The pack is data** (`grid_triage/policy.py`, pack
  2026.10.1). The tool table states every catalogue tool's
  shape and whether it is area-scoped — it touches one
  feeder's operational record — written out in full, never
  derived from the registry, so a catalogue that grows past
  the pack is drift the verifier can name. The Cedar face is
  one permit per shape, each naming its tools explicitly and
  carrying the area condition: the call's target feeders
  must sit inside the caller's area. Cedar's default is
  deny, so a control-shaped call needs no forbid statement —
  no permit for it can be written from this table.
- **The decision engine re-performs the pack in code.**
  `decide` judges one Gateway call and names its reason from
  a closed vocabulary: permitted, out_of_area, area_unstated
  (an area-scoped call whose targets the Gateway target
  could not resolve fails closed — the area condition over
  no targets proves nothing), control_shaped (a name outside
  the catalogue carrying the registry scan's control
  vocabulary), unknown_tool.
- **Drift is graded, in both directions.**
  `verify_policy_pack` re-performs the pack against the live
  catalogue: a tool the pack does not cover, a rule naming a
  tool the catalogue does not hold, a shape stated
  differently, a Cedar statement whose actions have drifted
  from the table, a denial code that is not the platform's —
  each is a named problem. The pack and its verification
  read on the API, the CLI and the Runtime under the access
  pack's new `policy` surface (operator, duty manager,
  auditor; hidden as absent from dispatcher and field crew),
  and `make policy-check` grades the claims from a planted
  record.
- **Denials are detected in code.** A Gateway policy denial
  is not an HTTP error — it arrives as HTTP 200 with a
  JSON-RPC error body, code −32002. `detect_denial` reads
  that shape and `raise_for_denial` raises it as a named
  exception, so no caller can mistake a denied call's empty
  result for data.

## Quick start

```bash
make install          # uv sync --extra dev
make demo             # planted streams: flood in, one incident out
make test             # pytest
make evals            # the eval gate (zero-tolerance counters)
make reports-check    # the oversight report check (plants forgeries)
make ledger-check     # the model ledger check (plants a tamper)
make quarantine-check # the quarantine check (plants trapped text)
make topology-check   # the topology dual-control check (plants forgeries)
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
  the notifications it produces are drafts, never sent.
  Nothing here dispatches a crew in the world; it records who
  decided what.
- **Field updates and re-correlation are Round 6**: updates
  are recorded against incidents and correlated under the
  pinned topology only; topology v2 is declared effective by
  a named act, and re-correlation produces a linked revision
  with a stated diff — the prior revision is never edited,
  and closed incidents are never re-correlated.
- **Restoration confirmation and the gated close are
  Round 7**: restoration is confirmed from recorded signals
  by a computed artefact, and an incident closes only through
  a named close act binding a complete confirmation, the R4
  closure and its ETR outcome, and the work-order position.
  Signals arrive through a recording port with planted local
  readings — no live meter/OMS system is connected, and a
  confirmation is only ever as good as the signals recorded
  against it.
- **Storm mode is declared, never automatic (Round 8)**: the
  declaration acts are the whole mechanism — there is no
  incident-rate threshold anywhere in the codebase that enters
  storm mode, and none is planned; the design brief's Law 8
  threshold wording is deliberately not implemented, because a
  mode that changes correlation, severity and ETR arithmetic
  should begin with a named human's recorded decision. What
  the mode changes is bounded by the storm pack; storm
  reporting cadence and customer communications are later
  rounds.
- **The console and the handover are read-only over the
  record (Round 9)**: the console renders the record, it does
  not operate it — there is no action surface on the page at
  all, and every approval, decision and close still happens
  through the governed acts on the API, Runtime and CLI. The
  handover's staleness is judged against the record's current
  state, so any later artefact that moves one of its sections
  — not only later-timestamped ones — makes an older handover
  stale; that is the honest reading of "the record has moved",
  and a fresh handover is cheap. (Round 10 stated the rule
  precisely: staleness is judged over the six sections the
  handover carries, so a figure-preserving artefact like a
  crew note does not stale it.)
- **Round 10 was a red team, not a feature.** Six defects in
  the R1–R9 system were found by adversarial probing and
- **The red-team record (Round 10)** named six defects, all
  fixed (severity/ETR head rewinds, the closure act's missing
  role pack and future-dated restorations, storm overlap at
  the extension door and by backdating); three design-level
  findings are open in `docs/red-team.md` — a storm declared
  after a handover does not stale it (the storm section is
  the fold at the handover's own moment), correlation
  chaining grows an incident's span by the pack's stated
  nearest-member rule, and the R4 closure surface was not
  itself restoration-gated. Round 11 closed that third
  finding by decision (above), and Round 12 addressed the
  other two: RT-15 by the derived flag on the handover's read
  surface, RT-16 by adopting the span cap as correlation pack
  2026.10.2 — both recorded as an amendment in
  `docs/red-team.md`. No red-team finding stands open.
- **Access control governs principal-carrying calls
  (Round 11).** A call that carries no principal — the CLI,
  the local harness — is the trusted local operations path,
  exactly as it has been since Round 1; the pack judges every
  call that presents a caller. In a deployment, AgentCore
  Identity terminates caller identity in front of these
  surfaces and every call presents one. The pack governs
  *authorisation* — which role may read and do what; *who* a
  principal id belongs to is the identity layer's question,
  not this repo's.
- **Oversight reports are statements over the record as it
  stands (Round 12).** A report's verifier re-performs its
  figures against the current store: a report over a record
  that has since moved names its drift, which is what the
  supersede-by-link discipline answers — the same stance the
  handover takes toward staleness. The span cap is likewise a
  pack value, not a universal truth: 2026.10.2's four hours is
  this utility's stated decision, and 2026.10.1's uncapped
  correlations stand as computed.
- **The AgentCore Policy pack ships in Round 18, graded
  in-repo; no live Gateway is called.** Law 1 is now stated
  twice in code: by the tool catalogue's construction and
  registry scan, and by the policy pack (2026.10.1) — the
  tool table, the Cedar permits, the decision engine that
  re-performs them, and the −32002 denial detection. What
  remains the deployment's: attaching the pack to a real
  Gateway (the action namespace in the Cedar is the one the
  deployment's Gateway target publishes), the Gateway
  targets themselves behind the catalogue's contracts, and
  the target's duty of resolving each call's target feeders
  from its arguments — the area condition judges what the
  target states, and a scoped call it cannot resolve fails
  closed here rather than passing unstated.
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

- **Model costs are pack-stated, not provider-billed
  (Round 13).** The ledger's token figures come from the
  pack's stated measurement rule over canonical sizes, and its
  rates are the pack's stated reference rates (the offline
  profile at zero; the bedrock profile at 3/15 micro-dollars
  per input/output token). A deployment states its own
  contracted rates by superseding the pack. No live Bedrock
  call has been made from this build — the hosted profile
  remains fail-closed and stub-tested, and the gateway records
  its refusal as an errored call rather than letting any path
  proceed unpriced.
- **The quarantine screens text, not people (Round 14).**
  The screen is a stated pattern pack, not a judgement of the
  reporter: a held note is a routing event, not an accusation
  — the text waits, escaped, for a second person's release,
  and the update itself (its reporter, kind and moment) is on
  the record throughout. The pack will have false positives
  by construction (a crew note that quotes an instruction is
  held like one that gives it) and false negatives by
  limitation (instruction shape is a vocabulary, not a proof
  — text that trips nothing is consumed as before). Staff
  text written under a judged act by a control-room role —
  an operator note on a work order, a closure's resolution
  summary — is attributed by its act and is not screened;
  the book governs the two outside channels only.
- **The post-incident review states one incident, as the
  record stands (Round 15).** Like the oversight report, a
  review's verifier re-performs its sections against the
  current store: a review over a record that has since moved
  names its drift, and the answer is a fresh strike, which
  supersedes by link. Completeness is the review pack's
  stated judgement of what an incident's record should hold
  — the required sets are deliberately narrow (classification
  and severity for a live incident; the closure record added
  once closed), because the review names every absence
  either way and the verdict exists to separate "not yet
  worked" from "worked and closed", not to invent duties the
  engines themselves do not impose. An estimate is not
  required for a `complete` verdict: an unestimated closure
  is a named gap on the artefact's face
  (`closed_without_computed_estimate`), which is the honest
  place for it — the accuracy ledger has always recorded
  that gap rather than dropping it.
- **Topology dual control governs the propose/decide route;
  the direct declaration act remains (Round 16).** Where a
  proposal stands pending, the direct act is blocked for
  that version — the governed route cannot be bypassed
  while it is live. But a version no proposal stands over
  can still be declared by the single Round 6 act, as it
  could before this round: the tightening binds the route
  the room opens, it does not rewrite the record's earlier
  discipline, and this paragraph is where that limit is
  stated rather than smoothed over. A deployment that wants
  dual control on every declaration closes the direct act
  at its own door — the access pack already gates it as
  its own act (`declare_topology`), separable from
  `propose_topology` / `decide_topology`. **That was the
  state through Round 16; the next bullet is Round 17
  closing the limit in this build.**
- **Dual control is the only route to a declaration
  (Round 17).** Topology pack 2026.10.2 states the route
  as data, and under it the direct act refuses every
  attempt, naming the governed route — the limit above no
  longer holds for declarations this build strikes. Two
  boundaries are stated plainly. First, the closure is the
  current pack's posture: the 2026.10.1 pack is kept
  verbatim, a service constructed under it behaves exactly
  as it did, and standing direct declarations from that
  posture stay valid and verifiable — no wired surface
  offers the old pack, but the pack mechanism itself is
  the record's versioning discipline, not a back door this
  round pretends away. Second, the declaration verifier
  judges artefacts, not writers: a declaration forged into
  the store under a pre-closure pack stamp, self-consistent
  in every field, is indistinguishable from an honest
  historical one — the verifier names what is provably
  wrong (a post-closure stamp on a direct declaration, a
  tampered field, a second declaration for a version), and
  the store's own access control is what stands between a
  writer and the table, as it is for every artefact here.

## Layout

```
src/grid_triage/   models, topology, correlation, services (ingest),
                   evidence, store, agent, tools (Law 1 registry),
                   severity (R2), cause / memory / packs (R3),
                   roster / etr / closure (R4), work_orders (R5),
                   revisions (R6), restoration (R7), storm (R8),
                   board / handover / static console (R9),
                   access (R11), oversight (R12),
                   model_gateway (R13), quarantine (R14),
                   review (R15), topology dual control in
                   revisions (R16), the direct-route closure
                   and the declaration verifier (R17),
                   runtime_app / runtime_main
                   (AgentCore Runtime),
                   api, demo, cli, local_invoke, wiring, config
data/topology/     versioned territory data (v1.json, v2.json)
data/roster/       versioned crew roster artefact (v1.json)
migrations/        numbered SQL migrations, sqlite + postgres trees
tests/             pytest suite (340 tests through R18)
evals/             the eval gate (run_evals.py)
scripts/           check_console.py — the console correctness check
                   check_reports.py — the oversight report check
                   check_model_ledger.py — the model ledger check
                   check_quarantine.py — the quarantine context audit
                   check_reviews.py — the incident review check
                   check_topology.py — the dual-control check
docs/              architecture.md — the honest AgentCore mapping
```

Apache 2.0. See `LICENSE`.
