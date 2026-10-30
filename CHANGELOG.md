# Changelog — Grid Alarm Triage & Field Work Agent (AWS Bedrock AgentCore)

## 1.0.0 — 2026-10-30

The blueprint at v1.0: the full twenty-round build, frozen by a
release-hardening pass (R20) that re-ran every gate on the
finished tree — the full test suite, the eval gate, the console,
reports, model-ledger, quarantine, reviews, topology, policy and
red-team checks, and the demo end to end. The README,
architecture and red-team docs were audited against the code as
shipped; the drift found (the intro still promising the operator
gate and the Policy pack as later rounds, an architecture table
that promised Gateway wiring, evaluator configs and a console
trace timeline this build never shipped, a truncated honest-status
fragment, a layout listing missing the R18/R19 modules and two
check scripts) was corrected in the docs. The exact freeze
figures are recorded in `docs/release-1.0.0.md`.

The gaps that remain are stated in the README's Honest status
and are unchanged by this release: no live AgentCore, Bedrock or
Gateway call has ever been made from this build — the Runtime
contract is exercised for real against the installed SDK, and
everything beyond it is a port, a local double or a shipped pack
waiting on a deployment; the hosted model profiles are
stub-tested and fail-closed; the Policy pack is graded in-repo
and unattached to a live Gateway; the quarantine screen's
remaining evasions are its stated vocabulary limits (RT-25),
pinned in tests; access enforcement governs calls that carry a
principal, with principal-less calls remaining the trusted-local
path (in production, AgentCore Identity terminates identity in
front of the surfaces); the suite's verified store is SQLite —
both-dialect migrations ship, but no Postgres parity leg was run
for this repo; and the Docker image build is reviewed statically
rather than run (no Docker daemon in the build sandbox). The
README's work-in-progress notice stays on: hands-on developer
testing and the companion blog post are still outstanding.

### The twenty rounds

- **R1 — Core.** The alarm/incident domain and versioned
  topology for the fictional Kestrel Power & Light (2
  substations, 3 feeders, 13 devices, 3 critical loads as a
  parent-linked tree); append-only ingest with a planted
  reference feed — a 40-alarm feeder flood, an 8-alarm comms
  nuisance stream, a lone breaker trip; correlation pack v1
  (the flood is exactly one incident with exactly the 40
  planted alarms; the nuisance stream creates none);
  hash-chained evidence over an S3-shaped port; the agent step
  behind a model port with a citation resolver that refuses any
  brief citing a non-member alarm; the real
  `BedrockAgentCoreApp` Runtime entrypoint plus the operations
  API, CLI and local harness; the store port (SQLite default,
  Postgres dialect, migration 0001 in both). Law 1 lands here:
  the tool catalogue holds read, draft and notify shapes only,
  and a source scan proves no control-shaped tool exists.
- **R2 — The severity engine.** A versioned formula pack
  computes each incident's band (customers affected,
  critical-load weights, duration, alarm breadth) into
  content-addressed assessments that record every input and
  supersede by link. A band the data cannot support is
  `UNRATED` with the missing inputs named; escalation bands
  require a named operator's acknowledgement; the brief may
  restate only the assessed band — a mismatch refuses it.
  Migration 0002.
- **R3 — Cause, memory, evidence packs.** Root-cause
  classification by a versioned rule pack over the anchor's
  pattern — a pattern no rule matches stays `unknown`, never
  forced. The memory port (a labelled local double for
  AgentCore Memory): closed incidents as insert-only records,
  retrieval by stated deterministic weights, briefs citing
  priors as recorded — a misattributed prior refuses the whole
  brief. Sealed evidence packs: a manifest over everything an
  incident rests on, its hash covering every member, drift
  named on verification. Migration 0003.
