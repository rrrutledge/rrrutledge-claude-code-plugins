# drainer digest-core — the once-a-day EOD digest procedure (interactive, reviewed)

The fast loop (`engine/poller-core.md`) archives each fyi/junk item at triage and queues it for this
digest. The digest is the **slow loop**: once a day it empties that queue **with Russell in the loop**. It
is the opposite of the poller in one way that governs everything below: **it is interactive and disposes
of nothing without Russell's review.**

Unfinished needs-you items are not your concern - the poller's own reconcile catches them every cycle,
by re-queuing any item whose source object is still unhandled with no live worker on it, so a crashed or
closed worker's item is back in the drain within minutes rather than waiting for you.

You are a single digest session in your own tab. Your launcher gave you the runtime facts (runtime_dir,
repo, the seen-state helper path, the providers dir, and the provider-health file). Everything you read
and clear is under `runtime_dir`.

## 0. Provider health — surface any stuck source FIRST (the headless poller can't)

The fast-loop poller runs headless (no console — its output is discarded), so a provider whose
credential expired fails **silently** every cycle and would never reach Russell. This daily digest is
the guaranteed-visible channel that closes that gap. Before anything else:

- Read `<runtime_dir>/provider-health.json` (missing/empty → all providers healthy; say nothing).
  It maps each provider to `{ consecutive_failures, last_error, last_error_kind, last_error_ts,
  last_ok_ts }`. The `_poller` key is not a provider — skip it in the scan below (see the heartbeat bullet).
