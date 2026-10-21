# Drainer: the 48-hour acknowledgement sweep

## Goal

A real person who messaged Russell and has waited 48 hours with no word gets a short acknowledgement.
The real work keeps its place in the queue and gets handled in its normal order.
Bots and automated mail never get one.

The sweep is one more pass inside the existing poller cycle, plus one end-of-day review where Russell approves the batch.
Nothing goes out without his say-so on that batch.

## Eligibility

An item is due an ack when every condition below holds.
The poller checks all of them every cycle; the end-of-day review re-checks them right before each send.

- **Still waiting on Russell.**
  Either a worker session is open on it and idle (parked for him, not busy), or the item has a needs-you verdict but hasn't been dispatched yet, held back by the worker buffer or the hold-by-correspondent rule.
- **Triaged needs-you.**
  A needs-you verdict already means a reply is expected, so there is no second "is there a reply to make" judgment.
- **From a real person.**
  See "The real-person test" below.
- **At least 48 hours old.**
  The clock starts when the message arrived; for a Slack span it starts at the oldest unread message in the span.
- **No reply from Russell since it arrived.**
  The poller reads the live thread, sent mail for email and conversation history for Slack, because he may have answered from his phone.
  Any message from Russell on the thread after the clock started makes the item ineligible.
- **Not already acked.**
  The thread has no entry in the ack ledger, or its entry predates a real reply Russell has since sent (see "The ack ledger").
- **No real reply already staged.**
  If a worker already staged Russell's real reply as a draft on that thread, the item is listed in the review as "real reply ready, send that instead" and gets no ack.
  This also protects the staged draft: Gmail keeps one draft per thread and replaces it on the next stage, and Slack refuses a second draft on a thread.
- **Not flagged by the security screen.**
  A flagged item never gets an autonomous engagement of its content.
