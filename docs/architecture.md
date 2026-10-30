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
| **Gateway** | SCADA-adjacent historian, OMS and AMI/meter systems exposed as read-only MCP targets — the single tool path | **Port-shaped.** The tool catalogue (`grid_triage/tools.py`) is the contract the Gateway targets will implement; the MCP wiring lands in a later round. No Gateway is called in R1. |
| **Policy** | Cedar pack on the Gateway: allow read/draft/notify, deny every control-shaped call, deny out-of-area reads; denials arrive as HTTP 200 + JSON-RPC −32002 and must be detected in code | **Designed, not yet shipped.** R1 enforces Law 1 by construction + registry scan. The pack itself is a later round. |
| **Memory** | Episodic memory of prior incidents per feeder/device, feeding briefs (and, later, correlation) as recorded inputs | **Port + local double (R3).** `grid_triage/memory.py` defines the port in the managed service's event-memory shape — closed incidents as records of fact — served locally by a double over the store. Records are insert-only, retrieval is weighted and deterministic, and briefs cite records directly. No live AgentCore Memory call is made. |
| **Identity** | Operator / dispatcher / field / auditor roles; control-room caller identity on every act | **Round 11 (in-repo layer).** The access pack (`grid_triage/access.py`) judges every principal-carrying call — surfaces carry the caller (API headers, Runtime payload, service argument), refusals are recorded in an insert-only denial log, and incident reads are existence-hidden. AgentCore Identity is the production termination point in front of these surfaces: it answers *who* the caller is; the pack answers what that caller's role may read and do. Calls carrying no principal (CLI, local harness) remain the trusted local path. |
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

