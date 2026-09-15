# outlook-rest provider - Outlook mail via the Outlook REST API

A provider for an Outlook mailbox read and cleared through the **Outlook REST API** - no browser for reads.
All mail mechanics go through the **`ms-rest`** skill's `outlook-mail.js` (don't reimplement the API here); it owns the bearer token sniffed from the live Outlook web session, the paged `enumerate`, `get`, `delete`, and the `create-reply` / `create-draft` compose calls.
Implements `../engine/provider.md`; classify by `../engine/triage.md`. id prefix: `outlook-rest-`; body file: `<id>.email.md`.

> Two-file provider: the **reading** mechanics (enumerate, stable id, capture-writing) live in the sibling **`outlook-rest-adapter.py`** that the poller drives.
> This doc is the **worker-facing** prose - AUTH-GLANCE, the captured item shape, CLEAR, JUNK-LEARNING, DRAFT-MODE.

> This is the **REST-transport** counterpart to the Graph-transport `outlook-graph-provider.md`: same operations, the Outlook REST v2.0 transport with a session-sniffed token instead of ms-graph/MSAL.
> It reads whatever account is signed into Outlook web (so it suits a work mailbox that has no personal Graph app).

**Shared email rules:** See `email-base.md` for CAPTURE shape, SITUATIONAL-CHECK decision logic, DRAFT-MODE voice rules, and JUNK-LEARNING priority order.
This file covers only the REST-specific mechanisms.

## Config (in `.claude/drainer.local.md` → `providers.outlook-rest`)
No config - `outlook-rest: {}`.
Auth is a bearer token the `ms-rest` skill sniffs from the live Outlook web session (signed in as yourself; no tenant baked in) and caches at `~/.claude/drainer/.tmp/outlook-token.json`.
The one-time sniff needs `playwright` (resolved from the home repo's `node_modules`); once cached, reads run with no browser at all.

The `ms-rest` `outlook-mail.js` lives at `<ms-rest-skill>/outlook-mail.js` - run it with `node`.

## AUTH-GLANCE
Run `node <ms-rest>/outlook-mail.js token`.
`Token OK ✅` means a valid token is available (cached or freshly sniffed) and the channel is ready.
If it reports "No Mail.ReadWrite token sniffed", Outlook web needs a signed-in tab to sniff from: open `https://outlook.office.com/mail/` (or `https://outlook.cloud.microsoft/mail/`) in the browser-chauffeur browser, confirm signed in, and stop reading mail until the token sniffs clean.
Never surface a raw auth error to the user.

## SITUATIONAL-CHECK mechanism
The inbox is drained and emptied by the poller, so recently-handled messages live in **Archive** (where CLEAR moves them), not the inbox.
Search all three folders - inbox, Archive, Deleted Items - covering both directions and **paginating each fully** (follow `@odata.nextLink`; older items cleared before this behavior changed may still sit in Deleted Items).
Use the `ms-rest` skill's `outlook-core.js` `apiCall` helper to query `/me/mailfolders/<folder>/messages` filtered by `receivedDateTime ge <cutoff>`, ordered newest-first, matching on the contact's name/address and thread subject.

## CAPTURE
See `email-base.md` for the shared two-file shape.
REST-specific: `messageId` is the Outlook REST id (opaque API handle).
Both `id` (stable slug) and `messageId` (API handle) are persisted in the JSON.

## CLEAR
`node <ms-rest>/outlook-mail.js delete <messageId>` - moves the message to **Archive** (reversible; keeps it searchable later; narrate it).
Never a permanent purge.

## JUNK-LEARNING (step 3 - Outlook work mailbox-specific)
After exhausting unsubscribe and source-app options (see `email-base.md`): propose an **Outlook rule** (a sender/subject/body match that files or deletes the class going forward).
Prefer extending an existing rule bucket over a new standalone rule.
Describe the rule for the user to add via the Outlook Rules UI.

## DRAFT-MODE CLI commands
Follow all voice and reply-vs-fresh rules in `email-base.md`, then use these `ms-rest` REST commands:

- **Reply-all on the thread:** `node <ms-rest>/outlook-mail.js create-reply <messageId> --json <path>` - creates the reply draft in **Drafts** with the quoted original below the new text and all To+CC recipients preserved.
  Thread off the most recent message (see `email-base.md`).
  JSON: `{ "comment": "<p>HTML body</p>" }`.
- **Fresh note:** `node <ms-rest>/outlook-mail.js create-draft --json <path>` - JSON: `{ "subject", "body": "<p>HTML</p>", "to": ["addr"], "cc": [], "bodyType": "HTML" }`.
