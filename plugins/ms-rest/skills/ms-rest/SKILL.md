---
name: ms-rest
description: Read, delete, and compose Outlook mail and read, create, and manage calendar events for Russell's WORK account via the Outlook REST API — no browser needed after a one-time sign-in (token sniffed from the live Outlook web session). For the PERSONAL Microsoft account use `ms-graph` instead.
---

# ms-rest — Outlook (work account) mail + calendar

Owns Outlook communication and calendar for Russell's **work** account. Two scripts drive everything
over the **Outlook REST API v2.0** (`https://outlook.office.com/api/v2.0`) using a bearer token
sniffed from Russell's live Outlook web session — so after a one-time sign-in, operations run with
**no browser at all** until the token expires:

- **`outlook-mail.js`** — inbox read, delete, draft, reply, send
- **`outlook-calendar.js`** — calendar view, event create/move/retime/delete (also usable as a Node library)

Both share auth plumbing via `outlook-core.js`.

Use this skill directly for ad-hoc work ("find the email about X", "what's on my calendar tomorrow",
"draft a reply to Y") and as the engine the `message-draft` skill calls for email.

**Work vs personal — don't mix them up.** This skill is the **work** account (corporate Outlook,
session-token sniff). The **`ms-graph`** skill is Russell's **personal** Microsoft account (MSAL +
Graph SDK, `russell.rutledge@outlook.com`). Different auth path, different mailbox. Pick by whose
mail you mean.

## Invocation (resolve the installed copy)

These scripts ship inside this plugin, which Claude Code installs into a versioned cache —
`~/.claude/plugins/cache/<marketplace>/ms-rest/<version>/skills/ms-rest/`. They are **not** copied
into any consuming repo's `.claude/`, so address them through the cache glob below and run from the
repo root (the script resolves `.tmp/` relative to your current repo for token caching). The glob
resolves the installed `ms-rest`; if more than one version is cached, run the newest.

```
node ~/.claude/plugins/cache/*/ms-rest/*/skills/ms-rest/outlook-mail.js <command>
```

## Commands

| Command | What it does |
|---|---|
| `enumerate` | JSON array of **all** inbox messages, newest-first (pages the whole inbox via `@odata.nextLink`). Each item: `id`, `from`{name,address}, `subject`, `received`, `preview`, `isRead`, `hasAttachments`, `webLink`. |
| `get <id>` | Full message JSON: `from`, `to[]`, `cc[]`, `subject`, `received`, `bodyType`, `body`, `preview`, `webLink`, `hasAttachments`. |
| `delete <id>` | Moves the message to **Deleted Items** (reversible). |
| `token [--force]` | Ensure/refresh the cached token; prints a one-line status. `--force` re-sniffs. |
| `create-draft --json <path>` | Creates a NEW draft email. Prints `{draftId, webLink, folder:"Drafts", sent:false}`. |
| `create-reply <id> --json <path>` | Creates a **reply draft** to `<id>` with the quoted original below the new text. Prints `{draftId, webLink, ...}`. |
| `send-draft <id> --yes` | **Sends** an existing draft. Gated — refuses without `--yes`. |

`<id>` everywhere is the Outlook REST message id returned by `enumerate`/`get`.

### Compose payloads (`--json <path>`)
HTML bodies go in a JSON file to avoid shell escaping. Write the file (e.g. to `.tmp/`), then pass it.

`create-draft`:
```json
{ "subject": "...", "body": "<p>HTML body</p>", "to": ["a@b.com"], "cc": [], "bodyType": "HTML" }
```
`create-reply` (new text only — the API supplies the quoted original, placed below):
```json
{ "comment": "<p>New reply text on top</p>" }
```
Recipients accept a plain address string or `{ "address": "...", "name": "..." }`. Use real
`<a href="...">anchor text</a>` for links so they render clickable (matches `document-authoring`).

## Calendar commands

```
node ~/.claude/plugins/cache/*/ms-rest/*/skills/ms-rest/outlook-calendar.js <command>
```

