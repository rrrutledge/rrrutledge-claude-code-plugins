---
name: slack
description: Read, mark-read, and (on explicit approval) send in a Slack workspace via the Slack Web API - no browser. Use to list unread DMs / group DMs / @-mentions / unread thread replies (muted conversations skipped), show a message with its permalink, mark a conversation or thread read, or send one reviewed message, optionally with attached files, on Russ's explicit per-message say-so (gated by the writing-review receipt). Headless-safe. Reply drafting stays with the message-draft skill's `slack` mode (browser, draft-only); this skill's send posts an already-reviewed body.
---

# Slack — Read/Mark a Workspace via the Web API

Read and clear a Slack workspace directly over the **Slack Web API** with a token call — no browser. Zero
npm dependencies: `slack.js` uses Node's built-in `fetch` (Node 18+).

**This skill reads unread items and advances read cursors; drafting is elsewhere; the one gated send is
`--send`.** Composing a reply is a separate, general capability - the **`message-draft`** skill's `slack`
mode stages a reply in the Slack composer (browser) and leaves it unsent for Russ to review in his own
Slack. Keep those two apart: reads go through `slack.js`, drafts go through `message-draft`. The one write
in this script that reaches another person is `--send` (see **Sending** below): it posts a body Russ has
already reviewed and explicitly told you to send this turn, which is why the send belongs next to the
token-call plumbing here rather than in the browser composer.

## Auth

`slack.js` calls the Web API with a token, sent as a Bearer header plus a companion `d` session cookie.
The cookie is required for a browser-issued `xoxc` user token (which is `invalid_auth` without it) and for
the `client.*` endpoints; a bot/app token that doesn't need a cookie can set it to any non-empty value. A
user token is what lets the poller read unread DMs and mentions the way the account-holder sees them.

Set three environment variables (never in a file):

- **`SLACK_BOT_TOKEN`** — the Slack API token (for a personal user token, the `xoxc-` value).
- **`SLACK_COOKIE_D`** — the `d` session cookie value (the `xoxd-…` companion to an `xoxc` token).
- **`SLACK_TEAM_ID`** — the workspace's team id.

**Secrets stay machine-local.** The plugin code is shared via the marketplace; the token/cookie are
per-machine, so the workspace is only reachable where you've set them. For a browser-sniffed user token,
the token and cookie expire periodically — when `--check` reports `invalid_auth`, re-sniff both from the
browser (the token from `localStorage.localConfig_v2.teams[<teamId>].token`, the cookie from
DevTools → Application → Cookies → `d`) and re-set the env vars. There is no refresh-token flow.

## Scripts

Under `scripts/` (run with `node`):

