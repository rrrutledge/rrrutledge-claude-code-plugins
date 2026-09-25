# email-base - shared logic for all email providers

All email providers (gmail, outlook-graph, outlook-rest) inherit these rules.
Each provider's own file covers only the provider-specific bits: CONFIG, AUTH-GLANCE, SITUATIONAL-CHECK mechanism, CLEAR, JUNK-LEARNING step 3, and DRAFT-MODE CLI commands.
Everything here applies as-is.

---

## CAPTURE shape (shared convention)

Every email provider writes two files per dispatched item:
- `items/<id>.email.md` - header block (From, Received, Link, MessageId) + full body text.
- `items/<id>.json` - `{ "id","source","triage","kind","from","subject","received","snippet","url",` `"messageId","correspondent","emailFile","ts" }`.

`messageId` is the **load-bearing field** - the worker needs it for the reply draft and for CLEAR.
`correspondent` is written for the poller's dispatch step, not the worker (which never reads it); its purpose lives in `poller-core.md`.
Its exact format varies by provider (RFC822 Message-ID for Gmail; opaque API id for Graph/REST), but the field name is always `messageId` and it is always present.

---

## SITUATIONAL-CHECK (do this BEFORE drafting any reply)

The captured item is the inbound message at capture time; the conversation may have moved since.

1. **Read the full thread** using your provider's thread-fetch mechanism (see your provider file).
   Cover both directions - messages FROM the contact AND the user's own sent replies.
   Paginate fully; don't stop at the first page.
2. **If the user's most recent message on the thread is already a reply to this sender** → the item is done.
   Close it without a new draft.
3. **Check for an existing draft** using your provider's draft-list command, to avoid staging a duplicate if a prior session already started one.

---

## CLEAR TIMING - the item's scope is knowable instantly (see worker-core.md §2d)

Every email provider keys **one item per message**, so this item's full scope is known the moment SITUATIONAL-CHECK above (and §2c's re-triage check) confirm it's a genuine, still-open needs-you item - there's no multi-ask span to read first the way a chat source has.
That means §2d's clear-now bar is met right there: archive it before starting any of step 3's work, not after.

---

## DRAFT-MODE (shared rules - CLI commands are in your provider file)

### Voice gate (mandatory - follow the full loop, not just Read)

Invoke the `document-authoring` skill (call the Skill tool to load it) and follow its mandatory **Read → Write → Verify → Stage → Learn** drafting loop for this message - not just the Read step.
Verify in that loop dispatches an independent review before the draft is staged, so nothing extra is needed here to get that check; skipping straight to composing from the Read section alone, without carrying the draft through Verify, is what lets a rule slip through uncaught.
The stage command enforces this: your provider's `--reply` / `--draft-new` / `create-reply` refuses to stage the body file until Verify has minted its review receipt (see `writing-review`'s **The stage gate**), so a skipped review surfaces as a block naming the fix, not a silent leak to Russell.

### After composing

- The voice loop still applies: after the user sends, diff sent-vs-draft and append a concrete lesson to the document-authoring skill's Voice learning loop.
- Write the body as an HTML file, then create the draft via your provider's CLI - **never sent** from inside the worker.
  Show the draft text in your reply so the user can review it.
- The user edits + sends themselves.
  Never auto-send; sending is gated behind an explicit OK in the top-level interactive session.

### Reply vs fresh note

Pick the mode by who the message goes to:

- **Reply-all on the thread** (responding to inbound mail): use your provider's `--reply` command.
  Always reply-all (not plain reply) to preserve every original To+CC recipient.
  Include the quoted original below the new text.
  Thread off the **most recent message in the thread** - pass *its* `messageId`, even if that last message is one the user sent - so the reply lands where the conversation actually stands.
- **Fresh 1:1 (or small-group) note** (e.g. an outreach nudge to a single contact - do NOT reply-all a group thread to single someone out): use your provider's `--draft-new` / create-draft command with explicit To/Subject/CC.

---

## JUNK-LEARNING (shared priority order)

Stop this junk arriving again - propose, never apply without the user's OK.
**Reach for a rule first.**
A rule is an asset that pays off against every future sender; an unsubscribe is a one-time transaction with a single company that never builds the rule set.
So the order is rule → source-app → unsubscribe:

1. **Build a type-level rule - the first reach.**
   If this mail is a *class* you can capture with a general pattern, make the rule - it then catches this kind of mail from *every* sender, now and in the future, which is how the rule set compounds.
   Defer to the **`mail-filters`** skill for the full phrase-selection method - name the type, prefer the subject and escalate to the body when it can't deliver, run the two-sided test, tighten until safe - generalizing from this one sample to a type-level phrase broad enough to recur across senders, strict enough to never bury wanted mail, fenced by the sender-domain whitelist.
   Match the shape to the mailbox - **both Outlook and Gmail can match on the body as well as the subject**; the difference is the fencing convention, not the capability (see your provider file).
   Before creating or appending anything, run `mail-filters`' show-literal-rule gate ("Wiring the drainer" step 3): show Russell the exact phrase(s), which bucket they land in, and the action, and create only on his explicit OK of that shown text - a digest-level go-ahead approves building a rule for the type, not the rule's literal text.
2. **Turn it off at the source app.**
   When the sender is an app whose notification settings the user controls (GitHub notification settings, LinkedIn email preferences, …), tuning it off there stops the mail at its source.
3. **Unsubscribe - the fallback.**
   Only when the mail is genuinely idiosyncratic to one sender and no type-level pattern - subject *or* body - fits.
   Unsubscribe is one-company-only, so it's the last reach, not the first.
   If unsubscribe is unavailable too (dead link, no `List-Unsubscribe` header) and the sender emails rarely, just archive the message and wait to see if it recurs.

**Noise at a shared mailbox, or a list the user doesn't own, never gets an unsubscribe.**
When the recurring noise arrives at an address other people also read (a shared `info@`/team alias) or on a mailing list the user isn't the owner of, unsubscribing would cut off mail others may want, and it isn't the user's list to leave.
The right stop is a **his-side skip-the-inbox rule** (a filter that skips the inbox or applies a label) so the mail still flows to the mailbox but the user never sees it - the type-level rule of step 1, applied for reach-me-only rather than to stop delivery.
Reserve an actual unsubscribe for mail that is genuinely only to the user and that no one else needs.
Which of the user's addresses are shared is a fact about them - their `context.md` names those.