| Command | What it does |
|---|---|
| `calendar-view --start <iso> --end <iso>` | JSON array of all events in the range (recurring series expanded into instances). Each item: `Id`, `Subject`, `Start`/`End` (wall-clock, America/Chicago), `IsAllDay`, `Type`, `SeriesMasterId`, `IsCancelled`, `Attendees`, `IsOnlineMeeting`. |
| `calendar-getschedule --schedules a,b --start <iso> --end <iso> [--interval 30]` | Free/busy for a set of mailboxes (self **and** other tenant attendees). Per schedule: `AvailabilityView` (per-interval `0`=Free/`2`=Busy/`3`=OOF string), `ScheduleItems`, `WorkingHours.TimeZone`, and an `Error` for any address that doesn't resolve. |
| `event-get <id>` | Full event JSON for a single event or recurring series master. |
| `event-create --json <path>` | Create a new event. Payload: `{ subject, start, end, timeZone?, isAllDay?, body?, location?, attendees?, isOnlineMeeting?, reminderMinutesBeforeStart?, categories?, isDraft? }`. Set `isDraft:true` to stage an on-calendar meeting that sends **no** invitation (opens in OWA with a "Send" button). Prints `{id, webLink, subject, start}`. |
| `event-move <id> --date <YYYY-MM-DD>` | Move an event to a new date, keeping time-of-day and duration. Returns `{status: "moved"}` or `{status: "boundary-blocked"}` (Outlook won't let a recurring occurrence cross its neighbors — leave it in place). |
| `event-set-time <id> --start <iso> --end <iso>` | Reschedule to explicit wall-clock start/end times. |
| `event-delete <id>` | Delete an event (204 = deleted, 404 = already gone — idempotent). |
| `token` | Print token status (`Token OK ✅` + expiry/audience) without any API call. |

**Teams-on-draft note:** when `isOnlineMeeting:true` is set on a **draft** (`isDraft:true`), the API read-back reports `IsOnlineMeeting:false` / `OnlineMeetingProvider:Unknown` / no join URL. This is **expected, not a bug** — the OWA editor still shows the Teams toggle ON and provisions the join link at Send. Don't "fix" the false read-back.

`outlook-calendar.js` can also be used as a Node.js library (`require('./outlook-calendar')`), exporting `{ TZ, calendarView, getSchedule, eventGet, eventCreate, eventMove, eventSetTime, eventDelete }`.

## DRAFT-ONLY default (never auto-send)
`create-draft` and `create-reply` only ever produce **drafts** — they never send. Sending is a
separate, gated step (`send-draft --yes`) reserved for an explicit OK from Russell. Drafting lands the
message in **Drafts** for him to review, edit, and send himself — which also preserves learning
from the send (`document-authoring`'s `learn-from-send.md`).

## AUTH-GLANCE (is a token available?)
Run `node ~/.claude/plugins/cache/*/ms-rest/*/skills/ms-rest/outlook-mail.js token`. `Token OK ✅` means a valid token is cached
or was just sniffed and the skill is ready. If it reports **"No Mail.ReadWrite token sniffed"**,
Outlook web needs a signed-in tab on the CDP browser (port 9222) to sniff from. Open one via
browser-chauffeur (navigate to `https://outlook.cloud.microsoft/mail/` and sign in), or reuse the
channel-watch helper: `"..\channel-watch\spawn-signin.cmd" "Outlook" "https://outlook.cloud.microsoft/mail/"`.
Then re-run `token`.

## How the token works (so failures are diagnosable)
Outlook web holds a bearer token whose audience is `https://outlook.office.com` with
`Mail.ReadWrite`/`Mail.Send` scopes. The script sniffs it over CDP (port 9222) and caches it at
`.tmp/outlook-token.json` with its real JWT expiry (typically 1–2 days). While the cached token is
valid, every command runs with **no browser at all**; the browser is opened only to (re)sniff when
the cache is missing/expired or a call returns 401. The script targets the Outlook REST API because
the `graph.microsoft.com` token Outlook holds carries no Mail scopes.

**Why sniff the session token instead of minting one with MSAL:** sniffing keeps our code entirely
out of the authentication path. Russell signs in only to the sanctioned Outlook web site, exactly as
normal, and we passively reuse the token his browser already obtained — no corporate credentials, no
login prompt, and no app/client-id of ours ever participates. An MSAL flow would make our own app the
party requesting the token, which is the kind of tool we keep out of corporate sign-in. The accepted
tradeoff: the sniffed token's audience is fixed at `outlook.office.com`, so we use the Outlook REST
API with direct calls rather than a Graph-audience token and the Graph SDK. (This is exactly why the
**work** account uses REST here while the **personal** account can use Graph via `ms-graph` — a
personal MSAL app registration is fine; a corporate one is not.)

## Endpoint status
The Outlook REST API v2.0 is officially deprecated — every response carries `Deprecation`/`Sunset`
headers — yet it stays live (verified serving HTTP 200 in 2026, years past its sunset date) because
Outlook on the web itself uses it with first-party tokens, which is exactly the token we ride. We stay
on it because the supported alternative, Microsoft Graph, needs a Graph-audience token with Mail
scopes, and the token observable in the session is Graph-audience *without* Mail scopes — so Graph
isn't reachable without an MSAL app registration we've ruled out. The script throws loudly on a real
`410 Gone`, which is the signal to revisit.
