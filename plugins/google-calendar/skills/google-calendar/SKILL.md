---
name: google-calendar
description: Read a Google Calendar's events over a time range, or remove the Google Meet link from an event, via the Calendar API - no browser. Reuses the gmail plugin's OAuth client with a separate token scoped to calendar.readonly and calendar.events. Use when a script needs a Google Calendar's events, when an event should carry a Zoom (or other) link instead of Meet, or when a Meet link has to come off an existing event.
---

# Google Calendar - Event Listing and Meet Removal via the Calendar API

List a Google Calendar's events over a time range through the Calendar API's `events.list` endpoint, or remove an event's Google Meet link, using Google's official Node client library (`googleapis`): `scripts/google-calendar.js`.

Reuses the same Google Cloud OAuth "Desktop app" client the `gmail` plugin already registered (`GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`) - one OAuth client, many scopes.
This path requests `calendar.readonly` plus `calendar.events` and keeps its own token cache (`~/.claude/google-calendar/oauth-token.json`).
A token without `calendar.events` still lists events; re-run the sign-in once before the first Meet removal.

## Events with no Google Meet link

The claude.ai Google Calendar connector's `create_event` attaches a Meet link to any event with guests unless the call passes `addGoogleMeetUrl: false` explicitly; leaving the flag out gets a Meet link despite its documented `false` default.
So for a meeting whose real link is Zoom, create it through the connector with `addGoogleMeetUrl: false`, and put the Zoom join URL in `location` and `description`.

The connector's `update_event` can add a Meet link but has no way to remove one.
To take a Meet link off an existing event, use `--strip-meet` below.

## Setup (per machine, one-time)

Claude Code installs the script dependencies (`googleapis`, `google-auth-library`) automatically from the plugin-root `package.json` and lockfile whenever it installs or updates the plugin.

1. **Enable the Calendar API on the project** the OAuth client belongs to: `console.cloud.google.com/apis/library/calendar-json.googleapis.com`, select the project, click Enable.
2. **One-time sign-in for the token** - `node scripts/google-calendar-auth.js --expect-email=<address>`, driven via **browser-chauffeur**: open the `AUTH_URL:` line it prints.
   The account owner completes the consent step themselves (granting a new scope to an app is their call) - browser-chauffeur opens the URL, they click Allow.
   Pass `--expect-email` so the token can only be saved for that account.
   For a Google Workspace account, the Workspace admin may first need to trust the OAuth client under Admin console, Security, API Controls, App Access Control.

## Commands - `google-calendar.js`

- **List a calendar's events by range:** `node google-calendar.js --start=<date> --end=<date> [--calendar=<id>] [--json]`
  `--calendar` defaults to `primary`, the signed-in account's own calendar.
  A plain `YYYY-MM-DD` is local midnight, a timestamp needs its UTC offset (`2026-10-01T00:00:00-05:00`), and `--end` is exclusive.
  `--json` prints the raw Google `Event` resources; without it, one line per event.
- **Remove an event's Google Meet link:** `node google-calendar.js --strip-meet --event=<id> [--calendar=<id>] [--send]`
  Guests get no update email unless `--send` is passed, and whether to notify them is Russell's call.
  The event id is the `id` the connector's `create_event` / `list_events` returns.
  For a recurring meeting, pick the scope: an occurrence id clears that one date, and its `recurringEventId` clears the whole series.
  It refuses an event whose conference is another provider's, such as a Zoom add-on.
- **Call it from another script:** `require('<google-calendar>/scripts/google-calendar.js').listEvents({ calendar, start, end })` resolves to the raw `Event` resources overlapping `[start, end)`, recurring events expanded to single instances and ordered by start time.
  Cancelled events are left out.
  `stripMeet({ calendar, event, send })` from the same module resolves to the patched `Event`.
