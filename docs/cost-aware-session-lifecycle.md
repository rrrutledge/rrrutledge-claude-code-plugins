# Cost-aware compaction, fresh-session, and handoff triggers

Make a Claude Code tab aware of when it has grown expensive, so compaction, a fresh session, a handoff,
or delegating to a subagent happens on its own rhythm instead of waiting for Russell to direct it.

This is a design plan, not an implementation.
It ends at the reviewable design plus the exact edits each change would make.

## The problem, in Russell's words

Russell runs Claude Code as a fleet.
The drainer reads his email, Trello, and Slack and launches worker tabs; he keeps 10-14 tabs open at
once and cycles through them, giving whichever tab is in front of him the next direction.
He does not track, per tab, how deep that session has grown or whether it should compact, clear, or
hand off.
All he holds is "the next thing to work on."

He wants the tabs themselves to notice when it is a good moment to reset, so it happens without him
deciding it each time.
He does not care whether a tab closes and a new one opens in its place - he only ever looks at whichever
tab is open and responds to it.

The reframe at the heart of this:

> When he asks "should we hand off or keep going here?", the answer is too often "keep going, I already
> have the context loaded." That is a capability lens. It should be a cost-efficiency lens.

## Why the reframe is literally true

Per-turn cost is close to a direct function of the current context-window size, because cache-read
dominates and the whole context is re-read every turn.
That size only ever grows within a session; it never falls without a compaction or a fresh start.

So "I already have the context loaded" names the exact thing that makes continuing expensive.
The loaded context is not a saving carried forward for free - it is a tax paid again on every turn.
A reset drops that tax to near zero.
The feeling that the loaded context is a reason to stay is precisely the cost that argues to leave.

## What the data proved

Parsing every Claude Code transcript (`~/.claude/projects/*/*.jsonl`) established the cost shape:

- **Cache-read is 95.9% of all tokens.** Cost is dominated by re-reading the existing context each turn.
- **CLAUDE.md is a rounding error** - about 9,150 tokens, roughly 5% of cache-read. Trimming it is not
  the lever, so this design does not touch it.
- **The quadratic is real and concentrated.** The 471 sessions over 120 turns (2.4% of all sessions)
  drove about 73% of total spend. A handful of marathon sessions is most of the cost story.
- **The signal is cache-read per turn, not turn count.** One 71-turn session cost about $20 because a
  roughly 250k-token skill loaded early to check one number, then rode the re-read prefix on every one of
  the 55 following turns. A single large early load (a big skill, a browser-chauffeur DOM dump, a large
  file read) builds a heavy fixed prefix that taxes the rest of the session as much as raw length does.
  So the right thing to watch is cache-read per turn, and the right fix for a heavy-prefix session is
  often "this reference is done, shed it," not "you are deep."

### Job applications are the single biggest workflow

An earlier cut classified sessions by their launching message and concluded job applications "barely
register." That was a classification artifact: the classifier matched drainer workers first and filed
every drained job card under "trello," so only sessions where Russell typed "apply for X" himself ever
counted as job applications.

Re-cutting the 700 largest transcripts by session title and by real `apply-for-job` skill invocations
tells the true story:

| Workflow (by session title, directional) | # sess | avg peak cache-read/turn | total $ | % of top-N |
| --- | --- | --- | --- | --- |
| **Job application** | 161 | 289k | 7,471 | **28%** |
| ISC (summit outreach + invoice + newsletter) | 73 | ~255k | 2,551 | ~10% |
| Strock / divorce financial review (personal) | 18 | 405k | 1,453 | 5.5% |
| Blog / LinkedIn post | 7 | 455k | 831 | 3% |
| Drainer / infra / other interactive (mixed tail) | ~440 | ~250k | ~14,000 | ~53% |

