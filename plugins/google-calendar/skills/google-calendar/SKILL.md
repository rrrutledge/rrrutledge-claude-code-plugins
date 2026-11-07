---
name: google-calendar
description: Read a Google Calendar's events over a time range via the Calendar API - no browser. Reuses the gmail plugin's OAuth client with a separate read-only token scoped to calendar.readonly. Use when a script needs a Google Workspace calendar's events (e.g. the ISC calendar) that the claude.ai Google Calendar connector would otherwise have to fetch.
---

# Google Calendar - Read-Only Event Listing via the Calendar API

List a Google Calendar's events over a time range through the Calendar API's `events.list` endpoint, using Google's official Node client library (`googleapis`): `scripts/google-calendar.js`.

Reuses the same Google Cloud OAuth "Desktop app" client the `gmail` plugin already registered (`GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`) - one OAuth client, many scopes.
This path requests the read-only `https://www.googleapis.com/auth/calendar.readonly` scope, so it keeps its own token cache (`~/.claude/google-calendar/oauth-token.json`).

## Setup (per machine, one-time)

Claude Code installs the script dependencies (`googleapis`, `google-auth-library`) automatically from the plugin-root `package.json` and lockfile whenever it installs or updates the plugin.

1. **Enable the Calendar API** on the Cloud project the OAuth client belongs to: `console.cloud.google.com/apis/library/calendar-json.googleapis.com`, select the project, click Enable.
2. **One-time sign-in** - `node scripts/google-calendar-auth.js --expect-email=<address>`, driven via **browser-chauffeur**: it prints an `AUTH_URL:` line, serves `http://localhost:8714/callback`, and on consent exchanges the code and caches the token.
   The account owner completes the consent step themselves (granting a new scope to an app is their call) - browser-chauffeur opens the URL, they click Allow.
   `--expect-email` refuses to save a token for any other account, and every later read re-checks the address.
   For a Google Workspace account, the Workspace admin may first need to trust the OAuth client under Admin console, Security, API Controls, App Access Control.

## Commands - `google-calendar.js`

- **List a range:** `node google-calendar.js --list --start=<date> --end=<date> [--calendar=<id>] [--json]`
  `--calendar` defaults to `primary`, the signed-in account's own calendar.
  A plain `YYYY-MM-DD` is local midnight, and `--end` is exclusive, so `--start=2026-10-01 --end=2026-11-01` is all of October.
  `--json` prints the raw Google `Event` resources; without it, one line per event.
- **From another script:** `require('<google-calendar>/scripts/google-calendar.js').listEvents({ calendar, start, end })` resolves to the raw `Event` resources overlapping `[start, end)`, every page fetched, recurring events expanded to single instances (`singleEvents=true`) and ordered by start time (`orderBy=startTime`).
  Cancelled instances come back as Google returns them, with `status: 'cancelled'`.

## Auth-error handling

A "Not signed in" error means the one-time sign-in must be (re-)run, via browser-chauffeur.
If Google declines to return a refresh token, revoke prior access at `https://myaccount.google.com/permissions` first, then re-run so a fresh one is issued.