- **`slack.js`**
  - Auth glance: `node slack.js --check` (calls `auth.test`; prints the signed-in user/team; non-zero
    exit on auth failure)
  - List unread: `node slack.js --list-unread [--top=50] [--json]` — unread **DMs**, **group DMs**,
    **@-mentions of you in channels**, **unread channel messages (no @-mention, one item per channel)**,
    and **unread replies in subscribed threads**, newest-first. **Muted conversations are skipped**
    (muting is the user's "stop"). `--json` emits a structured array; each item carries `id`
    (`<channel>:<ts>`), `channel`, `channelType` (`im`/`mpim`/`channel`/`thread`), `ts`, `threadTs`
    (set for thread items), `from`, `fromId`, `subject`, `channelName`, `received` (ISO), `preview`,
    `unreadCount`. Channel items: `subject` is `"@mention in #name"` for @-mention items or
    `"Unread in #name"` for non-mention unread items.
  - Show one: `node slack.js --show --channel=<C> --ts=<ts> [--thread-ts=<tts>] [--json]` — the message
    text plus a `chat.getPermalink` url. Pass `--thread-ts` to read a threaded reply. `--json` emits
    `{channel,ts,threadTs,from,fromId,received,text,permalink}`.
  - History: `node slack.js --history --channel=<C> [--thread-ts=<tts>] [--limit=50] [--json]` — recent
    messages, oldest first: a whole thread (`conversations.replies`) when `--thread-ts` is given, else
    the channel/DM/group-DM timeline (`conversations.history`). Use this for a situational check — a
    captured item's `url`/`ts` points at one message, not the whole conversation, so pull the history
    around it to see anything posted after capture, in either direction (the contact's reply, or your
    own follow-up). `--json` emits an array of `{ts,threadTs,replyCount,from,fromId,received,text}`.
  - Mark read: `node slack.js --mark --channel=<C> --ts=<ts> [--thread-ts=<tts>]` — `conversations.mark`
    up to `<ts>`, or `subscriptions.thread.mark` when `--thread-ts` is given (the conversation/thread's
    "gone"; reversible — re-reading re-surfaces it, never deletes).
  - Send (REAL SEND): `node slack.js --send --channel=<C> --body-file=<file> [--thread-ts=<tts>] [--file=<path>[,<path>...]]` -
    `chat.postMessage` of the reviewed body as the signed-in user (or, with `--file`, the upload-and-caption
    flow below), then prints the sent message's permalink. Pass `--thread-ts` to reply inside a thread; omit
    it to post a top-level message (a DM, group DM, or channel message). The body is **Slack mrkdwn**: a link
    is `<url|anchor text>` and a mention is `<@U…>`. `--body-file` (never an inline body) is what lets the
    writing-review gate read and receipt the exact bytes that go out. See **Sending** below - this runs only
    on Russ's explicit per-message say-so.
    Add `--file=<path>` to attach local files, comma-separated for several (`--file=a.png,b.png`).
    It uploads each file via `files.getUploadURLExternal`, then one `files.completeUploadExternal` call posts
    them all as a single message with the `--body-file` text as the caption.
    Same gate, same say-so - `--file` only changes how the reviewed body reaches Slack.
  - List drafts: `node slack.js --list-drafts [--channel=<C>] [--thread-ts=<tts>] [--json]` - the active
    (unsent, undeleted) drafts on Slack's server, optionally only those addressed to one conversation or thread.
    This server list is what the user's own Slack syncs from, so it is the proof a browser-staged draft saved.
  - Create a draft: `node slack.js --create-draft --channel=<C> --body-file=<file> [--thread-ts=<tts>]` -
    saves the reviewed body as an unsent draft in that conversation, under **Drafts & sent** for Russ to
    review and send himself.
    It reaches no one.
    Mrkdwn links (`<url|anchor text>`) become real links.
    Mentions (`<@U...>` for a person, `<#C...>` for a channel) become name chips instead of literal text.
    The writing-review gate requires a receipt on `--body-file`, the same as any other stage.
  - Delete a draft: `node slack.js --delete-draft=<draft id>` - removes one staged draft, using the id that
    `--list-drafts` or `--create-draft` prints.
    It reaches no one.
  - Open a DM by user ID: `node slack.js --open-dm=<user ID> [--json]` - `conversations.open` on a Slack
    user ID you already have, printing the DM channel id.
    Use this over `--find-dm` when you're resolving a batch of known member IDs rather than searching by name.

## How it works (Web API endpoints)

- `auth.test` — identity (`--check`, and to learn your own `user_id` for mention detection).
- `users.prefs.get` — the muted-conversation set (`all_notifications_prefs.channels[id].muted`), so muted
  conversations are excluded from `--list-unread`.
- `client.counts` — the unread/mention badge counts across all DMs, group DMs, and channels in one call
  (a Slack **client** endpoint; it needs the `d` cookie).
- `conversations.history` — the unread top-level messages in a conversation since its `last_read`.
- `subscriptions.thread.getView` — subscribed threads with unread replies (each thread keeps its own
  `last_read`, separate from the channel's, so thread replies aren't in `conversations.history`).
- `conversations.replies` — the messages of one thread (for `--show`/`--mark` on a threaded reply).
- `conversations.info` / `users.info` — resolve channel names and sender real-names (cached per run).
- `chat.getPermalink` — a stable web link to one message (the captured item's `url`, and the link `--send`
  prints for the message it just posted).
- `chat.postMessage` - post one reviewed message as the signed-in user (`--send`; needs the `d` cookie).
- `files.getUploadURLExternal` / `files.completeUploadExternal` - reserve an upload slot per file, then post
  the uploaded files into the conversation with the reviewed body as their caption (`--send --file=<path>[,<path>...]`).
- `drafts.list` / `drafts.create` / `drafts.delete` - read the server-side draft list, save an unsent draft,
  and remove one (`--list-drafts`, `--create-draft`, `--delete-draft`).
- `conversations.open` - open or find the existing 1:1 DM for a known user ID (`--open-dm`; also used
  internally by `--find-dm` once it's matched a name to a user).
- `conversations.mark` / `subscriptions.thread.mark` — advance the conversation / thread read cursor (CLEAR).

## Sending

Reading and drafting stay draft-only; `--send` is the single exception, and it is human-in-the-loop.
A message goes out **only** when Russ, having read the exact body you printed in the terminal this turn,
gives an explicit per-message instruction to send it - the same bar as the `gmail`/`ms-graph` send paths.
`--send` posts the body it is handed rather than sending a staged draft by id; the writing-review
receipt on `--body-file` proves that exact body was reviewed, so the gate blocks a send whose bytes have no
fresh receipt.

**The terminal preview is the review surface for a direct send.** Print the exact message text in the
terminal - that is what Russ reads to decide, usually without opening the browser-staged draft. Two
outcomes follow from what he reads there:

- **Looks good:** he says to send it, and you run `--send` on that exact body.
- **Wants changes:** he makes them in the browser composer and sends there himself, so the send is his.

When Russ says to send:

1. Print the exact body in the terminal (if you haven't already this turn), and author (or reuse) it as a
   Slack-mrkdwn file - links as `<url|anchor text>`.
2. Dispatch `writing-review` on that file and mint its receipt (the gate reads it), unless a fresh receipt
   for those exact bytes already exists from staging.
3. Resolve the target: a DM channel via `--find-dm` (by name) or `--open-dm` (by user ID), or the
   `channel` (+ `thread-ts`) from the captured item.
4. Run `node slack.js --send --channel=<C> --body-file=<file> [--thread-ts=<tts>] [--file=<path>[,<path>...]]` and
   report the permalink.

These constraints keep send safe:

- **Draft-only by default:** silence or ambiguous phrasing never becomes a send; infer a send only from a
  clear, explicit instruction to send this message.
- **Never in an autonomous run:** an `auto-handle` or otherwise non-interactive drain has no one to give
  that instruction, so it stops at the staged draft. `--send` is human-in-the-loop only.

## Notes

- The load-bearing identifier is **`<channel>:<ts>`** (the `id` in `--json`): a Slack `ts` is unique per
  message. Pass the `channel`/`ts` (and `threadTs` for a thread item) to `--show` and `--mark`.
- An **item** is one **conversation** for a DM/group-DM (keyed to its latest unread message), one
  **message** for each channel @-mention, and one **thread** for each subscribed thread with unread
  replies (keyed to its latest unread reply).
- **Drafting stays in `message-draft` (see the intro); only the approved send is here.** A reply is
  composed in the browser composer over there, which then confirms the save with `--list-drafts` and falls
  back to `--create-draft` when the server has no copy. The one send this script performs is `--send` (see
  **Sending**), and only on Russ's explicit per-message say-so.
