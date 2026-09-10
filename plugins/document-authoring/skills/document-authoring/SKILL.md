---
name: document-authoring
description: Russell's personal style conventions for authoring or editing any document or message that contains links or formatted prose — Confluence pages, Word docs, email, Teams messages, PRs, etc. Use whenever composing such content. It is the writer's process - the drafting loop, staging, and voice-learning - over the two rule rubrics it loads.
---

# Document Authoring Style (Russell's preferences)

Apply this whenever authoring or editing a document or message in Russell's name.

This skill is the **writer's process**: how you get to a finished message - the drafting loop, staging, and the voice-learning loop.
The rules for what the finished message must *be* live in two rubrics it reads:

**REQUIRED BACKGROUND - load both first:**
- `authoring-rules` - the medium-independent rules that bind every artifact Russell's name goes on, code comments and docstrings included. It is the rubric a writing reviewer checks against.
- `message-rules` - the message-specific artifact rules: register, warmth, asks, links, personas, emoji, sign-offs, and nudges. It is the second rubric the reviewer checks an outward message against.

The split mirrors the cover-letter writer/reviewer pattern: the two rubrics are what the writer composes toward and the reviewer checks; this skill is the generative and process side that never restates them.
Compose against what those two files say; this skill covers only the loop that gets a draft there, staged and learned from.

## The drafting loop: Read → Write → Verify → Stage → Learn (mandatory)

This is the message-shaped detail of the three-step spine in `writing-flow` (Draft → Review → Stage): load that first for the overall map, then follow the message-specific steps here.
Every message in Russell's name — a Jira comment, an email, a Teams post, a PR — runs through these steps, in order.
Skipping a step is what leaks the patterns these rules ban: reading once then composing "in the voice" is not enough.

1. **Read** — before writing a word, identify the persona/register this message is in (`1on1`, `outreach`, `announcement`, `meeting-invite`, formal, etc.) and read that section's bullets in `message-rules` plus its **Holding the voice** list.
   Compose against what you just read, not from memory.
   Two checks are worth holding from the start, since they're the ones most often missed: cold/first-touch outreach opens with the *soft* ask (gauge interest, invite a conversation) and names any hard commitment only lightly and later — **a scheduling link is a hard ask**, so don't propose a call or drop a calendar-booking link in the first cold-touch message; link instead to the event/program itself and let a reply be the next step, saving the calendar link for once they've shown interest; never restate a link, date, or detail already shared upthread; land on one ask; close with "let me/us know." And **every named event, program, or document gets its link on first mention, every single time** — including a card or ticket on an internal tracking board when the reader can open it themselves.
2. **Write** — draft the message.
   When it's on the same topic as a prior message or email thread — even if it isn't a direct response, and even when the most recent message is one Russell sent himself — anchor it there rather than composing fresh, so the follow-up answers where the conversation actually stands and the history stays together.
   In Teams, use Reply on a message in that topic; in email, reply into the existing thread on that subject.
3. **Verify** — dispatch the `writing-review` skill on your actual draft text, marked as an outward message so the reviewer checks it against both `authoring-rules` and `message-rules`.
   This step is a cold, independent check, not a self-walk - for why a separate dispatched reviewer is required rather than a re-read of the rules, see `writing-review`.
   Revise against what it finds, then dispatch a **fresh** reviewer on the revised text; repeat until it returns clean, a finding stands that you genuinely disagree with, or you've run three rounds — see `writing-review` for the disagreement and convergence rules.
   When the loop converges, mint the review receipt on the exact body file you'll stage (see `writing-review`'s **The stage gate**): the mail-staging commands are gated and refuse a draft that has no fresh receipt for its content, which is what makes this step contractual rather than a documented "should."
   **This step applies every time this skill is used to draft or edit a message, in every caller** — a provider doc or another skill that says "invoke document-authoring" gets Verify as part of that, with no separate reminder needed at the call site.
