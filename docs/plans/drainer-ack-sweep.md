# Drainer: the 48-hour acknowledgement sweep

## Goal

A real person who messaged Russell and has waited two business days with no word gets a short acknowledgement.
The real work keeps its place in the queue and gets handled in its normal order.
Bots and automated mail never get one.

The sweep is one more pass inside the existing poller cycle, plus one end-of-day review where Russell approves the batch.
Nothing goes out without his say-so on that batch.

## Eligibility

An item is due an ack when every condition below holds.
The poller checks all of them every cycle; the end-of-day review re-checks them right before each send.

- **Still waiting in the queue.**
  The item has a needs-you verdict but hasn't been dispatched yet, held back by the worker buffer or the hold-by-correspondent rule.
  An item with a worker session already open on it gets no ack: the real reply is in progress and lands soon, whether or not the worker has finished by the 48-hour mark.
- **Triaged needs-you.**
  A needs-you verdict already means a reply is expected, so there is no second "is there a reply to make" judgment.
- **From a real person.**
  See "The real-person test" below.
- **At least 48 business hours old.**
  The clock counts weekdays only, so a message that arrived Friday morning is due Tuesday morning.
  For a Slack span it starts at the oldest unread message in the span.
- **No reply from Russell since it arrived.**
  The poller reads the live thread, sent mail for email and conversation history for Slack, because he may have answered from his phone.
  Any message from Russell on the thread after the clock started makes the item ineligible.
- **Not already acked.**
  The thread has no entry in the ack ledger, or its entry predates a real reply Russell has since sent (see "The ack ledger").
- **Not flagged by the security screen.**
  A flagged item never gets an autonomous engagement of its content.
- **Not a self-email.**

Deeply personal messages (grief, a friend in a hard spot, anything where the right words depend on shared history) are not acked.
The drafting step in the end-of-day session makes that call, using the same test as worker-core step 4's "deeply personal" rule, and lists them under "yours to write" instead of drafting.

Sources in scope are the email providers (outlook-graph for personal Outlook, gmail for the ISC account) and Slack, including mail a relay delivers on a real person's behalf.
Trello and physical tasks are out of scope, since they are Russell's own queue.

## The real-person test

Triage today records a bucket, a kind (reply, work, work-then-reply, phishing), and a complexity; no field records whether the sender is a person.
The `correspondent` field on captured items is the hold-by-correspondent identity, and it resolves for an automated sender as readily as a human one, so it can't serve as this test.

The plan adds one field to the existing per-item triage verdict: `sender: person | automated`.
It rides on the triage call already made for every item, so it costs no new AI call, and the verdict cache carries it for held items the same way it carries the bucket.
A relay is a pass-through here, the same way triage already treats it: a real person's message delivered through a notification wrapper is `person`, judged by who wrote the words, not by the envelope.

Deterministic vetoes sit on top of the model's call, so a confident wrong verdict still can't ack a bot:

- a no-reply or bulk sender address with no relayed person behind it, or a list-unsubscribe header on the message;
- a Slack message from a bot user or an app integration;
- a self-email.

## The 48-hour clock and when the sweep runs

The sweep runs at the end of each poller cycle, after dispatch, so it sees the same live-session scan and verdicts dispatch just used.
It does only cheap work for items under the threshold: comparing an arrival time to now, in business hours.
The live-thread reply check runs only for items past the threshold that pass every other test, and its result is remembered for a few hours so a backlog of old items doesn't re-query every thread every five minutes.

Its output is a due list in the drainer runtime state: one entry per due item with the source, the thread, the sender, the link, and how long it has waited.
The sweep sends nothing and drafts nothing; drafting happens at review time, against the thread as it stands then.

## The ack ledger

The ledger lives inside `seen.json`, under its own reserved key beside the per-source id maps, so one file holds everything the poller knows about an item's handling.
Each entry is keyed by source plus conversation (the email thread, the Slack DM or channel plus thread), and records the item it was raised for, when the ack went out, and a reference to the exact sent message.
Keying on the conversation rather than the item id matters because a new Slack message, or a follow-up email in the same thread, produces a new item id for the same conversation.

The ledger holds four outcomes:

- **Sent** - the ack went out and the real reply is still owed; the sweep skips this conversation.
- **Completed** - the ack was the whole reply the item needed (see "Items an ack fully answers").
- **Skipped** - Russell chose not to ack it in the review; the sweep never proposes it again.
- **Superseded** - Russell has since sent a real reply on the thread, so a later wait starts fresh.
  If the person writes again and that new message waits two business days, it can be acked once more.

