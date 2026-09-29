---
name: document-authoring
description: Russell's personal style conventions for authoring or editing any document or message that contains links or formatted prose — Confluence pages, Word docs, email, Teams messages, PRs, etc. Use whenever composing such content. It is the writer's process - the drafting loop, staging, and learning from the send - over the two rule rubrics it loads.
---

# Document Authoring Style (Russell's preferences)

Apply this whenever authoring or editing a document or message in Russell's name.

This skill is the **writer's process**: how you get to a finished message - the drafting loop, staging, and learning from the send.
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
   **When the message reports on work you're doing yourself, finish that work first.** Draft the status update only once every action it describes is actually done, including a step only moments from finishing, since a draft written in parallel ends up describing something as upcoming that should already be done by the time the message goes out.
3. **Verify** — dispatch the `writing-review` skill on your actual draft text, marked as an outward message so the reviewer checks it against both `authoring-rules` and `message-rules`.
   This step is a cold, independent check, not a self-walk - for why a separate dispatched reviewer is required rather than a re-read of the rules, see `writing-review`.
   Revise against what it finds, then dispatch a **fresh** reviewer on the revised text; repeat until it returns clean, a finding stands that you genuinely disagree with, or you've run three rounds — see `writing-review` for the disagreement and convergence rules.
   When the loop converges, mint the review receipt on the exact body file you'll stage (see `writing-review`'s **The stage gate**): the mail-staging commands are gated and refuse a draft that has no fresh receipt for its content, which is what makes this step contractual rather than a documented "should."
   For an outward message, mint with `--score-context`, per `send-confidence.md`.
   **This step applies every time this skill is used to draft or edit a message, in every caller** — a provider doc or another skill that says "invoke document-authoring" gets Verify as part of that, with no separate reminder needed at the call site.
4. **Stage, never send** — a draft that survives verify is staged for Russell's approval; by default he sends it himself.
   Put the text into the real UI where it'll be sent — the ticket comment box, the Teams compose box, the email reply — via `browser-chauffeur`, so he sees it in context, edits inline, and clicks the app's own Send.
   If the UI genuinely can't be driven, show the proposed text in chat for approval instead, in a plain fenced code block rather than a blockquote - a blockquote's per-line `>` prefix rides along when Russell copies the text and corrupts the paste into the destination composer.
   **LinkedIn is the standing case of this** - by default, don't draft LinkedIn text at all, since Russell writes these himself; only draft one on his explicit ask, and never automate LinkedIn itself, so it's always handed over in chat rather than staged in the composer. Steps 1-3 (Read, Write, Verify) still run on it in full before it reaches Russell, the same as any other message.
   The one send exception: when a channel has a programmatic send path and Russell, having reviewed the exact draft this turn, gives an explicit per-message instruction to send it, you may send that reviewed text for him (today personal Gmail via the `gmail` skill's `gmail.js --send-draft`, personal Outlook via the `ms-graph` skill's `mail.js --send-draft`, and Slack via the `slack` skill's `slack.js --send`). Default, silence, and any autonomous run mean draft-only — never infer a send. This exception is the same one an interactive **drainer worker session** uses (see `worker-core.md`); an **autonomous drain** (no live Russell present, e.g. an `auto-handle` item) has no one to give that instruction, so it stays draft-only unconditionally.
5. **Learn** — after he sends or discards it, do the drafting session's part of `learn-from-send.md`: read what he actually sent, record the outcome, and run the spawn command it prints.
   The learning session it spawns does the rest, filling any gap in the rules and sharpening the send-confidence estimate in one PR.

A draft that reaches Russell should already read as his, because you verified it against the specific rules — not because you intended to.
