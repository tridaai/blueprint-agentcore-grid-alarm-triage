# Red team — Round 10

Round 10 built nothing new. It attacked what Rounds 1–9 built,
across every trust boundary in the system: alarm intake, field
updates, topology versions, storm declarations, work-order acts,
restoration signals, closure acts, and the console and handover
surfaces that read the record back to a human.

Method: survey the boundary first (what act can be attempted,
with what authority, against what state), then probe it against
the seeded record and crafted records, then grade the result —
**held** (the defence refused or contained the attack),
**defect** (the attack landed; fixed in this round and kept as
a regression test plus a zero-tolerance eval counter), or
**open** (a design-level finding, recorded here with a
severity, not silently absorbed).

Seventeen attacks: six defects, all fixed in this round; eight
held; three open findings. The regression tests are
`tests/test_r10.py`, numbered to match this record; the eval
gate carries one counter per defect.

## The defects

### RT-01 — a severity recomputation could rewind the head · HIGH · fixed

Severity artefacts are history the store keeps in recording
order, and `current()` reads the last one recorded. Nothing
required a recomputation's `as_of` to move forward.
*Probe:* assess the flood at T+300 minutes (SEV-1, score 94,
duration accrued), then assess it again at T+5 minutes — the
stale computation (SEV-1, score 84) became the current head.
Every surface that reads the head — the brief, the board, the
handover — would have shown the quieter, older picture of a
live fault, indefinitely, until someone recomputed forward.
*Fix:* a recomputation whose `as_of` precedes the standing
head's is refused (`SeverityRefusal`); an identical
recomputation of the head is still the standing artefact, and
a recomputation at the head's own instant over changed inputs
still supersedes it. The API answers 422; the Runtime and CLI
name the refusal. Kept as `test_rt01` and the eval counter
`rewound_severity_accepted`.

### RT-02 — a backdated estimate became the closure's estimate of record · HIGH · fixed

The same shape in ETR, with a worse landing place: the closure
picks the latest estimate with `as_of <= restored_at`, and the
"latest" was recording order, so a backdated estimate recorded
last was both the current head and the estimate the closure
graded the outcome against. *Probe:* estimate the flood at
T+30 (250 minutes), then estimate it at T+10 — the backdated
artefact became current, and the closure compared the actual
restoration against it. An operator (or a careless integration)
could choose which estimate an incident is graded against
after the fact, and the accuracy ledger — the number the
blueprint exists to make honest — would quote the result.
*Fix:* the same forward-only rule in the ETR engine
(`EtrRefusal`). The estimate of record is now the estimate
that stood when the incident was run. Kept as `test_rt02` and
the eval counters `rewound_estimate_accepted` and
`closure_graded_against_backdated_estimate`.

### RT-03 — the closure act accepted any role string · MEDIUM · fixed

Every other act in the system names its authority in a pack:
the work-order pack, the handover pack, the topology and storm
services' role checks. The R4 closure checked only that the
role was a non-empty string. *Probe:* the lone incident was
closed, in three separate worlds, with the roles `banana`,
`field_crew` and `system`. The closure is the act that writes
the accuracy outcome; it was the least disciplined act in the
system. *Fix:* the closure pack (`2026.10.1`) names the
control-room roles — `operator`, `duty_manager` — and any
other role is refused. Kept as `test_rt03` and the eval counter
`closure_with_packless_role`.

### RT-04 — a restoration could be dated after its own recording · MEDIUM · fixed

The closure checked that `restored_at` did not precede the
incident's anchor. It never checked it against `recorded_at`.
*Probe:* `restored_at` seven days out with `recorded_at` three
hours out was accepted, and the ledger gained an outcome with
an actual duration of 9,632 minutes — a restoration graded as
fact a week before it could have happened, poisoning every
mean the ledger publishes. *Fix:* a closure whose
`restored_at` postdates its own recording is refused; a
restoration recorded at the instant it happened still stands.
Kept as `test_rt04` and the eval counter
`future_dated_restoration_accepted`.

### RT-05 — a storm extension could widen onto another open window · HIGH · fixed

