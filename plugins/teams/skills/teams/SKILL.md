---
name: teams
description: Read Microsoft Teams chats and watched-team channels via the Teams internal REST services - fast, no DOM hydration. Use to list unread DMs / group chats / meeting chats / watched-team channels, or read a conversation's recent messages. Tokens are sniffed once from the live Teams web session (CDP). Mark-read and reply drafting are browser-driven (drainer worker + message-draft skill), not here.
---

# Teams - Read chats & watched-team channels via the internal REST services

Read a Teams account directly over the **Teams internal services that Teams web itself uses** (on the
`teams.cloud.microsoft` origin) - listing unread conversations and reading a chat's messages become
single ~1 s HTTP calls, with no 20–30 s browser hydrate and no opening each conversation. This is the
read transport for the drainer **teams** provider.

**Read-only here; clearing and drafting live elsewhere.** This skill enumerates unread conversations
and reads messages. Marking a conversation read is **browser-driven** (the drainer worker opens it in
Teams web via browser-chauffeur - the authoritative `isRead` flips only on a real open, not via any
replayable HTTP call). Composing a reply is the **`message-draft`** skill's `teams` mode (browser,
draft-only).

## Auth

`teams-chat.js` calls two Teams internal services, each with its own bearer token:

- **chatsvcagg** (`aud=chatsvcagg.teams.microsoft.com`) - the chat **aggregator**; source of truth for
  per-chat `isRead` / `isMuted`, member names, and last-message preview (DMs, group chats, meeting chats).
- **chatsvc / IC3** (`aud=ic3.teams.office.com`) - lists the watched team's channels and reads a
  conversation's messages.

Both tokens are **sniffed over CDP** (port 9222) from the live Teams web session and cached together at
`~/.claude/drainer/teams-ic3-token.json` with their real JWT expiries; the script auto-re-sniffs on
expiry or any 401. The one-time sniff needs `playwright` (the drainer adapter exports
`NODE_PATH=<repo>/node_modules` so it resolves); once cached, reads run with no browser.

**Why not Microsoft Graph:** the new Teams 2.0 web build (`teams.cloud.microsoft`) never calls
`graph.microsoft.com` for chat - a live sniff presents no graph token - so `/me/chats` is unreachable.
These internal services are what Teams web actually hits.

## Config (environment variables)

Defaults suit Russell's setup; override for another account/team:

- **`DRAINER_TEAMS_WATCHED_TEAM_ID`** - the watched team's space id (only this team's channels surface;
  every other team's channels are intentionally ignored as noise).
- **`DRAINER_TEAMS_WATCHED_TEAM_NAME`** - display name for that team.
- **`DRAINER_SELF_NAME`** - your display name (used to label 1:1 / group chats by the other member[s]).

## Scripts

Under `scripts/` (run with `node`):

- **`teams-chat.js`**
  - Auth glance: `node teams-chat.js token [--force]` - ensure/refresh the cached tokens; prints
    `Tokens OK ✅` with expiries (non-zero exit on a missing-token sniff failure).
  - List unread: `node teams-chat.js enumerate --unread [--top 40]` - unread **chats** (DMs, group
    chats, meeting chats) from the aggregator's authoritative `isRead`, plus unread **channels of the
    one watched team**, newest-first. `--json` is implicit (always JSON). Each item carries `id` (the
    conversation id - `messages`/CLEAR need it), `type` (`dm|group|channel|meeting`), `label`, `unread`,
    `muted`, `fromMe`, `lastMessage` {`id`,`from`,`time`,`preview`}, `lastMessageId`, `deepLink`.
  - Read one: `node teams-chat.js messages <convId> [--top 20]` - recent messages newest-first; each has
    `id`, `from`, `time`, `messageType`, `text` (plain), `html`, `deepLink` (per-message Teams link).

## Notes

- **Unread is the aggregator's `isRead`, not the IC3 consumptionhorizon.** A chat can have its horizon
  caught up yet still be unread; chat unread comes only from the aggregator. Watched-team channels (which
  expose no clean aggregator `isRead`) use the new-message-past-read-horizon heuristic.
- When a conversation has more than one unread message, read its full unread history with
  `messages <convId> --top <unread>` before triaging - the `lastMessage.preview` is only the final message.
- **No mark-read / no send here.** Mark-read is browser-driven (drainer worker); replies go through the
  `message-draft` skill's `teams` mode (Shift+Enter for newlines; never a bare Enter, which sends).