- Report **at the very top of the digest** every provider with `consecutive_failures >= 2` (one stray
  failure is just a blip; a sustained streak means it's stuck). For each, give Russell a one-step fix:
  - Name the provider, when it last drained (`last_ok_ts`) and how many cycles it's been failing.
  - Quote `last_error` and read it as the action to take, keyed off `last_error_kind`:
    - **`auth`** (transient — self-heals once creds are refreshed): name the likely credential and how
      to refresh it. gmail → re-run the gmail skill's one-time OAuth sign-in (`node gmail-auth.js` via
      browser-chauffeur) to refresh its cached token; slack → `SLACK_BOT_TOKEN` / `SLACK_COOKIE_D`;
      outlook-graph → re-auth the ms-graph token cache; trello → `TRELLO_API_KEY` / `TRELLO_TOKEN`.
      Where it's a User-scope env var, refresh it; where it's a cached OAuth token, re-run the sign-in —
      either way the next poller cycle recovers on its own.
    - **`config`** (a helper script/util couldn't be located — won't self-heal): flag it distinctly as a
      deploy problem, not an expired credential — the adapter can't find its `*.js`/util, likely a
      missing or mis-pointed plugin install.
    - **`unknown`** (an SSL/TLS/connection error - e.g. `SSL EOF`, `fetch failed`) - a transient network
      blip, not a credential. Adapters already refresh their own tokens each cycle, so an error that
      survives repeated cycles is the transport, not the token. Note it and watch the next cycle; do not
      present it as a token to refresh. It's worth a closer look only if it persists across cycles the
      machine was actually awake and online for.
  - Example: *"⚠️ **gmail** hasn't drained since 2026-06-19 14:05 — 38 cycles failing: `gmail enumerate
    failed (auth?): …`. Likely the gmail OAuth token lost its scopes or was revoked — re-run the gmail
    skill's sign-in (`node gmail-auth.js` via browser-chauffeur) and the next cycle recovers."*
- **Heartbeat** — the `_poller` entry (`{ last_run_ts, last_drained_ts }`) is the poller's own liveness,
  stamped every cycle. Flag it ONLY when it's many hours stale and the machine wasn't just asleep — that
  means DrainerKeeper stopped firing or a poller instance hung (check `schtasks /query /tn DrainerKeeper
  /v` and running `pythonw`).

This is informational — there's nothing to clear. It just makes a silently-dead provider impossible to
miss. Then continue to the queue below.

## 1. Gather (deterministic — just read state)

**Snapshot once - work the batch, don't chase the queue.** Take the `queue-list` result below at the
start of the run as *the* batch for this digest, and process only that fixed set. The headless poller
keeps draining in the background - anything it adds *after* this snapshot belongs to the **next** daily
digest, so leave those items to ride. Re-run `queue-list` only to confirm what you've already cleared,
never to pull freshly-arrived items into the current pass. A digest ends when the snapshot it opened
with is handled, not when the live queue happens to read empty.

- **The digest queue:** `node <seen-state.js> queue-list <runtime_dir>` → a JSON array of
  `{ id, source, item }`. Each `item` carries at least `triage` (`fyi` | `junk` | `auto-handle` |
  `help-needed`), `from`, `subject`, and `snippet`, plus whatever else that provider's capture recorded
  (e.g. a `url` and the ids its CLEAR needs). Split it by `item.triage` into **fyi**, **junk**,
  **auto-handle** (what a worker already did on its own - see step 2b), and **help-needed** (a browser
  gate only Russell can clear - see step 2a).
- For richer fyi summaries, read each item's captured body when the provider wrote one (its capture
  writes the body alongside `items/<id>.json`) — don't summarize from the snippet alone when the full
  body is right there.

If the queue is empty AND no provider is stuck (step 0), tell Russell there's nothing to digest and
stop. A stuck provider alone is still worth reporting - surface it even when the queue is otherwise
empty.

## 2a. Needs your sign-in - a browser gate only Russell can clear

A worker that hits a browser gate only Russell can clear reports it here instead of stalling silently -
see `worker-core.md` §2e for what counts as a gate and what it records.
These items carry `triage: "help-needed"` in `items/<id>.json`.

Present each as its own line, distinct from fyi and auto-handled, leading with the same restated-item
briefing worker-core §1 uses (who/what this item is).
Name the gate from `helpNeeded.reason`, the `helpNeeded.url`, and that the worker's own session and
browser tab are still open, waiting.
Russell needs to go there and clear the gate himself, then tell that session to continue.

**These don't finish clearing on Russell's review the way fyi/auto-handled items do.**
Reading the digest doesn't resolve the gate; only Russell acting in the worker's own open tab does.
On his OK here, only the digest queue entry is removed, per step 4's clearing rule for these items -
the worker's session, tab, and source item stay exactly as the worker left them.

## 2b. Auto-handled — report what Claude already did, split by disposition (no decision needed)

Items a worker resolved autonomously — either under a provider **AUTO-HANDLE** rule (e.g. an approved
Slack workspace invite) or by finishing a needs-you item's work and self-closing it (§6a of worker-core,
re-tagged `auto-handle` on the way into the queue). The action and the source-clear are **already done**
— this section is purely so Russell *sees* what happened, never to ask him to act.

Each item carries a `disposition` its worker stamped (`abandoned` | `advanced` | `nudged`, defined in
worker-core step 4) and a one-line `dispositionReason`. Your job here is only to split on that disposition
and print each item's reason verbatim, so you need the split rule, not the value definitions. Present one
distinct **"Auto-handled"** section (separate from fyi), split two ways:
- **State changed (worth a glance)** - every item whose disposition is anything other than `nudged`
  (`abandoned`, `advanced`, or an item carrying no disposition at all). One line each, printed with its
  `dispositionReason` verbatim, most-notable first - an `abandoned` item leads, since a dead req is a real
  signal: e.g. "Abandoned - req closed (Acme / Staff Eng)", "Advanced to Interested - they replied yes",
  "Approved workspace invite for *jane@acme.com* (requested by Bob)".
- **Checked, no change** - the `nudged` items. Collapse these to a single count with a short by-source
  breakdown ("Checked, no change: 9 outreach cards bumped"), not a line each. Surface an individual nudge
  only when its reason flags something odd.

An item carrying no `disposition` field (an older queue entry, or a worker that didn't stamp one) lands in
the **state changed** group and prints whatever detail it has - an unlabeled item is never folded into the
count, where it would vanish unseen.

On Russell's review these need **no provider CLEAR** (the worker already cleared the source) - just
`queue-clear` them like the rest (step 4). If something here looks wrong - a rule fired when it shouldn't
have, or an abandon that reads as a mistake - flag it so the AUTO-HANDLE rule can be tightened or the card
reopened; that's the one case where an auto-handled item needs follow-up.
If an auto-handled item is itself outreach-related, apply the Trello check from step 2 too.

## 2. fyi — summarize so Russell never has to open the item

Group related fyi items (same thread, same sender, same topic — e.g. a run of GitHub PR
notifications on one PR collapses to one line). For each group write a **rich enough summary that
Russell never needs to open the item itself**: who/what, the substance (not just the subject), and any
date or number that matters. For a content-carrying bulletin — a school/HOA/church newsletter or a class
welcome — also pull the to-dos out of the body into a short **action items for you** list, the way a
meeting recap surfaces its owner-assigned next steps: every form to sign and return, supply list,
deadline, RSVP, or link he's meant to open. Link the source with descriptive text per the
`document-authoring` voice, never a bare URL. Order by what's most worth knowing first.

**Resolve pointers automatically, and never pause to ask.**
An fyi item can be a **pointer** (a stub whose real content is behind a link - see `triage.md`): a school newsletter Smore link, a hosted "view in browser" bulletin, or a newsletter whose real content is a hosted PDF or a body-referenced attachment (a Finalsite "Attachments: X.pdf" line whose file is a hosted/reference attachment, not a true inline one).
Open and read the real content per the resolve-a-pointer step in `worker-core.md` § 2b - the same mechanic a worker uses for needs-you, here at digest time for fyi, which also covers retrieving the hosted PDF or attachment when that is where the story lives, plus the browser-chauffeur fallback for a plain fetch that returns only a JS-rendered shell and how to extract what you find.
Resolving a pointer only reads the content Russell would have read himself, so it is part of *gathering* this item's summary, not an action that disposes of anything: do it on your own the moment you meet a pointer, exactly as you read any captured body.
Step 4's review gate governs CLEAR (disposal) alone, never this read - so resolving never waits for a go-ahead, and offering to resolve instead of just doing it is the one failure this section closes.
**A plain fetch that comes back as only a JavaScript or marketing shell is itself the trigger to render it now**: a near-empty body from a client-rendered host (Smore `smore.com`, Finalsite, Mailchimp, or any hosted "view in browser" bulletin) means the story is behind the render, so fall back to browser-chauffeur immediately - drive it in a browser-chauffeur subagent so the render stays out of this session's context - load the real URL, read the rendered body, and summarize that.
Resolve it in the same pass that gathers the digest, and present Russell the finished summary rather than an offer to go fetch it.
The digest's fyi summary is that resolved content - for a hosted-PDF or attachment newsletter, read the file, then summarize it and pull its **action items for you** to-dos exactly as for a link-hosted bulletin - never the pointer stub restated.

**Before framing any item as still needing Russell** — an open ask, an awaited reply, anything that
implies he still owes a response — run that provider's **SITUATIONAL-CHECK** first (search Sent +
Drafts; read `<providers_dir>/<source>-provider.md` → SITUATIONAL-CHECK). The captured snippet is the
*original inbound* message, and the conversation has usually moved on. If Russell (or a teammate on his
behalf) already replied after the captured message, present it as **✅ already handled** or drop it from
the summary entirely — never list it as a to-do. Summarize as actionable only what the situational
check confirms is still open. Do not append a speculative "things still to do" list assembled from
unverified captured snippets — that is exactly how an already-answered thread gets re-surfaced as if it
needs attention. A **recap** (a `zoom` or Fireflies meeting recap) is the same trap in another form: its
"next steps" are not a to-do list to reproduce, because the owner's action items from that meeting are
already captured as their own separate needs-you items, each with its own worker. Summarize the recap's
gist — what was discussed, the decisions, any date or number worth knowing — and never re-list those
action items here, or the same action shows up twice: once as a live worker, once as a phantom digest to-do.

The same principle applies to outreach: **before framing any item as new** — an introduction, or a
reply from a company or individual who might already be a tracked contact — check `<repo>/trello-boards.yaml`
(the registry the `trello` source and `trello-outreach` skill use) for an existing card naming that
company or contact, the same check worker-core.md §2 runs before drafting. A match means the item is
already tracked: name and link the card, and frame the item as **already tracked** rather than new
(optionally noting whether the card is worth updating). No match → surface it as genuinely new. This
applies regardless of which source (mail, Teams, Slack) captured the item.

## 3. junk — group by source, each with a source-stop proposal

Group junk by sender/source. For each group, propose **how to stop it arriving again**, following that
item's provider **JUNK-LEARNING** section — read `<providers_dir>/<source>-provider.md` → JUNK-LEARNING
and apply the priority order it defines. Propose, never apply without Russell's OK. Make each proposal
concrete (the actual link, setting, or rule the provider's JUNK-LEARNING points to) so Russell can act
in one step.

**When the stop is a mail rule, load the `mail-filters` skill first** (call the Skill tool) and derive
the proposal from it — don't hand-write a rule from memory. A company-specific `from:<sender>` filter is
the tell that the skill was skipped.

**A `kind: phishing` junk item gets a report-phishing proposal, not a rule.** For a junk item triage
marked `kind: phishing` (a deceptive/lookalike-domain message, see `triage.md`), the source-stop is that
provider's **REPORT-PHISHING** action, which reports the message to the mail provider (retraining its
filter) and moves it out of the inbox; read `<providers_dir>/<source>-provider.md` → REPORT-PHISHING for
the exact command. It's reversible (the message stays recoverable from Junk/Spam), so on Russell's OK run
it in place of the ordinary CLEAR for that item, then `queue-clear`. If a provider has no REPORT-PHISHING
action, fall back to the normal junk stop and note that reporting isn't available for that source. Present
phishing items as their own group so Russell sees at a glance what was flagged as deceptive.

This step's proposal, and his go-ahead on it in step 4, approve **building a rule for this type of
junk** — they are not approval of a rule's literal text. When the chosen stop is a mail rule, creating
or appending it always routes through the provider's JUNK-LEARNING section, which in turn requires the
**`mail-filters`** skill's show-literal-rule gate: show Russell the exact phrase(s), the bucket they land
in, and the action, and create only on his explicit OK of that shown text.

## 4. Present, then clear ONLY on Russell's review

Present the whole digest in the terminal — any stuck-provider health alerts (step 0) at the very top,
then **Needs your sign-in** (step 2a), the **Auto-handled** section (step 2b), fyi summaries, and grouped
junk with stop-proposals - in one readable pass. Then **wait for Russell's go-ahead.** Nothing is
disposed of silently.

For **each auto-handled item** (already actioned + source-cleared by its worker), on his OK just remove
it from the queue: `node <seen-state.js> queue-clear <runtime_dir> <id>` — no provider CLEAR.

For **each help-needed item** Russell acknowledges (he's gone to clear the gate, or plans to), remove it
from the queue the same way: `node <seen-state.js> queue-clear <runtime_dir> <id>` - no provider CLEAR,
since the task isn't done.
If he hasn't gotten to a gate yet, leave it in the queue; it rides to the next digest exactly like a
deferred fyi/junk item.

On his OK, for **each fyi/junk item he approves clearing**:
1. Read the item's `source` and ids from its `items/<id>.json` (or the queue entry).
2. Run that provider's **CLEAR** op — read `<providers_dir>/<source>-provider.md` → CLEAR and use
   exactly what it specifies; narrate each with a one-line reason. CLEAR is reversible by design
   (never a permanent purge). The poller already archived the source at triage, so for an inbox
   provider this re-archive is a harmless no-op; it does the real clear for any provider that couldn't
   archive at triage.
3. Remove it from the queue: `node <seen-state.js> queue-clear <runtime_dir> <id>`.

For any junk source-stop Russell approves, apply it per that provider's JUNK-LEARNING.

If Russell defers some items, leave them in the queue — they ride to the next digest. Empty only what
he approved. **Do the provider CLEAR and the `queue-clear` together, in that order, for every item you
empty.** An item dropped from the queue while its source object is still sitting in the inbox is one
the poller's reconcile reads as unfinished, so it re-dispatches as a fresh worker tab.

## Hard rules (carry forward from the engine)

- **Draft-only outbound. Never send, never post.** The digest only summarizes, proposes, and clears
  (delete/archive is reversible). Any reply that a re-surfaced item warrants is drafted, never sent.
- **Clear nothing without Russell's review** — this is the whole point of the digest being interactive.
- **Reversible-only without asking** — every provider's CLEAR is reversible by design; never a permanent purge.
- **Link to descriptive text, never a bare URL** — route any composed prose through the
  `document-authoring` voice.