Job applications are the single biggest clearly-identifiable slice at about 28% (about $7.5k), and it is
an undercount - cover-letter work often spins off into its own handoff session (LVS, Indeed, the
cover-letter redesign) that marathons separately and lands in other buckets. The top of the expensive
list is unmistakable: micro1, Ferguson, Las Vegas Sands, KinderCare, eBay, Webflow, GiveDirectly,
HubSpot, Charles Schwab, all `apply-for-job` sessions at 700-1,100 turns, peaking at 700-900k cache-read
tokens per turn - more than triple the point where a session is already expensive.

Three caveats on the cut, so the numbers are read honestly:

- **It is the `apply-for-job` skill, not a Trello board.** Grepping transcripts for board IDs is useless,
  because CLAUDE.md and the drainer `context.md` embed every board ID in the system prompt, so all six
  boards appear in nearly every transcript. The clean signals are the session title and real Skill-tool
  invocations.
- **The ~53% "drainer / other" bucket is a polluted catch-all**, not 53% of genuine infra builds. Many
  untitled drainer sessions open with the large system-reminder (which mentions "drainer" repeatedly), so
  the title heuristic over-collects there. It is the hard-to-title long tail, not one workflow.
- **The digest is not a top bucket** by this cut. An earlier "8%" figure was itself soft, and it is out
  of scope here.

The conclusion: the biggest, most concentrated, and cleanest-to-target win is the `apply-for-job`
workflow. That leads. A generic per-turn nudge for the untunable long tail is a later backstop.

## The four levers, and how to choose

The one question underneath every reset is: **how much of this session's loaded context does the next
chunk of work actually need?**

| Lever | What it does to the context | Best when | What it costs you |
| --- | --- | --- | --- |
| **Subagent** | Runs the work in a separate, isolated context; only the distilled result returns, and the main cache is untouched. | A heavy one-off read or investigation you need one answer from. Pre-emptive - it stops the prefix from ever bloating. | You lose the subagent's detailed reasoning; you must phrase the delegation narrowly. |
| **Handoff / fresh tab** | Throws everything away except the seed you write; the new tab starts with a near-zero prefix. | The next work is a **different task or a fresh phase** that needs little from here, so a short seed captures it. | You author the seed by hand; anything you forget is gone. |
| **/compact** | Summarizes the conversation and replaces the verbatim history with that summary, then continues the same session; the per-turn prefix shrinks. | The **same task** continues but the context is bloated with dead references or failed tries. | The summary is lossy and unrecoverable once run; you steer it only via `/compact <instructions>`. |
| **/clear** | Wipes the conversation in the same tab, no summary carried forward. | Rarely his best tool - a fresh tab is cleaner and keeps nothing he would miss. | Total loss with no summary; only saves opening a new tab. |

Two rules make the choice concrete:

1. **Is the state the next work needs cleanly expressible in a short seed?** If yes, hand off - the reset
   is total and the seed is cheap. If it is a big fuzzy working state on the same task, compact - it
   preserves that automatically where a seed would lose fidelity. This is the real answer to "why compact
   instead of a handoff with the info in it": for same-task work you often cannot distill the working
   state into a seed without losing it.
2. **Can the heavy read be foreseen before it pollutes the prefix?** If yes, delegate it to a subagent so
   it never enters the main context. Subagent delegation beats compaction for one-off work, because a
   compact summary is unrecoverable while a subagent's result stays available as normal context.

## Track 1 (primary): the apply-for-job workflow

The `apply-for-job` skill (`~/Dev/personal-ai-pod/.claude/skills/apply-for-job/SKILL.md`) runs all five
steps in one session behind four human-in-the-loop gates:

1. Research the role, then pause for the fit decision.
2. Reach out for a warm intro.
3. The gap conversation, then a master-resume PR that Russell merges.
4. Tailor the resume, then a tailored-resume PR.
5. Render to PDF and .docx.

That single long session is why these runs reach 700-1,100 turns, and Step 1's research payloads (company
pages, the JD, the careers-site posting, the network CSV) ride the re-read prefix through every later
step. Two changes attack that directly.

