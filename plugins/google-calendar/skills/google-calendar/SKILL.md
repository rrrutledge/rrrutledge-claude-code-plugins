---
name: google-calendar
description: Read a Google Calendar's events over a time range via the Calendar API - no browser. Reuses the gmail plugin's OAuth client with a separate read-only token scoped to calendar.readonly. Use when a script needs a Google Calendar's events.
---

# Google Calendar - Read-Only Event Listing via the Calendar API

List a Google Calendar's events over a time range through the Calendar API's `events.list` endpoint, using Google's official Node client library (`googleapis`): `scripts/google-calendar.js`.

Reuses the same Google Cloud OAuth "Desktop app" client the `gmail` plugin already registered (`GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`) - one OAuth client, many scopes.
This path requests the read-only `https://www.googleapis.com/auth/calendar.readonly` scope, so it keeps its own token cache (`~/.claude/google-calendar/oauth-token.json`).

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
- **Call it from another script:** `require('<google-calendar>/scripts/google-calendar.js').listEvents({ calendar, start, end })` resolves to the raw `Event` resources overlapping `[start, end)`, recurring events expanded to single instances and ordered by start time.
  Cancelled events are left out.