So a conversation gets at most one ack per stretch of waiting, and a skipped one stays skipped.
`seen.json` reads as empty when it's missing or corrupt, which would also empty the ledger; the live-thread check still blocks a second ack in that case, because the first ack is a message from Russell sent after the item arrived.

## How items stay in the queue in their normal order

The ack is a side action that never clears, moves, or re-marks the item.
Every acked item is undispatched, so the source itself is what tracks it: the email still sits in the inbox and the Slack conversation is still unread.
Replying in an email thread leaves the inbound message in the inbox, so it dispatches in its normal turn.

For Slack, posting a message through the API might advance Russell's read cursor in that conversation, which would make the unread item read as handled and keep it from ever dispatching.
The send step guards against this directly: it reads the conversation's read cursor before the send and again after, and when the cursor moved it puts it back to where it was, so the conversation stays unread either way.
The guard works whichever way Slack behaves, so the design doesn't depend on knowing in advance.
The build still confirms the behavior once with a test in Russell's own self-DM, which reaches no one and can be deleted afterward: post a message, read the cursor, and see whether it moved.

No Trello card, no re-marking unread for email, no change to the item's seen-state entry.
The poller's reconcile reads only whether the message is still in the inbox and whether a live worker is on it, so an ack changes nothing it looks at.
The hold-by-correspondent rule is likewise unaffected.

## Items an ack fully answers

Some needs-you items need nothing beyond an acknowledgement: a thanks, a "sounds good", a closing note from someone who addressed Russell personally.
For these the ack is the reply, so the review finishes the item right there.
After the send it clears the item through its provider's CLEAR (archive or mark read), records it seen so it never dispatches, writes the ledger entry as completed, and lists it in the review's closing summary as done.
The drafting step makes this call per item when it reads the thread, and shows it beside the draft ("this ack finishes it") so Russell sees which items close on send.

## The worker-side fix

worker-core §2 closes an item when Russell's most recent message on the thread is already a reply to the sender.
Once an ack goes out, the ack is that most recent message, so a worker later dispatched onto the item would wrongly close it as answered.

The fix: a worker checks the ack ledger for its conversation before applying that rule.
A message the ledger lists as a sent ack does not count as Russell having replied, and the thread still owes the real reply.
When the worker drafts the real reply, it threads off the newest real message as usual, which may be the ack, and doesn't repeat the ack's thanks.

The same assumption appears in these places, and each gets the same ledger check or a pointer to worker-core's rule:

- **`providers/email-base.md` SITUATIONAL-CHECK step 2** states the same "most recent message is a reply, so the item is done" rule for every email provider.
- **`providers/slack-provider.md` SITUATIONAL-CHECK** asks the worker to confirm there's not already a reply.
- **worker-core §6's quiet resolve** for "a thread where they replied and the user already answered".
- **`engine/digest-core.md` step 2** treats an fyi item as already handled when Russell replied after it.
  Acks never go to fyi items, but a worker can re-triage an acked item to fyi, so the digest gets the same check.

Checked and unaffected: the poller's reconcile and the hold-by-correspondent rule (neither reads replies), and the Slack known-contact override (an ack makes a conversation a known contact, which only protects it from being bucketed junk).

## The end-of-day review

The review is its own background session, launched by the same daily scheduled task that launches the digest, and only when the due list is non-empty.
It stays out of the digest to keep the digest from growing: the digest already holds the whole day's fyi, junk, and auto-handled queue, and the acks would add the document-authoring writing stack plus a send step on top of it.
A separate session loads that stack only on the days there are acks to write, and leaves the digest's "never send" rule as it stands.

The session:

1. Re-runs eligibility for every due item and drops any that stopped qualifying since the sweep.
2. Drafts each ack in Russell's voice through the document-authoring flow, reading the thread so the opening engages what the person actually said.
   The shape: a short positive line that engages the content ("Sounds good", "I see what you're saying", "Thanks for sending this"), then a light "I'll take a look at this" line.
   No date and no commitment.
   Deeply personal items are set aside here, per Eligibility.
3. Runs the cold writing review, minting a receipt and a send-confidence score for each body (see Graduation).
4. Presents one list.
   Each entry pairs what the person said with the ack that answers it, so Russell can judge whether the ack fits: who, which source, how long they've waited, the thread link, a quote or tight paraphrase of their message (every ask in it, for a multi-message span), then the draft text, marked when the ack finishes the item.
   Then the "yours to write" items and the "send by hand" items, each with its link.
