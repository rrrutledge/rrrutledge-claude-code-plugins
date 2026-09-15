# zoom provider — Zoom AI Companion meeting summaries (REST)

A generic provider for **a Zoom account's AI Companion meeting summaries**. Each finished meeting's summary
carries a `next_steps[]` list; the steps **assigned to the account owner** are their action items. The
drainer treats one meeting as **N action items + 1 recap**: the sibling **`zoom-adapter.py`** (which the
poller drives) talks to the Zoom REST API directly and fans out one candidate per owner-assigned next step
(recap folded into each for context) plus one recap candidate for the meeting as a whole.

It finds meetings by listing the **authenticated account's own past meetings** (`/v2/users/me/meetings?
type=previousMeetings`) — the OAuth token fixes *whose* meetings are visible (always the account that owns
the credentials) — and reads each one's AI Companion summary. `owner_names` (below) is a separate filter:
which *next steps within those meetings* count as the owner's action items.

Everything Zoom-specific — the REST calls, the OAuth token refresh, the meeting walk, the parsing — lives
in `zoom-adapter.py`. It is **self-contained**: no external script, no assumption about who is running it.
Anyone with a Zoom account that produces AI Companion summaries can enable it by filling in the config
below and setting one environment secret. Implements `../engine/provider.md`; classify by
`../engine/triage.md`. id prefix: `zoom-`; body file: `<id>.zoom.md`.

> Two-file provider: the **reading** mechanics (enumerate, OAuth, fan-out, stable id, capture-writing) live
> in `zoom-adapter.py`. This doc is the **worker-facing** prose — AUTH-GLANCE, the captured item shape,
> CLEAR, JUNK-LEARNING, DRAFT-MODE.

## What is an item
- **action-item** — one `next_steps[]` entry whose **assignee** (the "Name:" prefix) is the account owner.
  This is the workable thing: draft a reply, do the work, make the intro. `zoomKind: "action-item"`,
  `stepText` set. Default triage is **needs-you**; the rubric may file a purely informational or
  already-done one as **fyi**.
- **recap** — the "meeting happened, here's the summary" fact. Nothing is asked of the owner by the recap
  itself (the action items are their own items), so it is **fyi** → the digest. `zoomKind: "recap"`.

"The owner" is defined by config (`owner_names`), else discovered from the authed account (`/users/me`) —
so if the owner is Sam, a step like "Priya: share the script *with Sam*" is Priya's task and is skipped
(the adapter matches an owner name in the "Name:" **prefix**, not anywhere in the line).

## Config (in `.claude/drainer.local.md` → `providers.zoom`)
- **`client_id`** (required) — the Zoom OAuth app's Client ID (not a secret). May instead be supplied as
  `ZOOM_CLIENT_ID` in the environment.
- **`owner_names`** (optional) — a list of display-name tokens whose next steps are "the owner's", e.g.
  `[Sam, Samuel]`. If omitted, the adapter uses the authed account's own name from `/users/me`.
- **`token_cache`** (optional) — path to the OAuth token-cache JSON (holds the rotating refresh token +
  the last access token). Defaults to `<runtime_dir>/zoom-tokens.json`. Point it at an existing Zoom token
  file to share auth with another Zoom tool on the machine (they must share ONE cache — Zoom rotates the
  refresh token on every refresh, so two independent caches would invalidate each other).
- **`lookback_hours`** (default 48) — how far back each poll looks. Bounds the API cost; older summaries
  are assumed already captured (seen-state dedups anyway).
- **`cooldown_minutes`** (default 30) - once a summary has generated, it is treated as **final** only after its `summary_last_modified_time` has been quiet this long, so a summary Zoom is still editing isn't frozen mid-write.
  A summary counts as generated once its `next_steps` are present: Zoom omits that key while AI Companion is still working (the shell it returns has no recap and no `next_steps`) and includes it once done, so a summary whose `next_steps` have not arrived is skipped and re-fetched next cycle rather than cached - the recap is never captured ahead of the action items that belong with it.
  An empty `next_steps` list, from a meeting with genuinely no action items, counts as present and done.
- **`poll_interval_minutes`** (default 20) — self-throttle: the meeting walk is heavy (many API calls), so
  the adapter skips it if it ran within this window. Nothing is lost — seen-state dedups and the cooldown
  means a just-finished meeting isn't ready yet anyway.

Credentials (environment):
- **`ZOOM_CLIENT_SECRET`** (required) — the OAuth app's secret.
- **`ZOOM_REFRESH_TOKEN`** (bootstrap) — a valid refresh token for the first run; after that the adapter
  caches and rotates it in `token_cache`, so this is only needed until the cache exists.

## AUTH-GLANCE
The adapter manages Zoom OAuth itself: it reads the access token from `token_cache` and **auto-refreshes**
it (POST `https://zoom.us/oauth/token`, Basic auth with `client_id`:`ZOOM_CLIENT_SECRET`, `grant_type=
refresh_token`) when it nears expiry, writing the rotated refresh token back to the cache. A failure
surfaces as a provider error and the poller skips zoom this cycle; the daily digest flags it. If it stays
broken:
1. **`config` error** — `client_id` / `ZOOM_CLIENT_SECRET` / a refresh token is missing. Set them.
2. **`auth` error (refresh returns `invalid_grant`)** — the refresh token expired (~90 days) or was
   consumed elsewhere. Re-authorize the Zoom OAuth app to mint a fresh refresh token and put it in
   `ZOOM_REFRESH_TOKEN` (or the `token_cache`).
Never surface a raw "token expired" error to the user — refresh (or re-auth) and retry silently.

**Zoom OAuth app scopes** the adapter needs: `meeting:read:summary`, `meeting:read:list_meetings`,
`meeting:read:list_past_instances`, and `user:read` (for the `/users/me` owner-name fallback).

## CAPTURE (the item shape the worker reads)
The adapter writes two files per dispatched item (`zoom-adapter.py` → `capture`):
- `items/<id>.zoom.md` — header (Meeting, When, Summary doc, Task link, MeetingId, StepId) + (for an action
  item) the **This action item** block + the **Meeting recap** + **All next steps** + **Details**.
- `items/<id>.json` — `{ "id","source":"zoom","triage","kind","zoomKind":"action-item|recap","from",`
  `"subject","received","snippet","url","meetingId","meetingTopic","stepId","stepText","bodyFile","ts" }`.

Load-bearing: `meetingTopic` + `stepText` (what the action is), `url` (the Zoom Tasks deep link for an
action item — `tasks.zoom.us?meetingId=…&stepId=…` — else the summary doc), `meetingId` (the occurrence
uuid). `stepId` is a convenience only (it lives solely in the summary HTML and can change as the AI refines
the summary) — never treat it as identity.

## CLEAR
Zoom exposes **no public API to mark a Tasks step complete** (the `tasks.zoom.us` links are a UI surface,
not a documented write endpoint). So CLEAR for this source is **internal seen-state only**: the item's
seen key, written when the poller dispatched it, is what keeps it from resurfacing, and the poller's
reconcile leaves this source alone - a new meeting summary is a new item, so nothing is lost. Honest and
reversible (losing seen-state just re-surfaces it; never destructive). The *real* completion of an action
happens in its own channel - the email got sent, the doc got written - and is cleared there if that
channel is itself a drainer source.
Optionally the worker can open the item's `url` (the `tasks.zoom.us` deep link) in **browser-chauffeur** to
tick the step off by hand — offer it, don't assume it.

## JUNK-LEARNING
N/A — these are AI-curated meeting action items, not inbound noise. (If a whole class of meeting is never
actionable, the lever is upstream — turn AI Companion off for it in Zoom — not a drainer rule.)

## DRAFT-MODE
There's **no Zoom thread to reply into** — a Zoom action item is a commitment whose channel is whatever the
action implies, not Zoom. Most action items are **work** (prepare, check, build) or an **outbound message**.
When the action is a reply/outbound message, draft it through the **`message-draft`** skill in the channel
the action implies (e.g. `outlook` / `gmail` for email, `slack` / `teams` when the action names that
surface). Draft-only; never send. A reply is warranted only after any underlying **work** is done — draft
about the outcome, not a promise.
