# outlook-graph-junk provider - personal Junk Email folder (Microsoft Graph API)

A provider for a **personal** Outlook.com mailbox's **Junk Email folder**, read entirely through the **Microsoft Graph API** via the **`ms-graph`** skill's `mail.js`.
The poller enumerates Junk, triages each item, and surfaces misfiled mail (anything that's actually `fyi` or `needs-you`) into the normal worker/digest flow.
Genuinely junk mail found in Junk is silently recorded as seen (matches Russell's framing: "already in the right spot").

Implements `../engine/provider.md`; classify by `../engine/triage.md`. id prefix: `outlook-graph-junk-`; body file: `<id>.email.md`.

> **Sibling to `outlook-graph-provider.md`** - same mailbox, same `mail.js`, same message ids and capture shape.
> This file covers only the Junk-specific mechanics: CLEAR (the `--not-junk` action that un-junks + retrains), and notes on shared mechanics (AUTH-GLANCE, SITUATIONAL-CHECK, DRAFT-MODE all reuse the Inbox provider's docs - no need to restate).
> JUNK-LEARNING is N/A for items triaged `junk` from this provider, since they never reach a worker or the digest.

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

## SITUATIONAL-CHECK

**Same as `outlook-graph-provider.md`** - search all three folders (Inbox, Archive, Deleted Items) using `node mail.js --search="<subject>"`.
A captured message in Junk may have moved or been handled since capture (the user's own reply might already be sitting in Inbox, or a second message from the same sender arrived and landed in Junk).
Pull the full thread via search - don't stop at the first page.

## CAPTURE

**Same shape as `outlook-graph-provider.md`:** `items/<id>.email.md` (header + full body) and `items/<id>.json` (metadata record).
Graph-specific: `messageId` is the opaque Graph message id (works identically regardless of which folder the message sits in).

## CLEAR

`node mail.js --not-junk=<messageId>` - un-junks a message by reporting it "not junk" to Microsoft's filter and moving it to **Inbox**.
This is a **single atomic step** that both rescues the misfiled message *and* retrains the junk filter so future mail from that sender is less likely to be misfiled.

**Mechanism:** Uses the **beta** Graph action `POST /me/messages/{id}/reportMessage` with `IsMessageMoveRequested: true` and `ReportAction: "notJunk"`.
The old stable `markAsNotJunk` was retired in Dec 2025; Microsoft's replacement is this beta endpoint.
If the beta call fails (should be rare), `mail.js` falls back to a plain move-to-Inbox (`POST /me/messages/{id}/move`), so the message is still rescued even if filter retraining doesn't happen.

**Narrate:** Mention both the un-junking (moved to Inbox, no longer junk) and the filter retraining ("future messages from this sender are less likely to be misfiled").
On fallback, note that the message was un-junked but the filter wasn't retrained.

## JUNK-LEARNING

**N/A for this provider.**
The poller filters out items triaged `junk` from `outlook-graph-junk` **before** they reach the worker or the digest queue - they're silently recorded as seen with zero noise.
So there is no worker, no digest entry, and no worker asking the user "how do we stop this?" because the item is already in the right place.

**Why:** `outlook-graph-junk`'s whole point is to *ignore* genuine junk (leave it alone - it's correctly filed) while surfacing misfiled mail (which will be `fyi` or `needs-you`).
A genuinely junk item never reaches JUNK-LEARNING because it never reaches the user.

## DRAFT-MODE

**Same as `outlook-graph-provider.md`** - the voice rules and reply mechanics are identical, since this is the same mailbox.
Use the same `mail.js --reply` / `--draft-new` commands.
See `email-base.md` and `outlook-graph-provider.md` for the full DRAFT-MODE mechanics.