Scope overlap was checked at the declaration door only, and
only against windows open at the declaration's own instant.
The extension door restated scope with no overlap check at
all. *Probe:* declare a window over Feeder 7 and a second over
Feeder 12, then extend the first onto Feeder 12 — accepted.
Two windows now covered one feeder, and the mode fold, which
takes the first matching declaration in recording order,
silently named the extended window for Feeder 12: the second
cell's incidents would have been stamped, packed and reviewed
under the first cell's declaration. *Fix:* extensions are
checked against every other window's recorded segments, in
time and scope, before they land. Kept as `test_rt05` and the
eval counter `storm_overlap_accepted`.

### RT-06 — a backdated declaration could overlap an open window · MEDIUM · fixed

The declaration door's own check had the same blind spot in
time: it asked which windows were open *at the new act's
instant*, so a declaration dated an hour *before* an open
window over the same feeder saw an empty fold and landed.
*Probe:* with a Feeder 7 window open from T0, a second Feeder
7 window declared at T0−1h was accepted; both cover the
feeder from T0 on, and the fold names whichever was recorded
first. *Fix:* declarations are checked over the whole recorded
timeline — a new window that would run open-ended from its
declared instant cannot collide with any recorded segment,
including a closed window's span (a storm cannot be
retroactively declared over hours another window already
covers). Kept as `test_rt06` and the eval counter
`storm_overlap_accepted`.

A note on the eval gate, because the fix bit it first: the
gate's Round 1 loop assembled briefs stamped with wall-clock
time inside a scenario world dated two weeks earlier, so under
the forward-only rule every graded replay that followed was a
rewind and was refused — the fix working as designed, against
our own script. The loop now assembles inside the scenario
timeline. Production surfaces stamp real time, where acts on
an incident are naturally ordered; a replay that wants an
earlier instant computes it with the pure functions, which
never needed recording.

## The attacks that held

### RT-07 — Law 1: no control or send capability on any surface · held

Source scan plus behaviour. The tool registry holds read,
draft and notify shapes only; the notify shape records a
draft, and the only caller is the work-order decision on
approval, whose bodies begin "DRAFT notification — not sent."
No module under `src/grid_triage` carries a send mechanism
(no SMTP, no send call). The R10 scan extends the perimeter to
every public surface: every API route, every Runtime action
name and every CLI subcommand was enumerated and checked
against the control/send verb list — zero hits. The R9 console
is static HTML whose only request fetches the board JSON; the
console check (`scripts/check_console.py`) passes and the eval
counter `control_surfaces_present` stays at zero. Kept as
`test_rt07`.

### RT-08 — correlation window edges · held

A consequence at exactly +600s from its nearest member joins;
at +601s it does not. The edge is the pack's number, applied to
the nearest member, on both sides of the line. Kept as
`test_rt08`.

### RT-09 — nuisance alarms riding a storm · held

Under a declared storm over Feeder 12, a burst of a breaker
trip, a `meter_ping_failure`, a `comms_poll_failure` and a
`tap_step_counter` correlated to exactly {trip, ping}: the
storm pack widens the joinable set by the one kind it names,
and the other nuisance kinds stay uncorrelated evidence. Kept
as `test_rt09`.

### RT-10 — act order: gated close, stand-down, revision cycles · held

The gated close before any confirmation is refused (Round 7's
gate). Stand-down before declaration and of a stood-down
window are refused (Round 8). Re-correlation refuses cycles:
the already-revised source and the already-under-v2 revision
both refuse a second act, so lineage stays a chain, never a
loop. Kept as `test_rt10`.

### RT-11 — post-close contradiction on a split part · held

The Round 7 contradiction path was probed against the harder
target: not the original incident but its *restored split
part*. The part was closed through the gated act, then an
alarm update naming one of its member devices was posted
against it. The outcome: `post_close_contradiction`, a fresh
incident carrying the alarm and the link, the closed part
byte-identical — same row, same memberships — and the
contradiction on its record. Closed records never mutate,
including records the split created. Kept as `test_rt11`.

### RT-12 — handover integrity, and the staleness rule stated · held

Self-acceptance is refused; an accepted handover cannot be
accepted again; a stale one cannot be accepted at all. An open
SEV-1 appears in the handover's open-incidents section with
its band — sections are computed over all live-open incidents,
with no severity filter anywhere in the path.

