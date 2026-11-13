# outlook-graph provider - personal Outlook.com mail (Microsoft Graph API)

A provider for a **personal** Outlook.com mailbox (`outlook.live.com`) read and cleared entirely through the **Microsoft Graph API** - no browser.
All Graph calls go through the **`ms-graph`** skill's `mail.js` (don't reimplement Graph here); it owns auth, the token cache, and silent refresh.
Implements `../engine/provider.md`; classify by `../engine/triage.md`. id prefix: `outlook-graph-`; body file: `<id>.email.md`.

> Two-file provider: the **reading** mechanics (enumerate, stable id, capture-writing) live in the sibling **`outlook-graph-adapter.py`** that the poller drives.
> This doc is the **worker-facing** prose - AUTH-GLANCE, the captured item shape, CLEAR, JUNK-LEARNING, DRAFT-MODE.

> This is the Graph-transport counterpart to `outlook-rest-provider.md` (which reads whatever account is signed into Outlook web, e.g. a work mailbox).
> Use this one for a personal Microsoft account.

**Shared email rules:** See `email-base.md` for CAPTURE shape, SITUATIONAL-CHECK decision logic, DRAFT-MODE voice rules, and JUNK-LEARNING priority order.
This file covers only the Graph-specific mechanisms.

## Config (in `.claude/drainer.local.md` → `providers.outlook-graph`)
No config - you sign in once via `ms-graph`.
Credentials: `GRAPH_CLIENT_ID` / `GRAPH_CLIENT_SECRET` in the environment (used by `ms-graph`); the MSAL token cache is machine-local at `~/.claude/ms-graph/token-cache.json`.

The `ms-graph` `mail.js` lives at `<ms-graph-skill>/scripts/mail.js` - run it with `node`.

## AUTH-GLANCE
Run `node mail.js --list-unread --top=1`.
If it prints messages (or "No unread messages."), you're signed in.
If it errors with "Not signed in" or an auth error, do the `ms-graph` one-time sign-in (`node scripts/auth.js` via browser-chauffeur), then retry - never surface the token error to the user.

## SITUATIONAL-CHECK mechanism
The inbox is drained and emptied by the poller, so recently-handled messages live in **Archive** (where CLEAR moves them), not the inbox.
Search all three folders - inbox, Archive, Deleted Items - covering both directions and **paginating each fully** (older items cleared before this behavior changed may still sit in Deleted Items).
Use `node mail.js --search="<subject>"` - verify it covers Archive and Deleted Items and do not stop at the first page.

## RESOLVE-A-POINTER (hosted-PDF / body-link newsletter)
The shared open-the-pointer mechanic, used by both the worker and the daily digest, lives in `../engine/pointers.md`.
This section holds only the Graph commands for it.

**Decide which kind of "attachment" the newsletter has first, because the two resolve differently.**
A **true inline/MIME attachment** is a real file on the message - `node mail.js --get-attachments=<messageId>` downloads it, and that is the right tool for it.
A **hosted/reference "attachment"** is a link, not a file: a Finalsite "Attachments: X.pdf" line, or a Safe-Links link in the body pointing at a `.pdf`, whose bytes live on a CDN rather than on the message.
`--get-attachments` reports "No attachments" for the hosted kind, so route it straight to the HTML-body-and-fetch path below rather than reading that empty result as "nothing to read."

A Finalsite-style school newsletter is the hosted kind: it delivers its real content as a hosted link in the HTML body, which the plaintext `mail.js --show` strips.
Emit the raw HTML body with `node mail.js --show=<messageId> --html`, fetching by the captured message id, which stays valid after archiving (see the CLEAR section).
Scan that whole HTML for the hosted-PDF `<a href>` and fetch it per `../engine/pointers.md` (Safe Links decode, plain fetch first); for Graph mail the files usually sit on Google Cloud Storage or myschoolcdn.
When the newsletter's story is inline or on a "view in browser" page rather than a PDF, read that content straight from the HTML instead.
The browser-chauffeur fallback opens the message's `webLink` and downloads the file.

## CAPTURE
See `email-base.md` for the shared two-file shape.
Graph-specific: `messageId` is the opaque Graph message id.

## CLEAR
`node mail.js --delete=<messageId>` - moves the message to **Archive** (reversible; keeps it searchable later; narrate it).
Never a permanent purge.
The poller also runs this through the adapter's `clear`, at triage for fyi/junk and right after launch for needs-you (`../engine/poller-core.md` steps 5-6).

**The captured `messageId` stays valid after CLEAR moves the message.**
ms-graph requests immutable message ids, so the `messageId` captured at triage time (or in `items/<id>.json`) keeps resolving after the message is archived or moved - `--show`, `--get-attachments`, and `--reply` all work against that same captured id.
Since email items CLEAR before step 3's work per `worker-core.md` §2d, this matters on the normal path: draft the reply with `node mail.js --reply --message-id=<captured messageId>` directly, no re-lookup.
`node mail.js --search="<subject>"` (which covers Archive, per SITUATIONAL-CHECK above) is the fallback for a message you can't resolve by its captured id.

## JUNK-LEARNING (the first-reach rule - Outlook.com-specific)
The first-reach stop (per `email-base.md`'s rule-first order, including its show-literal-rule gate): an **Outlook.com inbox rule** - append the type phrase to the right consolidated bucket, keeping the sender-domain exclusion whitelist that fences every broad bucket; pin the phrase to a single sender only when it isn't distinctive enough to stand on its own.
Once Russell has OK'd the shown rule, create it via `ms-graph`'s `mail.js --append-rule`/`--create-rule`.

## REPORT-PHISHING
For a junk item triage marked `kind: phishing` (see `../engine/triage.md`), the stronger disposition: `node mail.js --report-phish=<messageId>` reports the message to Microsoft (retraining the filter) and moves it out of the inbox to **Junk Email**.
Reversible: the message stays recoverable from Junk.
Personal Outlook.com accepts a `junk` report (not `phishing`), so the command reports `junk` under the hood and, if even that is refused, falls back to a plain move to Junk so the message still leaves the inbox.
The daily digest runs this on Russell's approval in place of the ordinary CLEAR for a phishing item (see `../engine/digest-core.md` step 3).

## DRAFT-MODE CLI commands
Follow all voice and reply-vs-fresh rules in `email-base.md`, then use these Graph commands:

- **Reply-all on the thread:** `node mail.js --reply --message-id=<messageId> --body-file=<file>` - Graph's reply-all keeps the thread quote below your text automatically and preserves all To+CC recipients.
  Thread off the message `email-base.md` names.
- **Fresh note:** `node mail.js --draft-new --to="<addr>" --subject="<subj>" --body-file=<file> [--cc="<addrs>"]`
