# outlook-graph-junk provider - personal Junk Email folder (Microsoft Graph API)

A provider for a **personal** Outlook.com mailbox's **Junk Email folder**, read entirely through the **Microsoft Graph API** via the **`ms-graph`** skill's `mail.js`.
The poller enumerates Junk, triages and security-screens each item, and un-junks misfiled mail (anything that's actually `fyi`, `auto-handle`, or `needs-you`) into the Inbox.
It never launches a worker for a Junk item: once the message is back in the Inbox, the `outlook-graph` provider enumerates it as an ordinary new message and its normal worker/digest flow takes over, so one email is never worked from two providers at once.
Genuinely junk mail found in Junk, and anything the security screen flagged, is silently recorded as seen and left in Junk (matches Russell's framing: "already in the right spot").

Implements `../engine/provider.md`; classify by `../engine/triage.md`. id prefix: `outlook-graph-junk-`; body file: `<id>.email.md`.

> **Sibling to `outlook-graph-provider.md`** - same mailbox, same `mail.js`, same message ids and capture shape.
> This file covers only the Junk-specific mechanics: RESCUE (the `--not-junk` action that un-junks + retrains), run by the poller in plain code.
> No worker ever holds a Junk item, so SITUATIONAL-CHECK, DRAFT-MODE, and JUNK-LEARNING are the Inbox provider's, applied after the rescue.

**Shared email rules:** See `email-base.md` for CAPTURE shape, SITUATIONAL-CHECK, DRAFT-MODE voice rules, and JUNK-LEARNING priority order.
This file covers only the Junk-specific bits.

## Config (in `.claude/drainer.local.md` → `providers.outlook-graph-junk`)

No config - you sign in once via `ms-graph`.
Credentials: same as `outlook-graph` (shared mailbox).

The `ms-graph` `mail.js` lives at `<ms-graph-skill>/scripts/mail.js` - run it with `node`.

## AUTH-GLANCE

**Same as `outlook-graph-provider.md` - same mailbox, same token cache.**
Run `node mail.js --list-unread --top=1`.
If it prints messages (or "No unread messages."), you're signed in.

## SITUATIONAL-CHECK, CAPTURE

**N/A - no worker holds a Junk item.**
The rescued message is captured and situationally checked by the `outlook-graph` provider, from the Inbox.
The message id is the same opaque Graph id in either folder, but the two providers keep separate seen-state, so the Inbox provider sees the rescued message as new.

## RESCUE

The poller runs `node mail.js --not-junk=<messageId>` in plain code, after triage and the security screen, for every Junk item that is not `junk` and not screen-flagged.
It un-junks a message by reporting it "not junk" to Microsoft's filter and moving it to **Inbox**.
This is a **single atomic step** that both rescues the misfiled message *and* retrains the junk filter so future mail from that sender is less likely to be misfiled.

A screen-flagged item stays in Junk: the report would teach Microsoft's filter to pass it.
The item is recorded seen only after the un-junk succeeds; a failure leaves it unrecorded, so the next cycle retries.

**Mechanism:** Uses the **beta** Graph action `POST /me/messages/{id}/reportMessage` with `IsMessageMoveRequested: true` and `ReportAction: "notJunk"`.
The old stable `markAsNotJunk` was retired in Dec 2025; Microsoft's replacement is this beta endpoint.
If the beta call fails (should be rare), `mail.js` falls back to a plain move-to-Inbox (`POST /me/messages/{id}/move`), so the message is still rescued even if filter retraining doesn't happen.

## JUNK-LEARNING, DRAFT-MODE

**N/A for this provider.**
Genuinely junk items are recorded seen with zero noise, so no worker or digest entry ever asks the user "how do we stop this?" because the item is already in the right place.
A rescued message is the Inbox provider's from then on, including any reply drafting (`outlook-graph-provider.md`, `email-base.md`).

**Why:** `outlook-graph-junk`'s whole point is to *ignore* genuine junk (leave it alone - it's correctly filed) while returning misfiled mail to the Inbox, without a second worker racing the Inbox provider's own.