- **R4 — The ETR engine.** Restoration estimates computed in
  code — crew wait from the versioned roster, travel and
  repair base minutes from the versioned rate pack — and every
  estimate content-addressed and pinned to its inputs. The
  basis law: `computed_from_rates` when every input exists,
  else `not_computable` naming the missing input; the brief
  verifier refuses a restoration time stated without its
  basis. Re-estimates supersede by link; the closure act
  records the actual restored time and feeds the accuracy
  ledger and the Round 3 memory record. Migration 0004.
- **R5 — Work orders and the operator gate.** Drafts derived
  in code from the records under a versioned work pack —
  scope, safety notes, materials, the pinned ETR — with what
  the data cannot supply named in `gaps`. Assignment as a
  recorded act, checked against the roster (qualification,
  double-commitment). The approval gate (Law 5): a named
  operator's act in a pack-stated role, against the current
  version only, pinning that version; the drafter cannot
  approve; rejection requires its reason. Approval drafts the
  notifications — records that mark themselves unsent; there
  is no send surface. Migration 0005.
- **R6 — Field updates and topology v2.** Updates (alarm
  observations, crew notes, damage findings) as insert-only
  artefacts; a new alarm re-correlates under the incident's
  pinned topology version only — an alarm from a device that
  exists only in v2 is `unresolvable_under_pinned_topology`,
  never silently re-correlated. Topology v2 ships as data with
  its change set stated, and takes effect only through a named
  operator's declaration act. Re-correlation is a revision,
  not an edit: a linked record stating the diff (for the
  flood, 13 alarms out and the new sensor's alarm in; severity
  recomputed on both sides, SEV-1 85 → SEV-2 64). Closed
  incidents are never re-correlated. Migration 0006.
- **R7 — Restoration and the gated close.** Restoration is
  confirmed from recorded signals in code: every required
  evidence class named present or absent with the artefact ids
  behind the verdict; `complete` only when every class is
  present and every device restored. The gated close binds a
  complete confirmation, the R4 closure and its ETR outcome,
  and the work-order position (a completion act, or an
  explicit waiver with a reason). Partial restoration splits
  an incident rather than closing it whole; a post-close alarm
  is a `post_close_contradiction` opening a fresh incident,
  the closed record byte-identical. The incident event log
  exports one incident's whole chain, re-performed by an
  independent verifier. Migration 0007. The round's parity
  work also found and fixed a pre-existing Postgres boot bug:
  the migration ledger insert used a SQLite placeholder on the
  raw psycopg connection, so the Postgres profile could never
  finish applying migrations.