5. Waits for Russell: "send all", or "send all except 3 and 5", or an edit to one draft.
6. For each approved ack, re-checks eligibility once more (he may have answered while reading), sends it, and writes the ledger entry with the sent message reference.
   Skipped acks are written to the ledger as skipped.
7. Records each draft's send-confidence outcome, then closes itself.

Approval is per batch, and the batch is the exact list he read in that turn, so the global "every message to another person waits for Russell" rule holds unchanged.

## Sending mechanics per source

Each send goes through the source's existing human-in-the-loop send path, the one its skill already gates on a review receipt:

- **Personal Outlook** stages the ack with ms-graph's reply-all draft and sends it with `--send-draft` on that draft id.
- **ISC Gmail** stages with the gmail skill's `--reply`, which appends the Executive Director signature, and sends with `--send-draft` on the printed draft id.
- **Slack** posts the reviewed body with `slack.js --send` into the same conversation, inside the thread for a thread item, with no staged draft in between, then runs the read-cursor guard.

Email acks reply-all, the same as every drainer email reply, so everyone on a group thread sees that Russell picked it up.

**A relayed message with no programmatic send path is sent by hand.**
The review lists it under "send by hand" with what the person said and a link straight to the conversation, such as the LinkedIn thread link carried in the notification email.
LinkedIn entries carry no drafted text, per Russell's standing LinkedIn rule; he writes those himself.
Russell marks each one done or skipped in his reply, and the session writes the ledger entry the same way it does for a programmatic send.

## Graduation to auto-approve

The scoring method Russell has in mind is **send-confidence**, in the document-authoring plugin.
Every outward draft minted through the review gate gets a composite score from five factors (rule coverage, content latitude, fact support, stakes, familiarity), and the send outcome is recorded as sent as-is, edited, or discarded.
Today it is Stage 1, measure only: no draft is gated on its score and no score is shown per draft.

Acks graduate through that general mechanism with no ack-specific tracking.
An ack scores high on its own merits: one positive line plus a fixed close is low content latitude, it makes no factual claims or commitments, and an acknowledgement is the "1 - routine" anchor on stakes.
So when send-confidence reaches its Stage 2, a gate that lets a high-scoring draft go out without waiting, acks clear it the same way any other low-risk message does.

Two things keep the ack data clean in the meantime:

- every ack is minted with a score context and its outcome recorded, the same as any outward draft;
- a skip in the review is recorded as discarded and moot, since the decision was about the recipient rather than the text, so it doesn't count against draft quality.

When Stage 2 lands, the end-of-day list keeps a "sent automatically today" section so every auto-sent ack is still visible.
Turning it on stays a deliberate change Russell makes to his own global rule.

## Edge cases

- **Russell replied from his phone.**
  The live-thread check at sweep time and again right before each send catches it, and the ack is dropped.
- **Thread already acked.**
  The ledger entry suppresses it, until a real reply from Russell supersedes the entry.
- **Item dispatched or finished between the sweep and the batch.**
  The re-check at review start and again before each send finds a worker session open on it, or the item no longer waiting, and drops the ack.
- **Multi-message Slack spans.**
  The span is one conversation and gets one ack, and the clock starts at its oldest unread message.
  The review shows every ask in the span beside the draft, and the draft acknowledges them together without answering any.
- **The same person waiting on several threads.**
  One ack per person per batch, on their most recent waiting thread.
  Their other waiting threads are written to the ledger as covered, so they aren't proposed tomorrow.
- **An item held behind the same person's earlier open item.**
  The earlier item has a worker on it, so that person's real reply is already in progress; the held item waits for it and gets no ack.
- **A draft already staged on the thread.**
  The drafting step checks for an existing draft before staging, since Gmail replaces a thread's draft on the next stage and Slack refuses a second one, and lists the item for Russell rather than overwriting it.
- **The verdict cache expired for a held item.**
  The poller re-judges it every cycle it's held, so the sweep always sees a current bucket; an item whose verdict is unavailable this cycle is skipped, the same fail-closed rule dispatch uses.
- **The review session dies mid-batch.**
  The ledger is written per send, so a rerun skips what already went out and offers the rest.

## Open questions

1. **Do holidays pause the business-day clock?**
   The plan counts weekends only.
   Holidays would need a calendar source the drainer doesn't read today.