### Delegate Step 1 research to a subagent

The heavy reads in Step 1 - company research, the job description, the careers-site posting, the
`network/linkedin-connections.csv` grep - happen in a subagent that returns a structured research brief
(fit read, referral candidates, the JD link, the key requirements to answer). The main session presents
the brief and drafts from it; the raw pages never enter its context. This is the single highest-leverage
change, because it removes the heavy-prefix cause that drives the 700-900k per-turn peaks.

Edit home: `apply-for-job/SKILL.md` Step 1, so the research reads are delegated rather than done inline.

### Reset at the phase boundaries, especially the master-merge gate

The skill's gates are natural reset points. After Step 3's master-resume PR is merged, the gap
conversation and research context is largely dead weight for Step 4's tailoring.

- **Hand the tailoring off to a fresh tab** seeded with the merged master resume plus the Step 1 brief -
  a short, clean seed, since the tailoring needs those two artifacts and little of the conversation. This
  is the strongest reset and it fits the "different phase, short-seedable state" rule.
- **Or compact** at that boundary when staying in one tab, with `/compact focus on the merged master and
  the target role`.
- **Cover-letter work carries the same discipline.** A cover letter that spins into its own handoff is a
  fresh phase, seeded with the finished resume and the brief, not a continuation of the marathon.

Edit home: `apply-for-job/SKILL.md` at the Step 3 to Step 4 transition, plus the cover-letter guidance.

### The decision framework it draws on

The four levers and the two choice rules above are stated once, authoritatively, in the global
`~/.claude/CLAUDE.md` "Handoffs & session model default" section. The skill references that framework
rather than restating it, and the same section carries the handoff-chain discipline below.

## Track 1 also: keep handoff-chained sessions resetting

Handoff-chained sessions are about 20% of the expensive set and the longest category (about 337 average
turns): a handoff starts fresh but then grows into its own marathon, so handing off once is not enough.
The handoff-launch guidance in `~/.claude/CLAUDE.md` should state that the new session inherits the same
compact/re-handoff rule - it compacts at its own phase boundaries and re-hands-off when it crosses into a
different task, rather than running unbounded. This is where the cover-letter handoffs from Track 1 get
their discipline too.

## Track 2 (deferred backstop): the generic per-turn nudge

Deferred to a second phase, after Track 1 lands and its dent is visible, and parked on a Trello card
(Personal Follow-Up, https://trello.com/c/Bm2MVdLT) that resurfaces in mid-September to decide whether it
is still worth building. Its value is the untunable long tail - the Strock divorce financial reviews,
blog posts, and ad-hoc interactive sessions that have no skill to tune but still marathon (Strock peaks
at about 405k per turn, blog at about 455k).

The design, when built: extend `close-check.py` (the existing Stop hook) so that on each Stop passing its
once-per-real-turn gate it tail-reads `transcript_path`, takes `cache_read_input_tokens` from the latest
usage line as the per-turn re-read size, maps it to a band, and on entering a new higher band appends one
line to the `additionalContext` it already injects (tracking the high-water band in
`~/.claude/close-check/<session_id>.band`). Any read or parse failure skips the nudge.

- The primary signal stays Russell's own next message - a different task is the strongest reset cue. The
  bands only gate whether acting is worth it: green below ~120k (continue freely), yellow ~120-250k (reset
  at a boundary), red above ~250k (reset actively). Observed peaks of 400-900k confirm the expensive tail
  sits deep in red, so an early nudge is warranted.
- The nudge text matches the cause: a heavy one-off load that now rides every turn reads "a large
  reference is riding every turn - `/compact` to drop it, or delegate that kind of lookup to a subagent";
  a genuinely deep same-task session reads "consider `/compact`"; a deep session facing a different task
  reads "hand off to a fresh tab."

Feasibility for this is already confirmed: the Stop hook receives `transcript_path` and `session_id`; the
transcript's latest usage line gives the per-turn re-read size from a single tail-read (about 222,660
tokens from one line on a live transcript); a hook cannot invoke `/compact`, so it nudges and the model or
Russell resets. The transcript format is officially internal, so the hook fails safe on any parse error,
exactly as `close-check.py` already does.

