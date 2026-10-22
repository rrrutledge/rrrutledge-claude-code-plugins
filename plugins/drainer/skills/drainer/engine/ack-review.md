# drainer ack-review - the end-of-day acknowledgement batch (interactive, approved per batch)

A real person who messaged Russell and has waited 48 business hours with no word gets a short acknowledgement, while the real work keeps its place in the queue.
The digest's daily task runs the once-a-day sweep that lists the conversations due an ack, and launches this session when that list is non-empty.
This session drafts the acks, puts them in front of Russell as one batch, and sends exactly what he approves.

Every deterministic step, the sweep included, lives in the ack script your seed names (`ack_sweep.py`, always run with `--repo <repo>`), whose module docstring lists them.
Each sent, skipped, or covered ack is recorded in the ack ledger, under the reserved `_acks` key in `seen.json`, which every per-source reader of `seen.json` refuses.
This doc holds the judgment and the order of the calls.

## 1. Re-check the batch

Run `python <ack script> prepare --repo <repo>`.
It re-checks every due conversation against the live source and prints one row per conversation: `status` `ready` with what the person said (`message`), the link, how long they've waited, the send path, whether a draft is already staged (`hasDraft`), and any other waiting threads from the same person it `covers`; or `status` `dropped` with the reason it stopped qualifying.
Carry the dropped ones as a one-line count; the rest are your batch.

## 2. Sort and draft

Read each ready row's `message`, and open the thread at its link when the message leans on earlier context.
Then put each row in one of these groups:

- **Yours to write** - a deeply personal message, by worker-core §4's "Deeply personal messages" test.
  Draft nothing for it.
- **Draft already staged** (`hasDraft` true) - something is already in progress on the thread; list it with its link and draft nothing, so nothing overwrites it.
- **Send by hand** (`sendPath` `by-hand`) - a relayed message with no programmatic reply path.
  When the row is LinkedIn (`linkedin` true), draft nothing: Russell writes those himself, and the row's link opens the conversation.
  Otherwise draft the ack for him to paste.
- **A reply that handles the item** - the message asks for nothing beyond a short reply, so that reply is the item's whole handling rather than an ack.
- **An ack** - everything else.

Draft each body through the `document-authoring` writing flow (load `writing-flow` first), in Russell's voice, one file per row under the repo's `.tmp/` (`.tmp/ack-<n>.md`).
An ack's shape: one short positive line that responds to the substance of what the person said, then one light line saying Russell will take a look.
It carries no date and no commitment, and answers none of their asks; for a multi-message span it acknowledges every ask together.
A Slack body is Slack mrkdwn; an email body is Markdown, and the ISC Gmail send appends the Executive Director signature itself.

Run the cold `writing-review` on every body, then mint each with a send-confidence score context per `send-confidence.md`, passing the row's `channel` as `--channel`.

## 3. Present one list

One numbered list, each entry pairing what the person said with the text that answers it, so Russell can judge the fit:

- **Who, where, how long** - the sender, the source, how long they've waited, and the thread link.
- **What they said** - a quote or tight paraphrase, with every ask in it for a multi-message span, plus the other threads it covers.
- **The draft** - the body text, marked **closes the item on send** when it is a reply that handles the item.

After the list, the **yours to write**, **draft already staged**, and **send by hand** groups, each entry with its link (and its drafted text, for a by-hand row that has one).
Then stop and wait.

## 4. Act on Russell's answer

He answers for the batch you presented: which entries to send, any edit to a draft, and a done or skipped mark for each by-hand row.
Act on exactly the entries that answer names, and only after it:

- **An approved entry** - `python <ack script> send --repo <repo> --key <key> --body-file <body>`, adding `--handles` for a reply that handles the item.
  When Russell edited a draft, write his text to a new body file, run `writing-review` on it and mint it, then send that file with `--scored-body <the original body>` so the outcome records his edit.
- **An entry he left out** - `python <ack script> skip --repo <repo> --key <key> --body-file <body>` (omit `--body-file` when nothing was drafted).
- **A by-hand row marked done** - `python <ack script> hand-done --repo <repo> --key <key>`; one marked skipped takes `skip`.

`send` re-checks the entry right before it sends and exits 3 with the reason when it stopped qualifying; tell Russell which ones that was.
When a `send` or `skip` prints a learning-session spawn command in its `sendConfidence` output, run it exactly as printed and name the new session by its title.

## 5. Close

Report what went out, what was skipped, and anything `send` dropped, then close this session with the `session-mgr:close` skill.