**Work orders are drafts; assignment and approval are acts
(R5).** A work order (`grid_triage/work_orders.py`) is derived in
code from the incident's records under a versioned work pack:
scope from the classification of record, affected devices and
per-kind safety notes from the pinned topology, materials and
the required crew qualification (in the roster's skill
vocabulary) per cause class, and the current ETR estimate pinned
by id. Unsupportable statements are named in `gaps` rather than
filled — an unclassified incident states no qualification, and
so cannot be assigned. Drafts are content-addressed versions
superseding by link; their state is *derived* from the decision
acts, never stored on the draft, so the artefact an approval
pins cannot drift. Assignment is a recorded act checked in code
(qualification on the roster; no double-booking against roster
commitments or other open orders' current assignments — a
superseded version's assignment dies with it). Approval is a
named operator's act in a pack-stated role against the current
version only, refused to the operator who drafted or last
revised that version, and it requires a live assignment;
rejection requires a reason and closes the version. Approval
drafts the field and customer notifications as records marked
unsent in their own text — the notify shape has no send surface
anywhere in the package, and the eval gate scans for one.

**Field updates arrive under the pinned topology; the territory
changes by act, and re-correlation is a revision (R6).** A field
update (`grid_triage/revisions.py`) is an insert-only artefact
in a versioned pack's kind vocabulary — alarm observation, crew
note, damage finding — naming its reporter and role. An update
carrying a new alarm re-runs the correlation pack for that alarm
against the incident's current members under the incident's
*pinned* topology version, and the outcome is recorded on the
update itself: joined with its rule, a named non-join, or
`unresolvable_under_pinned_topology` when the alarm's device
does not exist in the pinned version. The newer topology is
never silently substituted — the update from the v2-only sensor
SEN-0723 against the v1-pinned flood is the graded case.
Topology v2 ships as data (`data/topology/v2.json`: a feeder
split, a device added, a device replaced, a critical load
added, the change set stated in the payload) and takes effect
only through a **declaration act** — a named operator, a role,
an effective time; before the act, or before its time, nothing
migrates. **Re-correlation** is a second named act producing a
new incident row under v2, linked to its predecessor by an
`IncidentRevision` record: the diff (alarms/devices added and
removed — membership follows the v2 device→feeder mapping, so
the split section's 13 alarms leave the flood's group), the
severity recomputed on *both* sides at the re-correlation
instant (so the band change is the topology's doing, not the
clock's; the moved formula inputs are named), and the ETR and
classification artefacts on both sides linked by id. The prior
incident's rows and artefacts are never written by any of this;
supersession lives on the link record. An incident with a
closure anywhere in its lineage is refused, naming the closure.

**Restoration is confirmed from signals; the close is a gate
(R7).** Round 4's closure act records the facts of a close, but
nothing in Rounds 1–6 *earned* one. Round 7 adds the earning
(`grid_triage/restoration.py`). A **restoration signal** is an
insert-only reading recorded against an incident: per-device
readings from the meter/OMS-shaped sources (`restored` /
`still_out`) and a feeder supply reading from the
topology-shaped layer (`supplied` / `not_supplied`). The
**confirmation** is computed in code from the signals, the
incident's field updates and its pinned topology, under a
versioned restoration pack: each required evidence class —
meter restoration, feeder supply reading, crew confirmation
(a field-crew note on the incident) — is named present or
absent with the artefact ids behind the verdict; restored and
outstanding customers are summed per device over the outage
set (the severity slice, anchor plus descendants). A device's
state is its latest signal's state, so a late `still_out`
reading makes a confirmation stale-honest rather than wrong.
The artefact reads `complete` only when every class is present
and no device is outstanding; otherwise `partial`, and a
partial confirmation cannot close anything. The **gated close**
is a named operator's insert-only act binding the complete
confirmation by id, the R4 closure and its ETR outcome by id
(the accuracy ledger's entry — linked, never duplicated), and
the work-order position: the current order's completion act
(new in R7 — approvals decided state, but nothing recorded
that the field work was *done*) or an explicit waiver by the
closer with a reason. Open items refuse the close, named. A
**partial restoration splits**: a named act divides the
incident into a restored part and a continuing incident —
new rows, memberships partitioned by the confirmation's device
sets, the partition and customer counts stated on the split
record — following the R6 revision pattern exactly, including
the rule that the source is never edited. A split part inherits
the source's signals and crew updates through the split link,
so the restored part confirms against its own partition of the
outage set and closes on its own evidence. A **closed incident
is never reopened**: an alarm update arriving on a closed
incident's device set never joins it — the update service
checks for a closure before correlating, and the restoration
service's registered handler records a
`post_close_contradiction` and opens a fresh incident holding
the alarm, linked back to the closed one; the contradiction is
readable from both incidents. The **event log** assembles one
incident's whole chain from artefacts of record only, in
timestamp order, each line carrying its artefact id (JSON plus
a plain-text rendering), and its verifier re-performs an
export against the store — a line that names no artefact, or
the wrong timestamp, and an artefact missing from the export
are both named as problems.

**Storm mode is a declared state (R8).** On a major-event day
the operating parameters change — and the change is itself an
act in the record (`grid_triage/storm.py`). A **storm
declaration** is a named operator's insert-only act — declare,
extend or stand-down — stating its operator, role, reason and
scope (a feeder list; empty means the whole territory). The
state of any feeder at any moment is the *fold* of the acts:
the latest act in record order at or before that moment whose
scope covers the feeder decides, and a state read names the
declare act, the head act and the pack version behind it.
Nothing computes an entry into storm mode: the design brief's
Law 8 threshold wording is deliberately not implemented,
because a mode that changes judgement arithmetic should begin
with a recorded human decision, not a tripwire. A **storm
pack** states every parameter the mode changes with its
normal and storm values side by side — the correlation window
(600s → 1800s), the excluded kind that becomes joinable
(`meter_ping_failure`, join-only), severity's duration and
breadth terms, and the ETR base-repair factor — and the
engines apply exactly the pack and nothing else: correlation
reads its window from the mode in force at the incident's
*anchor alarm time* (a burst raised before the declaration
correlates narrow and normal even when it is correlated
afterwards), severity merges the pack's four named overrides
into its own pack, and ETR scales the base repair line and
adds the difference as its own `storm_conditions` component,
labelled with the factor, the pack version and the
declaration id. The resulting **stamp** — operating mode, pack
version, declaration id — is recorded on the incident row,
the severity assessment's inputs, the ETR estimate and the
ETR outcome, so storm performance reads out of the accuracy
ledger in its own row, provably summing with the normal row
to the overall figures. Stand-down closes the window from its
moment and rewrites nothing: later creations are stamped at
their own acts' times. The **post-storm review** is computed
only over a closed window, only from artefacts — the stamped
incident set, their current bands, their closures and
outcomes — and its verifier re-performs the window times,
scope, set and figures exactly, naming drift; a recomputation
over a moved record supersedes by link rather than editing in
place. Storm mode adds no surface that operates anything: the
new tools are two reads, and Law 1's registry scan still
grades the whole catalogue.

**The operator console and the shift handover (Round 9)** add
the read side of the control room without adding a single
action to it. One computation — the board — assembles the
operating picture from the artefacts alone: which incidents
are live-open (a stated definition: no closure, no gated close
act, not superseded by a revision or a split), the operative
work-order version and its decision-derived state, the
restorations no confirmation has completed, the storm windows
folded at the board's moment, and the accuracy ledger as it
stands. The console page renders exactly that computation's
JSON; the console check re-performs every figure from raw
store reads and executes the page's own rendering under Node,
so a figure with no artefact behind it cannot survive the
gate. The **handover** freezes the same computation at a named
operator's act into an insert-only artefact whose record hash
covers its sections; acceptance is a second named act bound
to that hash, and because the sections re-perform against the
record as it now stands, any later artefact that moves a
section makes the handover stale on its face — the verifier
names the drifted sections and acceptance refuses a stale
handover. Nothing in
the console or the handover writes to the record beneath
them: the only new rows are the handover and its acceptance,
and the only new tools are reads.

**The red team (Round 10)** added no component; it hardened
three invariants the earlier rounds had left implicit, and
the full attack record is `docs/red-team.md`. First, the
computed artefacts are **forward-only**: severity assessments
and ETR estimates are history in recording order and every
reader takes the last recorded as current, so a recomputation
whose `as_of` precedes the standing head's is refused in both
engines — a head can be superseded, never rewound, and the
estimate a closure grades against is the estimate of record.
Second, the **closure act carries the same pack discipline
as every other act**: its roles are stated in the closure
pack, and a restoration cannot be dated after the closure's
own recording, so the accuracy ledger never grades a future
as fact. Third, **storm scope overlap is a property of the
recorded timeline, not of an instant**: declarations and
extensions are checked against every recorded window segment
— open or closed — they would collide with in time and scope,
so two windows never cover one feeder and the mode fold's
first-match rule is never asked to arbitrate an overlap.
Three findings the round could not fix in scope are recorded
as open in the red-team record, two of them pinned by
characterisation tests so changing them is deliberate.

**Access control (Round 11)** is the domain layer the mapping
table's Identity row points at. Its shape is the repo's own:
the rules are a versioned pack in code (`access.py`, pack
2026.10.1) stating per-role read surfaces and permitted acts,
evaluated by a fail-closed judge; the grants are deliberately
*derived from* the engine packs (an act's access grant never
exceeds the roles its own pack allows), so the new layer
narrows who may reach an act without ever widening what the
act accepts. Three disciplines do the work. First, a carried
principal binds the act: the caller must hold the act, must be
the human the act names (compared under a case/whitespace
folding — the RT-25 lesson applied to caller identity), and
must record it under their own role. Second, incident reads
are existence-hidden for roles without incident scope: the
answer for a real id is byte-identical to the answer for an
invented one on every read surface, and lists filter rather
than refuse, so neither existence nor counts leak. Third,
every refusal is an insert-only denial artefact (migration
0010) recorded before the error propagates, in a log that is
itself a guarded read surface. The round also closed the red
team's RT-17 by decision: the Round 4 closure surface — the
one act surface that predated the Round 7 gate — now applies
the gate's preconditions (complete confirmation of record, no
open work-order position) to principal-carrying calls,
refusing with the unmet preconditions named, while its
principal-less local path and the R4 accuracy-ledger contract
are unchanged. The stated scope limit is the portfolio's:
enforcement governs calls that carry a principal; identity
termination in deployment is AgentCore Identity's layer, in
front of these surfaces.

**Oversight reporting (Round 12)** is the record's periodic
statement, and the round that closes the red team's book. Its
two finding-fixes are exercises in the repo's own disciplines.
RT-15's answer respects the handover's construction: the storm
section stays the fold at the artefact's moment — re-performing
it live would stale every storm handover at stand-down — so the
correction is a *derived read* on the handover surface,
computed from the declaration acts at read time and stored
nowhere: post-handover storm acts are shown alongside the
artefact they postdate, outside its hash and its verification.
RT-16's answer is a pack revision: correlation pack 2026.10.2
adds an anchor-relative span cap (`max_span_seconds`), the
2026.10.1 pack is kept verbatim for re-performance, and an
incident's pinned pack version decides which rule its
correlation is judged under — the same versioned-pack
discipline the topology, severity and storm packs already use.
The report artefact itself follows the portfolio's reporting
pattern: insert-only and content-addressed (migration 0011),
windowed, aggregate-only with no incident named anywhere,
rates carrying numerator and denominator with empty
populations reading no-data, generation a named act judged by
the access pack, and verification implemented as a genuinely
second computation path (`reperform_sections`) rather than a
re-call of the first — the property `scripts/check_reports.py`
grades by planting forgeries whose fingerprints honestly cover
their wrong figures.

**The model gateway (Round 13)** governs the build's one model
call, which Rounds 1–12 left bare. The gateway wraps the
configured profile port — offline composer by default, the
fail-closed Bedrock profile when selected — and the assembler
calls it exactly where it called the port, so the verified-
brief pipeline (citation, band, attribution and basis checks)
is untouched. Every routed call lands as an insert-only ledger
record (migration 0012) carrying measured canonical sizes, the
model pack's token estimates and rates, and an exact integer
micro-dollar cost; refused calls (the pack's per-incident call
and cost budgets, judged before the port is touched) and
errored calls (a hosted profile's fail-closed refusal) are
records too, at zero tokens and zero cost. Totals are a fold,
never a stored number, and `verify_ledger` re-derives the
arithmetic record by record — the same second-path discipline
as the oversight verifier, graded by
`scripts/check_model_ledger.py` from raw store reads. The
ledger read is an incident-scoped surface in the access pack,
with the existence hiding every incident surface carries. The
gateway holds no tool shapes: it routes a draft-shaped call
and writes its own ledger, so Law 1's catalogue is unchanged.

**The injection quarantine (Round 14)** screens the two
channels that carry free text from outside the control room —
field update notes and findings at the field door, alarm
messages at the ingest door — against a versioned rule pack
before anything is stored. A tripped text becomes an
insert-only hold (migration 0013) and the artefact it arrived
on is stored with the field absent: derivations fold it
exactly as if it had never arrived (the restoration crew
class reads the release acts, so a released note counts
again), and the assembler's facts never carry it — for alarm
messages, not even after release, since release governs
human visibility of machine text, not its readmission to a
prompt. Standing holds render only as escaped, data-only
strings on the access pack's `quarantine` surface; release is
a named reviewer's act, refused to the text's own reporter.
`scripts/check_quarantine.py` grades the whole claim from the
record, auditing the gateway's prompts through a recording
port. The book screens text at the doors; it holds no tool
shapes and operates nothing.

**The post-incident review (Round 15)** is the per-incident
counterpart of the oversight report. Where the report is
aggregate by construction and names no incident, the review
(migration 0014, review pack `2026.10.1`) re-performs ONE
incident's whole timeline — foundation and membership, cause
and severity, the ETR record against its graded outcome,
work orders and their acts, the field record (quarantine
holds named by id and rule, their text never carried),
restoration and closure, revision/split lineage — into an
insert-only artefact that grades its own completeness:
every element the pack expects stands present with its
artefact ids or absent with a stated reason, and the verdict
fails only on the absences the pack makes required for the
incident's state. The verifier re-performs every section by
an independent walk (`scripts/check_reviews.py` grades the
claims from a planted record); re-striking over a moved
record supersedes by link and the superseded artefact names
its drift, exactly the oversight stance. Reads sit on the
access pack's `incident_review` surface with incident
existence hiding, keyed on the incident the review names.
The review holds one new read shape in the catalogue
(`get_incident_review`) and operates nothing.

**Topology dual control (Round 16)** closes the declaration
gap the earlier rounds left: the Round 6 declaration act was
the record's one remaining single-signature change of
standing state. A proposal (migration 0015) is an insert-only
artefact stating the whole declaration it asks for —
version, proposer, effective time, the payload's change set
verbatim — and has no effects: nothing loads, nothing is
declared, re-correlation stays refused. A decision is a
second named operator's insert-only act; the decider may
not be the proposer under the repo's name folding, a
rejection must state its reason, and a proposal's standing
is the fold of its decisions. Confirmation strikes the
declaration through the same path the direct act uses, with
the proposal's provenance (proposer, proposal id) carried
on the declaration model — declarations struck directly
carry neither field. While a proposal stands pending, the
direct declaration of its version is refused by name; the
direct act otherwise remains, and the README's honest
status states that limit plainly. `verify_proposal`
re-performs a standing from the artefacts (id recomputation,
change-set agreement with the payload, decision-fold
legality, declaration agreement), so a forged decision
inserted at the store is named rather than folded in
silently. Both acts are access-pack acts
(`propose_topology`, `decide_topology`) on the topology
surface, granted to operator and duty manager — the same
roles the topology pack itself states. No new tool shape
enters the catalogue: governing a declaration is an
operator act on the API, CLI and Runtime surfaces, and the
agent's topology reads are unchanged.

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