## What is automatic, and what is a nudge

Lean automatic only where the mechanism is already transparent and the boundary is clean.

- **Silent auto-handoff at a clean drainer boundary** is safe, because Russell does not track which tab is
  open. When a worker has finished its item, its context is deep, and the next direction is a clearly
  different task, the model spawns a fresh seeded tab and closes its own. The daisy-chain machinery exists
  already (`launch-session.ps1`, `close-session.py` / the `session-mgr:close` skill, the live-session
  registry so nothing is orphaned); this design adds only the trigger.
- **Everywhere else it is a one-line nudge** the model or Russell acts on. Compaction is always a nudge,
  since a hook cannot run `/compact`.

The dividing line: auto-handoff only when the current item is genuinely closed and the next task is
clearly unrelated. Anything mid-task, ambiguous, or in live back-and-forth gets a nudge, never a silent
action, so a live conversation is never thrown away.

## Where each change lands

One load-bearing home per rule, so the same guidance is not restated in four places.

| Concern | Home | Change |
| --- | --- | --- |
| apply-for-job sheds research context | `~/Dev/personal-ai-pod/.claude/skills/apply-for-job/SKILL.md` (Step 1) | Delegate the company / JD / careers-site / network-CSV reads to a subagent that returns a research brief. |
| apply-for-job resets at phase boundaries | same file (Step 3 to Step 4 transition, cover-letter guidance) | Hand tailoring off to a fresh tab seeded with the merged master and the brief, or compact at the master-merge gate; cover letters are a fresh seeded phase. |
| The cost-lens decision framework (four levers, the two rules) and handoff-chain discipline | `~/.claude/CLAUDE.md` "Handoffs & session model default" | State the framework once; a chained session inherits the compact/re-handoff rule. |
| Generic per-turn nudge (deferred) | `close-check.py` (OneDrive-backed Stop hook) | Phase 2: tail-read `cache_read_input_tokens`, band mapping, high-water file, cause-matched one-line nudge. Fail-safe. |

Out of scope: the digest, and any change centered on CLAUDE.md size.

## Risks and fail-safes

- **Subagent research loses nuance.** The brief must carry what Step 1's presentation needs (fit read,
  referral candidates, JD link, key requirements). Phrase the delegation to return those explicitly; if a
  later step needs a detail the brief dropped, it re-fetches that one item rather than the whole page.
- **Transcript format drift** (Track 2). The hook treats any parse failure as no-nudge, matching
  `close-check.py`'s posture. A format change degrades the feature to silence, not a broken turn.
- **Auto-handoff losing live context.** Bounded to clean, closed boundaries with a clearly unrelated next
  task; everything else is a nudge, and the live-session registry means no work is dropped if a spawn
  races a close.
- **Compaction is lossy and one-way.** Where a heavy read can be foreseen, prefer a subagent, whose result
  stays recoverable, over compacting it away.

## Rollout order

1. **Delegate apply-for-job Step 1 research to a subagent.** Biggest single win, self-contained, and it
   validates the subagent lever on the workflow that needs it most.
2. **The CLAUDE.md decision framework** (four levers, the two rules, handoff-chain discipline), so every
   session and handoff shares one reasoning model.
3. **apply-for-job phase-boundary handoff/compact**, referencing that framework.
4. **Track 2 generic hook**, only after 1-3 land and the remaining tail is worth a backstop.

## Open questions for Russell

- Auto-handoff aggressiveness: start nudge-only, or include the silent drainer auto-handoff from the
  first change?