The cosmetic-change question the round was asked to judge:
*does staleness catch a figure-preserving artefact addition?*
**The rule, stated:** staleness is judged over the six
sections the handover carries, re-performed and compared.
Artefacts that move no section — a crew note is the probed
case — do not stale a handover, and acceptance after one
still binds. Artefacts that move a section — a fresh severity
head moves the open-incidents row — stale it, by name. This is
the right rule: the handover is an accountability artefact for
its figures, not a freeze on the record; a rule that staled on
any artefact would make handovers unacceptable in any busy
hour, and the discipline would be routed around instead.
Kept as `test_rt12`.

### RT-13 — topology attacks · held

A v2-only device (`SEN-0723`) injected into a v1 incident
through a field update resolves as
`unresolvable_under_pinned_topology` — never a member, never a
silent drop. A topology declaration by a `field_crew` role, by
a blank operator, or with an effective time before its own
declared time is refused. A loaded version is immutable:
reloading v1 with one device's customer count rewritten left
the stored payload untouched — versions are insert-only, and
there is no edit path to probe. Kept as `test_rt13`.

### RT-14 — ETR basis switching · held

The mode on every severity and ETR artefact comes from the
incident's stamp, which comes from the fold at correlation
time — there is no caller-supplied mode or pack parameter on
any surface (the one hypothesis-shaped input the agent layer
accepts is refused by `EtrBasisRefusal`, Round 6). A normal
incident estimated while a storm window is open elsewhere
carries no storm component and the plain basis label; the
storm incident's estimate carries the pack's factor as its own
component line and the declaration id on its face. Kept as
`test_rt14`.

## Open findings

### RT-15 — a storm declared after a handover does not stale it · MEDIUM · open

Five of the handover's six sections re-perform against the
current record at acceptance. The sixth — the storm section —
is the fold *at the handover's own moment*, by construction:
it answers "what was declared at the moment of handover", and
a window declared afterwards is not in it, so verification
reports no drift and the incoming operator can accept a
handover that says "no storm" while a storm is now in force.
The artefact is internally honest — it names its moment, and
its storm section names windows and spans, not a count that
silently aged — and the naive fix is wrong: re-performing the
storm section live would stale every handover assembled
during a storm the moment the window stands down, making
storm handovers unacceptable by construction. The finding is
recorded for a design round: the acceptance surface should
*show* declarations recorded after the handover's moment
alongside the artefact, without folding them into its hash.
Until then the discipline is procedural — assemble the
handover at the shift change, not before it. The current
behaviour is pinned by `test_rt15`, so changing it is a
deliberate act, not a drift.

### RT-16 — correlation chaining grows an incident's span without bound · LOW · open

The pack's window applies to the *nearest member*, so a ladder
of consequence alarms spaced just inside the window chains an
incident's membership span arbitrarily far from its anchor —
the probe chained one incident's members across five hours of
nine-minute-spaced sags. The dangerous direction held: anchors
are placed before consequences are attached, so two real
faults never merge into one incident (the ladder's second
breaker trip opened its own incident and took no members).
Whether an incident's span should also be capped is a pack
decision for the utility — the chaining behaviour *is* the
pack's stated rule today ("within the window of any alarm
already in it") — recorded here rather than silently changed
in a red-team round. Pinned by `test_rt16`.

### RT-17 — the R4 closure surface is not itself restoration-gated · LOW · open (accepted boundary)

Round 7 gated the *operational* close path
(`RestorationService.close_incident` — confirmation, work
orders, readiness) and left the Round 4 `ClosureService`
callable directly through the API, Runtime and CLI, as the
surface that records a closure once restoration is established
by other means. That remains a deliberate boundary, and this
round hardened what the un-gated surface can write: RT-03 and
RT-04 mean it can no longer record a packless role or a
future-dated restoration, and it still refuses a second
closure, a pre-anchor restoration and unknown incidents.
Whether the base surface should additionally demand a
confirmation artefact is a product decision — it is also the
surface the seeded history and the eval record are built with
— and is recorded here rather than decided by the red team.

## Boundaries the round relied on

The seeded record (the Feeder 7 flood, the lone Feeder 3 trip,
the Feeder 12 nuisance feed), the versioned packs as the only
place parameters live, content-addressed artefacts, and the
store's immutability errors. The Postgres backend runs the
same suite and the same eval gate; the fixes are
service-level, above both stores.