- **R8 — Storm mode, declared.** Storm mode exists only as
  named operator acts — declare, extend, stand-down — the
  state at any moment the fold of those acts, naming the act
  that put it there; the design's automatic incident-rate
  threshold is deliberately not implemented. The storm pack
  states every parameter the mode changes with normal and
  storm values side by side (the correlation window, the
  joinable kinds, severity's terms, the ETR repair scale), and
  nothing the pack does not name changes. The stamp is
  permanent on the incident's face; storm accuracy is reported
  separately in the ledger, never blended; the post-storm
  review is computed after stand-down from artefacts only.
  Migration 0008.
- **R9 — The console and the handover.** The operator console:
  one static page fed by one read endpoint, every figure the
  board's computation over the artefacts of record — open
  incidents by severity with mode stamps, the feeder board,
  the ETR board with each row's basis on its face, the queues,
  the storm banner naming its declaring act. Its check
  re-performs every figure from raw store reads and executes
  the page's own JavaScript under Node. The shift handover
  freezes the board into an artefact at a named operator's
  act; acceptance is a second named act bound to the record
  hash, the handing-over operator cannot accept their own, and
  a handover over a moved record is stale on its face.
  Migration 0009.
- **R10 — The first red team.** Seventeen attacks on Rounds
  1–9, recorded in `docs/red-team.md`. Six defects found and
  fixed: severity and ETR recomputations could rewind the
  head; the closure act took any role string and accepted
  future-dated restorations into the accuracy ledger; storm
  scope overlap was judged only at the declaration door, and
  a backdated declaration could slip before an open window.
  Eight attacks held. Three findings were recorded open with
  severities (RT-15, RT-16, RT-17) rather than absorbed.
  Every defect is kept as a regression test and a
  zero-tolerance eval counter.
- **R11 — Access control.** The access pack (2026.10.1)
  states, per control-room role, the surfaces a role may read
  and the acts it may perform — an act's grant never exceeds
  the engine pack that judges the act. Principals are carried
  on every surface and judged as carried (a padded or partial
  pair is malformed; an unknown role is refused by name; the
  carried principal must be the human the act names).
  Incident reads are existence-hidden: a role without incident
  scope is answered exactly as for a non-existent incident,
  byte for byte. Every refusal lands first in an insert-only
  denial log (migration 0010), itself a guarded surface.
  RT-17 is closed by decision: the R4 closure surface, where a
  principal is carried, now requires the pack's close act and
  the gate's preconditions.
- **R12 — Oversight, and RT-15/RT-16 closed.** RT-15: the
  handover's read surface gains a derived flag — the storm
  acts declared after its moment, computed at read time, never
  folded into the artefact or its hash — so the incoming
  operator sees the moved storm position before accepting.
  RT-16: correlation pack 2026.10.2 caps an incident's span at
  four hours from its anchor; what the cap refuses stays
  visible with its named reason, and the 2026.10.1 pack is
  kept verbatim and re-performs uncapped. Operations oversight
  reporting: insert-only window reports (migration 0011) over
  the artefacts of record — volumes by state, severity mix,
  ETR accuracy by operating mode with storm and normal never
  blended, restoration outcomes, storm activity, the handover
  position — every rate carrying `{value, numerator,
  denominator}`, no section naming an incident, and a verifier
  that re-performs every figure by a second path.
- **R13 — The model gateway and cost ledger.** Every model
  call routes through one governed door that wraps the
  configured profile without changing its behaviour; a hosted
  profile that is not configured still refuses loudly, and the
  refusal is recorded. The cost ledger (migration 0012) lands
  completed, refused and errored calls as insert-only records:
  canonical sizes, token estimates by the pack's stated rule
  (canonical bytes ÷ 4, rounded up), the pack's rates, the
  exact integer cost in micro-dollars. Totals are never
  stored — they fold from the records and a verifier
  re-derives them by a second accumulation. The pack states a
  per-incident call budget and cost budget; a call that would
  cross either is refused before the port is touched, and the
  refusal is itself a ledger record. The ledger reads as a
  guarded surface under the access pack.
- **R14 — The injection quarantine (RT-18).** Two channels
  carry outside text into the record — field update text and
  alarm messages — and both now pass a versioned screen
  (quarantine pack 2026.10.1): a closed vocabulary of
  instruction shapes, run over the raw text and over any
  base64-decoded view of it. A tripped text lands in the
  quarantine book as an insert-only hold (migration 0013);
  the artefact it arrived on is stored with the field absent
  and the hold named in its place — a held crew note does not
  satisfy the restoration class, a held alarm message never
  enters the model's facts. Standing holds are visible only as
  escaped, data-only text on a guarded surface; release is a
  named reviewer's act, never the text's own reporter, and a
  released alarm message regains human readability, never
  readmission to a prompt.
- **R15 — The post-incident review.** One incident, whole, as
  an artefact: a named operator or duty manager strikes the
  review (review pack 2026.10.1, migration 0014) and it
  rebuilds the incident from its artefacts of record alone —
  membership with the rule that placed each alarm, the storm
  stamp, classification, the severity history, the ETR
  estimates graded against the closure outcome, work-order
  versions and their acts, the field record, restoration,
  the closures, contradictions, and the revision/split
  lineage. Completeness is graded on its face: each element
  the pack names stands present with its artefact ids or
  absent with a stated reason, and the verdict is `complete`
  only when every required element is present. Held text
  appears as hold metadata only. Reviews are insert-only,
  supersede by link, and a verifier re-performs every section
  by an independent walk.
- **R16 — Topology dual control.** The declaration that makes
  a topology version effective gains the second pair of eyes
  every other consequential act already had. A proposal
  states the whole declaration — version, proposer, proposed
  effective time, the change set verbatim — and changes
  nothing; one live proposal per version. A decision is a
  second named operator's act (migration 0015): the proposer
  cannot decide their own proposal under the repo's name
  folding, a rejection requires a stated reason, a decided
  proposal is not decided twice. Confirmation strikes exactly
  the stated declaration through the same strike path the
  direct act uses, with the proposal's provenance on its face,
  and `verify_proposal` re-performs the whole standing. While
  a proposal stands pending, the direct declaration of that
  version is refused, naming the proposal.
- **R17 — The direct route closed.** Topology pack 2026.10.2
  states the posture as data — `declaration_route:
  dual_control_only` — and under it the direct declaration act
  refuses every attempt, naming the governed route; the access
  guard still judges first. The closure governs new
  declarations: standing direct declarations struck under
  2026.10.1 stay read, verified and re-correlated under,
  unchanged, and the earlier pack is kept verbatim.
  `verify_declarations` re-performs every standing declaration
  — id, change set against the version payload, role and time
  legality under its own pack, one per version — and names a
  direct declaration forged under the current pack's stamp as
  struck by no route the record allows.
- **R18 — The AgentCore Policy pack.** The platform layer of
  Law 1, shipped as a pack in the repo's pattern
  (`grid_triage/policy.py`, pack 2026.10.1). The tool table
  states every catalogue tool's shape and area scope in full —
  never derived from the registry, so a catalogue that grows
  past the pack is drift the verifier can name. The Cedar face
  is one permit per shape, each naming its tools explicitly
  and carrying the area condition; Cedar's default is deny, so
  no forbid for a control-shaped call can be written from this
  table. The decision engine re-performs the pack in code and
  names its reason from a closed vocabulary (permitted,
  out_of_area, area_unstated, control_shaped, unknown_tool);
  `verify_policy_pack` grades drift in both directions. A
  Gateway denial — HTTP 200 with a JSON-RPC −32002 body — is
  detected in code and raised as a named exception, so no
  caller can mistake a denied call's empty result for data.
  No migration; the pack reads on every surface under a new
  `policy` surface in the access pack.
- **R19 — The second red team.** Rounds 12–18 attacked;
  six defects found, fixed at the root and recorded as
  RT-19…RT-24: the name fold kept separators, so "op dana"
  could release their own held text and confirm their own
  proposal (RT-19); a URL-safe base64 payload split below the
  screen's run length (RT-20); the Policy verifier compared
  action lists but never the Cedar statement carrying them
  (RT-21); `verify_proposal` let a self-consistent proposal
  struck under an unreadable pack version verify clean
  (RT-22); the ledger verifier crashed with a raw KeyError on
  a record under an unpriced profile instead of naming it
  (RT-23); an identical field-update re-post died on the
  store's uniqueness constraint instead of returning the
  standing artefact (RT-24). RT-25 records the screen's
  remaining probes — sub-threshold splits, hex, sentence-break
  evasions — as its stated vocabulary limits, pinned in tests
  so changing them is deliberate. With RT-19…RT-24 fixed and
  RT-25 bounded, no finding in the record stands open.
- **R20 — Release hardening (1.0.0).** Version 1.0.0 set in
  the package metadata, the package `__version__` and the
  lockfile; this changelog; the release notes in
  `docs/release-1.0.0.md` carrying the freeze figures; the
  README's honest status restated as the release's; the docs
  swept for stale claims (the operator gate and the Policy
  pack in the present tense, the architecture table corrected
  where it promised a later round, the layout listing the
  R18/R19 modules and checks, a truncated honest-status
  fragment removed); and the final full freeze — every gate
  green on the release tree. No new features.
