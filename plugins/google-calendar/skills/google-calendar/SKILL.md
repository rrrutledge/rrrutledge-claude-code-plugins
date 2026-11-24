---
name: google-calendar
description: Schedule or create a Zoom (or other non-Meet) meeting on Google Calendar with no Google Meet link, read a Google Calendar's events over a time range, or remove the Meet link from an existing event via the Calendar API - no browser. Use when putting a meeting, call, or invite on Google Calendar (especially one with a Zoom or Teams link), when a script needs a Google Calendar's events, or when a Meet link has to come off an event.
---

# Google Calendar - Creating Events Without Meet, Listing, and Meet Removal

## Creating an event

1. Create every meeting with `google-calendar.js --create` (Commands, below), with its Zoom (or other) link as `--join`.
2. Pass `--send` once Russell says to send the invites; without it, guests are added with no email.
3. To take a Meet link off an event that already has one, run `--strip-meet`.

Reschedule or otherwise edit an event with the claude.ai connector's `update_event`, leaving `addGoogleMeetUrl` out; it adds Meet when that flag is `true`.

## The Calendar API script

List a Google Calendar's events over a time range through the Calendar API's `events.list` endpoint, create an event with no Google Meet link, or remove an event's Meet link, using Google's official Node client library (`googleapis`): `scripts/google-calendar.js`.

Reuses the same Google Cloud OAuth "Desktop app" client the `gmail` plugin already registered (`GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`) - one OAuth client, many scopes.
This path requests `calendar.readonly` plus `calendar.events` and keeps its own token cache (`~/.claude/google-calendar/oauth-token.json`).
A token without `calendar.events` still lists events; re-run the sign-in once before the first create or Meet removal.

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
- **Create an event with no Google Meet link:** `node google-calendar.js --create --summary=<title> --start=<YYYY-MM-DDTHH:MM> --end=<YYYY-MM-DDTHH:MM> --join=<url> [--guests=<a@x.com,b@y.com>] [--description=<text>] [--tz=<IANA>] [--reminders=<minutes,...>] [--calendar=<id>] [--send]`
  Times are wall-clock in `--tz`, which defaults to `America/Chicago`.
  `--reminders=15,10,0` sets popup reminders that many minutes before; without it, the calendar's defaults apply.
  It prints the event id and link.
- **Remove an event's Google Meet link:** `node google-calendar.js --strip-meet --event=<id> [--calendar=<id>] [--send]`
  Guests get no update email unless `--send` is passed, and whether to notify them is Russell's call.
  The event id is the one `--create` prints, or the `id` the connector's `list_events` returns.
  For a recurring meeting, pick the scope: an occurrence id clears that one date, and its `recurringEventId` clears the series, except occurrences already edited on their own, which each need their own id.
- **Call it from another script:** `require('<google-calendar>/scripts/google-calendar.js').listEvents({ calendar, start, end })` resolves to the raw `Event` resources overlapping `[start, end)`, recurring events expanded to single instances and ordered by start time.
  Cancelled events are left out.
  `stripMeet({ calendar, event, send })` from the same module resolves to the patched `Event`, or `null` when the event had no conference.
  `createEvent({ calendar, summary, start, end, join, guests, description, timeZone, reminders, send })` resolves to the created `Event`.
