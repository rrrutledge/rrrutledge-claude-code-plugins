# teams provider - Microsoft Teams via the internal REST services

A provider for Microsoft Teams read through the **Teams internal REST services** (the same services Teams web uses) - fast, no DOM hydration.
All reads go through the **`teams`** skill's `teams-chat.js` (don't reimplement the API here); it owns the sniffed ic3 + aggregator tokens, the authoritative `isRead` unread detection, the watched-team channel scoping, and the `messages` read.
Implements `../engine/provider.md`; classify by `../engine/triage.md`. id prefix: `teams-`; body file: `<id>.msg.md`.

> Two-file provider: the **reading** mechanics (enumerate, stable id, capture-writing) live in the sibling **`teams-adapter.py`** that the poller drives.
> This doc is the **worker-facing** prose - AUTH-GLANCE, the captured item shape, CLEAR, JUNK-LEARNING, DRAFT-MODE.

> Reads are REST (fast, browser-free once the token is cached); **CLEAR and DRAFT stay browser-driven** because Teams' authoritative `isRead` flips only on a real conversation open, and Teams has no draft API.

## Config (in `.claude/drainer.local.md` → `providers.teams`)
No config block - `teams: {}`.
Behavior is tuned by environment variables read by the `teams` skill: `DRAINER_TEAMS_WATCHED_TEAM_ID` / `DRAINER_TEAMS_WATCHED_TEAM_NAME` (only this team's channels surface) and `DRAINER_SELF_NAME` (labels 1:1/group chats by the other member[s]).
Tokens are sniffed from the live Teams web session and cached at `~/.claude/drainer/teams-ic3-token.json`; the one-time sniff needs `playwright` (resolved from the home repo's `node_modules`).

The `teams` `teams-chat.js` lives at `<teams-skill>/scripts/teams-chat.js` - run it with `node`.

## Teams footguns (IRREVERSIBLE - never violate)
- **Enter SENDS** in a Teams composer.
  Reads are REST and never type into a composer.
  All composing happens later in the worker via the `message-draft` skill (`teams` mode), which owns the footguns (identity-gate the chat, target the visible composer, Shift+Enter for newlines, never a bare Enter).
- **Heavy iframes (browser path only):** for CLEAR/DRAFT keep to ONE browser-driving context.

## AUTH-GLANCE
Run `node <teams>/teams-chat.js token`.
`Tokens OK ✅` means both tokens are valid (cached or freshly sniffed) and the channel is ready.
If it errors with "Missing token(s)", Teams web isn't open/signed in in the CDP browser: open `https://teams.microsoft.com/`, confirm signed in, and stop reading Teams until the token sniffs clean.
Never surface a raw auth error to the user.

## UNRENDERABLE CARDS ("go.skype.com/cards.unsupported")
When a captured message body is `Card - access it on https://go.skype.com/cards.unsupported`, the message is an adaptive card the drainer's REST API cannot render as text.
**Do not treat this as the content.**
Go read the actual card in Teams web:

1. Find the already-open Teams tab (`teams.cloud.microsoft`) in the CDP browser - do NOT open the deep link in a new tab (it lands on a "download the app" wall).
2. In that tab, click the conversation's name in the left chat list (e.g. "Workday").
3. Screenshot the conversation - Teams web renders the card visually.
4. Read the screenshot and triage based on what the card actually says.

Common Workday cards seen this way: time-off approvals (FYI - no action needed), time-off request confirmations, manager-approval tasks.
Re-triage after reading: most are FYI → route to digest.

## MEETING RECORDING MESSAGES
A meeting-recording notification (recording/transcript link or "Meeting ended" summary) is a **pointer** (a container holding the real content) - see "recognize pointers" in `../engine/triage.md` and the resolve-a-pointer step in `../engine/worker-core.md` § 2b.
Classify as **needs-you (work)** and follow these steps in order:

1. **Open AI notes.**
   Open the meeting's AI notes via `browser-chauffeur`.

2. **Share notes if Russell is the organizer.**
   Check the meeting details or recording attribution - if Russell organized the meeting, copy the full AI summary content (summary and action items) and use the `message-draft` skill (`teams` mode) to draft a message into the meeting chat: "Hey everyone, here are the notes from the meeting." then paste the AI notes verbatim - nothing else after.
   Stage as a draft for Russell's review (standard needs-you draft flow, worker-core step 4).

3. **Extract Russell's action items.**
   Regardless of who organized, scan the AI notes for action items assigned to Russell.
   Each action item becomes a separate needs-you item: write a new `items/<recording-id>-ai<N>.json` + `items/<recording-id>-ai<N>.msg.md` for each (using the meeting name + action-item text as subject/body, `source: "teams"`, `triage: "needs-you"`, `kind: "work"`).
   The poller's next cycle picks these up and spawns worker tabs for them.

4. **No notes or no action items.**
   If AI notes don't exist or none are assigned to Russell (and Russell is not the organizer), the recording is **fyi** - route it to the digest queue and close up.

## WEEK-IN-REVIEW ANNOUNCEMENTS
A team Week-in-Review announcement post (the weekly post linking to that week's R&D Weekly Confluence page) is a container pointing to a report worth analyzing - **needs-you (work)**.
The work: run the `week-in-review-analyzer` skill on the linked Confluence page and present its opportunity table.
The worker opens the linked doc, runs the analyzer, and surfaces the result; there's no reply to send.

## CAPTURE (needs-you)
The adapter writes these; documented here so the worker can rely on the shape:
- `items/<id>.msg.md` - header block (Chat/From, Type [dm|group|channel|meeting], Latest, Link=`deepLink`)
  + the recent messages.
- `items/<id>.json`:
  `{ "id","source":"teams","triage":"needs-you","kind":"reply|work|work-then-reply","from":"<person`
  `or chat label>","subject":"<chat label>","chatType":"dm|group|channel|meeting","received","snippet",`
  `"url":"<conversation deep link>","messageId":"<IC3 conv id>","convId":"<IC3 conv id>",`
  `"msgFile":"<abs path to .msg.md>","ts":"<ISO now>" }`

`convId` (= `messageId`) is the IC3 conversation id - CLEAR needs it to identify the conversation.

## MULTI-MESSAGE THREADS (group and meeting chats)

The `.msg.md` file tags each message with `[NEW]` (unread, after the IC3 consumption horizon) or `[context]` (already seen).
When tags are present:

- **Treat every `[NEW]` message as a potential action item.**
  Scan all of them for direct questions or requests addressed to Russell by name - do not stop at the latest one.
- **Use `[context]` messages for background only.**
  They are included so you can understand what the `[NEW]` messages are responding to; they do not require action.
- **If tags are absent** (horizon unavailable), scan all recent messages for unanswered direct questions or requests addressed to Russell by name before clearing.

Do not let a lower-stakes `[NEW]` message (e.g., a simple acknowledgment) cause you to overlook an earlier `[NEW]` message that contains a direct question.

## CLEAR
**Browser-driven (via `browser-chauffeur`), not REST.**
Teams' authoritative `isRead` is not driven by any replayable HTTP call (the consumptionhorizon PUTs return 200 but don't flip `isRead`); opening the conversation in Teams web flips it within ~5 s via a trouter/websocket signal.
So mark-read = **open the conversation in Teams web**: navigate the CDP browser to `https://teams.cloud.microsoft/v2/?ctx=chat`, wait for the rail, and click the conversation's row (match its visible `label`; `getByText`, first match).
Verify by re-running `node teams-chat.js enumerate --unread` - the item drops out of unread.
Teams has no delete/trash; mark-read is the "gone," and it is reversible.
Narrate each clear with a one-line reason.

**Guard against silently clearing newer messages.**
Opening the conversation marks ALL messages read - including any that arrived after the worker started.
Before clicking the conversation, check for messages newer than `firstUnreadMessageId` (present in `items/<id>.json`):

1. Run `node teams-chat.js messages <convId> --top 20` and parse the JSON output.
2. Compare each message `id` (numeric string) against `firstUnreadMessageId`: collect any with a higher numeric value.
3. Open the conversation in Teams web (marks all read as above).
4. For each newer message id collected in step 2, run:
   `node teams-chat.js mark-unread --conversation-id <convId> --message-id <msgId>`
   (use the **smallest / earliest** newer id - this re-flags that message and everything after it as unread so the next poller cycle picks them up as a fresh item).

Skip step 4 if no newer messages were found - the clear was clean.

If the underlying WORK isn't finished (you only drafted a reply), do NOT clear - leave the conversation unread and write a "paused" note instead.
This is worker-core §2d's clear-as-soon-as-scope-is-accounted-for principle, same as every other source - it just lands this late here because a Teams item's full scope (how many distinct asks the unread messages hold, per MULTI-MESSAGE THREADS above) isn't knowable until they're all read, and marking read is all-or-nothing across the whole conversation.

## JUNK-LEARNING
None.
Teams junk is just cleared (marked read) - the user manages their own Teams mutes; don't propose mutes or rules.

## DRAFT-MODE
`message-draft` skill, `teams` mode (browser composer - owns the voice + composer mechanics + the footgun rules).
Only the read mechanics use the REST script; drafting stays in the browser.