- **Reachable on the channel it arrived on.**
  A direct email, a Slack DM, a group DM that names Russell, a channel @-mention, or a subscribed thread.
  A relay (a notification mail fronting someone else's message, such as a prison-mail or social-network notice) is skipped, since a reply goes to the relay's no-reply box rather than the person.
  A self-email is skipped.

Deeply personal messages (grief, a friend in a hard spot, anything where the right words depend on shared history) are not acked.
The drafting step in the end-of-day session makes that call, using the same test as worker-core step 4's "deeply personal" rule, and lists them under "yours to write" instead of drafting.

Sources in scope are the email providers that can send (outlook-graph for personal Outlook, gmail for the ISC account) and Slack.
Teams, Trello, Zoom action items, and physical tasks are out of scope: Trello and physical tasks are Russell's own queue, and Teams and Zoom have no programmatic send path in the drainer.

## The real-person test

Triage today records a bucket, a kind (reply, work, work-then-reply, phishing), and a complexity; no field records whether the sender is a person.
The `correspondent` field on captured items is the hold-by-correspondent identity, and it resolves for an automated sender as readily as a human one, so it can't serve as this test.

The plan adds one field to the existing per-item triage verdict: `sender: person | automated`.
It rides on the triage call already made for every item, so it costs no new AI call, and the verdict cache carries it for held items the same way it carries the bucket.
A real person writing through an automated envelope is still `automated` here, because the reply would reach the relay instead of them.

Deterministic vetoes sit on top of the model's call, so a confident wrong verdict still can't ack a bot:

- a no-reply or bulk sender address, or a list-unsubscribe header on the message;
- a Slack message from a bot user or an app integration;
- a relay sender recognized by the existing relay registry;
- a self-email.

## The 48-hour clock and when the sweep runs

The sweep runs at the end of each poller cycle, after dispatch, so it sees the same live-session scan and verdicts dispatch just used.
It does only cheap work for items under 48 hours: comparing an arrival time to now.
The live-thread reply check runs only for items past 48 hours that pass every other test, and its result is remembered for a few hours so a backlog of old items doesn't re-query every thread every five minutes.

Its output is a due list in the drainer runtime state: one entry per due item with the source, the thread, the sender, the link, how long it has waited, and whether the item is dispatched or undispatched.
The sweep sends nothing and drafts nothing; drafting happens at review time, against the thread as it stands then.

## The ack ledger

The ledger lives in the drainer runtime state beside `seen.json`.
Each entry is keyed by source plus conversation (the email thread, the Slack DM or channel plus thread), and records the item it was raised for, when the ack went out, and a reference to the exact sent message.
Keying on the conversation rather than the item id matters because a new Slack message, or a follow-up email in the same thread, produces a new item id for the same conversation.

The ledger holds three outcomes:

- **Sent** - the ack went out; the sweep skips this conversation.
- **Skipped** - Russell chose not to ack it in the review; the sweep never proposes it again.
- **Superseded** - Russell has since sent a real reply on the thread, so a later wait starts fresh.
  If the person writes again and that new message waits 48 hours, it can be acked once more.

So a conversation gets at most one ack per stretch of waiting, and a skipped one stays skipped.

## How items stay in the queue in their normal order

The ack is a side action that never clears, moves, or re-marks the item.
What tracks the item depends on whether it's dispatched:

- **Dispatched email and Slack items** were already cleared at spawn (email archived, Slack marked read), so the open worker session is the tracker.
  Sending the ack leaves that session open and untouched.
- **Undispatched items** are tracked by the source itself: the email still sits in the inbox and the Slack conversation is still unread.
  Replying in an email thread leaves the inbound message in the inbox, so it dispatches in its normal turn.
  For Slack, whether posting a message advances Russell's read cursor through the API is unverified (see open question 1).
  If it does, the cursor is put back to just before the unread span right after the send, so the conversation stays unread and dispatches in its normal turn.

No Trello card, no re-marking unread for email, no change to seen-state.
The poller's reconcile reads only whether the message is still in the inbox and whether a live worker is on it, so an ack changes nothing it looks at.
The hold-by-correspondent rule is likewise unaffected.

## The worker-side fix

worker-core §2 closes an item when Russell's most recent message on the thread is already a reply to the sender.
Once an ack goes out, the ack is that most recent message, so a worker would wrongly close the item as answered.

The fix: a worker checks the ack ledger for its conversation before applying that rule.
A message the ledger lists as an ack does not count as Russell having replied, and the thread still owes the real reply.
The one exception is an item whose whole need was an acknowledgement (a thanks, a "sounds good"); there the ack already discharged it and the worker closes it as done.
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
It sits beside the digest rather than inside it because the digest's hard rules are "never send" and "dispatch execution work, don't run it here", and the ack batch is exactly a send.
A separate lean session keeps the digest's rules intact and loads the writing stack only on the days there are acks to write.

The session:

1. Re-runs eligibility for every due item and drops any that stopped qualifying since the sweep.
2. Drafts each ack in Russell's voice through the document-authoring flow, reading the thread so the opening engages what the person actually said.
   The shape: a short positive line that engages the content ("Sounds good", "I see what you're saying", "Thanks for sending this"), then a light "I'll take a look at this" line.
   No date and no commitment.
   Deeply personal items are set aside here, per Eligibility.
3. Runs the cold writing review, minting a receipt and a send-confidence score for each body (see Graduation).
4. Presents one list: for each ack, who, which source, how long they've waited, the thread link, and the draft text.
   Then the "yours to write" items and the "real reply ready" items, each with its link.
5. Waits for Russell: "send all", or "send all except 3 and 5", or an edit to one draft.
6. For each approved ack, re-checks eligibility once more (he may have answered while reading), sends it, and writes the ledger entry with the sent message reference.
   Skipped acks are written to the ledger as skipped.
7. Records each draft's send-confidence outcome, then closes itself.

Approval is per batch, and the batch is the exact list he read in that turn, so the global "every message to another person waits for Russell" rule holds unchanged.

## Sending mechanics per source

Each send goes through the source's existing human-in-the-loop send path, the one its skill already gates on a review receipt:

- **Personal Outlook** stages the ack with ms-graph's reply-all draft and sends it with `--send-draft` on that draft id.
- **ISC Gmail** stages with the gmail skill's `--reply`, which appends the Executive Director signature, and sends with `--send-draft` on the printed draft id.
- **Slack** posts the reviewed body with `slack.js --send` into the same conversation, inside the thread for a thread item, with no staged draft in between.

The reply-to-group question for email is open question 2.

## Graduation to auto-approve

The scoring method Russell has in mind is **send-confidence**, in the document-authoring plugin.
Every outward draft minted through the review gate gets a composite score from five factors (rule coverage, content latitude, fact support, stakes, familiarity), and the send outcome is recorded as sent as-is, edited, or discarded.
Today it is Stage 1, measure only: no draft is gated on its score and no score is shown per draft.

Acks are a good first class to graduate, because they sit at the low end of every risk factor: one positive line plus a fixed close is low latitude, they make no factual claims or commitments, and an acknowledgement is the "1 - routine" anchor on stakes.

The path:

1. **Measure from day one.**
   Every ack is minted with a score context that marks it as an ack, so the log can separate ack outcomes from everything else.
   A skip in the review is recorded as discarded and moot (the decision was about the recipient, not the text), so it doesn't count against the draft quality.
2. **Read the track record.**
   The weekly rollup reports ack outcomes on their own line: how many went out as-is, how many were edited, and the scores of the edited ones.
3. **Propose a threshold.**
   Once acks show a sustained run of sent-as-is outcomes, the rollup proposes a cutoff: an ack whose composite clears it, to a recipient Russell has written to before, goes out without waiting for the batch.
   Anything under the cutoff, and every first-time recipient, stays in the batch.
4. **Russell turns it on.**
   Graduation is a deliberate change he makes himself, an explicit carve-out for this class of message in his global rule, and the end-of-day list keeps a "sent automatically today" section so every auto-sent ack is still visible.

What send-confidence lacks for this today is Stage 2 itself: a gate that reads a score and allows a send.
Building that gate is a separate change in document-authoring, after acks have a record to justify it.

## Edge cases

- **Russell replied from his phone.**
  The live-thread check at sweep time and again right before each send catches it, and the ack is dropped.
- **Thread already acked.**
  The ledger entry suppresses it, until a real reply from Russell supersedes the entry.
- **Item finished between the sweep and the batch.**
  The re-check at review start and again before each send finds the worker session closed or the item no longer waiting, and drops the ack.
- **Multi-message Slack spans.**
  The span is one conversation and gets one ack, and the clock starts at its oldest unread message.
  The draft engages the span as a whole; with several distinct asks it acknowledges them together without answering any.
- **The same person waiting on several threads.**
  One ack per person per batch, on their most recent waiting thread.
  Their other waiting threads are written to the ledger as covered, so they aren't proposed tomorrow.
- **An item held behind the same person's earlier open item.**
  It's undispatched, so it qualifies the same as any other undispatched item; the person rule above keeps them from getting two acks at once.
- **A worker session busy with Russell when the review runs.**
  A busy session isn't waiting on him, so the item drops out; he's working it now.
- **The verdict cache expired for a held item.**
  The poller re-judges it every cycle it's held, so the sweep always sees a current bucket; an item whose verdict is unavailable this cycle is skipped, the same fail-closed rule dispatch uses.
- **The review session dies mid-batch.**
  The ledger is written per send, so a rerun skips what already went out and offers the rest.

## Open questions

1. **Does a Slack send through the API advance Russell's read cursor?**
   If it does, an undispatched Slack item would read as handled and never dispatch.
   The plan's answer is to restore the cursor after the send; the first build step is to test this against a real DM before relying on it.
2. **Who does an email ack go to on a group thread?**
   The drainer's email rule is always reply-all.
   An ack to everyone on a group thread is noise for the others, so the recommendation is reply-all on a two-person thread and reply to the sender alone otherwise.
3. **Is 48 hours wall-clock or business hours?**
   Wall-clock is simpler and matches "two days with no word"; a message from Friday morning would be due in Sunday's batch.
   The recommendation is wall-clock, with the batch itself only on days the digest runs.
4. **Does the writing-review gate accept one review across a batch of bodies?**
   The plan assumes one cold review covers the whole batch and mints a receipt per body.
   If the gate needs one review per body, the review session runs them in parallel subagents instead.
5. **Should an item whose only need is an acknowledgement be acked at all, or dispatched sooner?**
   An ack fully answers it, so the batch effectively becomes its reply.
   The recommendation is to let it ride as an ack and have the worker close it on sight, per the worker-side fix.