4. **Stage, never send** — a draft that survives verify is staged for Russell's approval; by default he sends it himself.
   Put the text into the real UI where it'll be sent — the ticket comment box, the Teams compose box, the email reply — via `browser-chauffeur`, so he sees it in context, edits inline, and clicks the app's own Send.
   If the UI genuinely can't be driven, show the proposed text in chat for approval instead, in a plain fenced code block rather than a blockquote - a blockquote's per-line `>` prefix rides along when Russell copies the text and corrupts the paste into the destination composer.
   The one send exception: when a channel has a programmatic send path and Russell, having reviewed the exact draft this turn, gives an explicit per-message instruction to send it, you may send that reviewed text for him (today personal Gmail via the `gmail` skill's `gmail.js --send-draft`, personal Outlook via the `ms-graph` skill's `mail.js --send-draft`, and Slack via the `slack` skill's `slack.js --send`). Default, silence, and any autonomous run mean draft-only — never infer a send. This exception is the same one an interactive **drainer worker session** uses (see `worker-core.md`); an **autonomous drain** (no live Russell present, e.g. an `auto-handle` item) has no one to give that instruction, so it stays draft-only unconditionally.
5. **Learn** — after he sends, run the **Voice learning loop** below: diff what he actually sent against your draft and update the guidance when the voice changed.

A draft that reaches Russell should already read as his, because you verified it against the specific rules — not because you intended to.

---

## Voice learning loop (keep the message rules current)

This runs after **any** drafted message — formal or conversational, any channel (Jira, email, Teams). Whenever Russell edits a draft before sending, learn from the difference:

1. After he sends (he edited it in the app UI and clicked send, or said "sent" / "learn from that"), read the **actually-sent** version from the source — Jira via the API/comment, Teams via browser-chauffeur reading the chat, email from the sent item — and diff it against your draft.
2. Classify every difference into exactly one bucket:
   - **Information fix** — a corrected fact, name, link, date, number, or scope detail.
     One-off; it does **not** change the guidance.
   - **Voice change** — phrasing he swapped, filler he cut, structure he reordered, length or altitude he adjusted.
     Durable; this is what we learn from.
3. For each voice change, distill the underlying **rule** — not the transcript.
   The learned rule almost always belongs in `message-rules` (the message artifact rubric); a rule about *how you compose* rather than what lands belongs in this skill's loops instead.
   **Search the whole target file for overlap before writing a word of new text — this is the step most often skipped, and skipping it is what produces a duplicate bullet.** Grep the entire `message-rules` `SKILL.md` for the concept (register keywords, the behavior, near-synonyms), across **every** section and persona block — Core voice, Asks, Holding the voice, Formal writing, and *every* persona under Conversational writing (`1on1`, `outreach`, `announcement`, `meeting-invite`) — not just the one persona the message you're learning from happens to match. The closest existing bullet is very often sitting in a sibling persona (an `outreach` rule can be exactly what a `1on1` message needs too); "I'm editing the 1on1 section so I only need to check 1on1" is the exact mistake to avoid. Search `authoring-rules` too when the instinct is medium-independent.
   Fold the rule into whatever bullet that search turns up — expand its scope, sharpen its language, generalize it to a cross-persona Core-voice bullet if the instinct is universal, or add a sub-case — rather than adding a new one.
   Add a new top-level bullet **only** once that whole-file search has genuinely come up empty, and name in the PR description which sections you checked and confirm none overlapped.
   **State the rule crisply as a single imperative bullet: a bold lead phrase plus one sentence, no before/after quote.**
   Add a short concrete pointer only when the rule is genuinely unclear without one; default to none.
   The goal is fewer, broader, crisper rules — not a growing list of siblings, and not a museum of examples.
4. **Make the edit as a PR to this plugin's source repo — never by editing the file you're reading.**
   These skills ship from a separate GitHub repo (`rrrutledge/rrrutledge-claude-code-plugins`); the copy loaded at runtime is an installed/cached snapshot (e.g. under `~/.claude/plugins/...`), and editing that snapshot in place is silently thrown away on the next plugin update.
   To make a change stick:
   - Locate the working clone (`~/Dev/rrrutledge/rrrutledge-claude-code-plugins`; clone it from the origin if it's not there) — do **not** edit under `~/.claude/plugins/`.
   - The voice rules are in `plugins/document-authoring/skills/message-rules/SKILL.md`; medium-independent rules are in `plugins/document-authoring/skills/authoring-rules/SKILL.md`.
   - Create a branch, make the edit there, commit, and push - don't push straight to `main`.
   - **Before opening the PR, dispatch an independent reviewer on the change** - a fresh agent that reads the *whole* file, not a re-read of your own edit - to confirm the new text isn't already covered elsewhere and that it obeys the rules in step 3 (one crisp imperative, no before/after quote, no "don't X, do Y" couplet, no em dash).
     This is the same cold check the drafting loop's **Verify** step runs, and for the same reason it names.
     Revise against what it finds, then mint the review receipt on the edited file so the PR gate lets it through: `python ~/.claude/plugins/cache/*/document-authoring/*/hooks/verify_gate.py mint <path-to-SKILL.md>` (run the newest if several are cached).
     The `gh pr create` this loop ends in is gated on that receipt (see `writing-review`'s **The stage gate**), so minting here is what lets this loop's own PR open.
   - **In the PR description, state the overlap search's outcome and the reviewer's verdict** - either "folded into `<bullet>` in `<section>`" or "searched Core voice / Asks / Holding the voice / every Conversational persona - no overlap, new bullet." This is what makes the check auditable at review time instead of invisible inside the diff.
5. Tell Russell in one line what you learned and changed, with the PR link — or, if every edit was an information fix, say there were no voice changes (no PR needed).

The goal is convergence: over time his edits should become information-only.
A send where the only differences were information fixes is the **success signal** that the voice guidance is dialed in — not a missed chance to add a rule.
