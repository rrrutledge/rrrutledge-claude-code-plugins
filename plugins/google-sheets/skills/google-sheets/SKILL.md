---
name: google-sheets
description: Append rows to a Google Sheet, or read a range's values, via the Sheets API — no browser. Reuses the gmail plugin's OAuth client with a separate token scoped to the spreadsheets API. Use when writing to a tracked Google Sheet programmatically instead of pasting by hand or driving a browser.
---

# Google Sheets — Programmatic Row Appends via the Sheets API

Append rows to a Google Sheet, or read a range's values, through the Sheets API's `values.append`/`values.get` endpoints, using Google's own official Node client library (`googleapis`) rather than hand-built REST calls or browser paste automation: `scripts/google-sheets.js`.

Reuses the same Google Cloud OAuth "Desktop app" client the `gmail` plugin's filter path already registered (`GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`) - one OAuth client, many scopes.
This path requests `https://www.googleapis.com/auth/spreadsheets`, a scope the gmail client's cached token was never consented for, so it keeps its own token cache (`~/.claude/google-sheets/oauth-token.json`) separate from `~/.claude/gmail/oauth-token.json`.

## Setup (per machine, one-time)

Claude Code installs the script dependencies (`googleapis`, `google-auth-library`) automatically from the plugin-root `package.json` and lockfile whenever it installs or updates the plugin.

1. **Enable the Sheets API** on the Cloud project the OAuth client belongs to: `console.cloud.google.com/apis/library/sheets.googleapis.com`, select the project, click Enable.
2. **One-time sign-in** - `node scripts/google-sheets-auth.js`, driven via **browser-chauffeur**: it prints an `AUTH_URL:` line, serves `http://localhost:8712/callback`, and on consent exchanges the code and caches the token to `~/.claude/google-sheets/oauth-token.json` (machine-local).
   Approve consent as the intended Google account.
   The account owner completes this consent step themselves (granting a new scope to an app is their call) - browser-chauffeur opens the URL, they click Allow.
   After this, `google-sheets.js` runs silently (the client auto-refreshes the access token).

**Secrets stay machine-local**, like the gmail OAuth path - the Sheets API is reachable only where `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET` are set and this token is cached.

## Commands - `google-sheets.js`

Every command takes `--sheet-id=<id>` (the spreadsheet id from its URL, `docs.google.com/spreadsheets/d/<id>/edit`) and `--range=<A1 range>` (a sheet name plus column/row span, e.g. `Sheet1!A:D` or `Sheet1!A4:D`).

- **Append rows:** `node google-sheets.js --append-rows --sheet-id=<id> --range=<A1 range> --rows-file=<path to JSON>`
  `rows-file` is a JSON array of rows, each row an array of per-column cell values.
  The API finds the sheet's current last row within `--range` and inserts the new rows immediately below it - no last-row detection needed on the caller's side.
  Values are written with `valueInputOption=USER_ENTERED`, so a string like `"9/3/2026"` lands as a real date cell, the same way typing or pasting it into the sheet would.
- **Read a range's values:** `node google-sheets.js --get-values --sheet-id=<id> --range=<A1 range>`
  Prints the range's values as a JSON array of rows - useful to confirm the row count before/after an append.

## Auth-error handling

A `google-sheets.js` "Not signed in" error means the one-time sign-in must be (re-)run: `node scripts/google-sheets-auth.js` via browser-chauffeur.
If Google declines to return a refresh token, revoke prior access at `https://myaccount.google.com/permissions` first, then re-run so a fresh one is issued.
