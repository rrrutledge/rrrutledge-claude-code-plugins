---
name: google-photos
description: Download photos and videos from someone's Google Photos library via the Photos Picker API - the account owner picks the items in Google Photos, and the script downloads exactly those into a local folder with a manifest. Serves several Google accounts side by side with --account, each with its own token and a wrong-account guard. Use whenever a task needs photos that live in a Google Photos library.
---

# Google Photos via the Photos Picker API

`scripts/google-photos.js` reads a Google Photos library through the Photos Picker API.
Google gives third-party apps one way into a user's existing library: the account owner picks items in Google Photos itself, and the app downloads the picked items.
So every fetch is one pick, and one pick holds up to 2000 items, so it can cover as much as the task needs; Claude then chooses among the downloaded files.

It reuses the Google Cloud OAuth "Desktop app" clients the `gmail` plugin registered, with its own token per account under `~/.claude/google-photos/`.
The token carries `photospicker.mediaitems.readonly` plus `openid email`, which the wrong-account guard reads.

## Accounts

Commands serve the default account; `--account=<name>` on any command switches it to a named account.
A named account reads its OAuth client from `GMAIL_OAUTH_CLIENT_ID_<NAME>` / `GMAIL_OAUTH_CLIENT_SECRET_<NAME>` (`<NAME>` is the account name uppercased, hyphens as underscores), falling back to the shared `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET` - the same naming the `gmail` and `google-drive` skills use.
Every token records the address it was authorized for, and every command first checks that the token still authorizes that address.

## Setup (per machine, one-time per account)

Claude Code installs the dependency (`google-auth-library`) from the plugin-root `package.json` whenever it installs or updates the plugin.

1. **Enable the Photos Picker API** on the Cloud project each OAuth client belongs to: `console.cloud.google.com/apis/library/photospicker.googleapis.com?project=<project number>`, click Enable.
   The project number is the leading digits of the OAuth client id.
2. **Sign in to each account** - `node scripts/google-photos-auth.js [--account=<name>] --expect-email=<address>`, run in the background.
   It prints an `AUTH_URL:` line and serves `http://localhost:8714/callback`, so the consent happens in a browser on this machine where the account is signed in; hand Russell the URL, and the account owner approves.
   The flow checks the consenting address against `--expect-email` before it saves the token.

## Commands - `google-photos.js`

- **Check the signed-in address:** `--whoami` prints the signed-in address - the cheap auth check.
- **Pick and download items:** `--pick --out=<dir> [--since=YYYY-MM-DD] [--max=<n>] [--width=<px>] [--session=<id>]`, run in the background.
  It prints `SESSION:` and `PICKER_URL:` lines, waits for the pick, downloads every picked item into `--out`, writes `manifest-<session>.json` (id, createTime, type, original filename, local path), and deletes the session.
  Hand Russell the `PICKER_URL`; it opens on any device signed in to the account, phone included, and he or the account owner selects and taps Done.
  Google expires an unpicked session after about 25 minutes, so a timeout means a fresh `--pick` and a fresh link.
  `--session` resumes waiting on an existing session, and a session already picked downloads straight away.
  Files are named `<createTime UTC as YYYYMMDD-HHMMSS>_<original filename>`.
  Photos download as originals (often HEIC) unless `--width` asks for a resized copy bounded by that many pixels; videos download as a high-quality transcode.
  `--since` drops picked items created before that date, and `--max` caps how many items the picker lets the user select.

## Auth-error handling

A "Not signed in" error, `invalid_grant`, or the account guard tripping each means that account's sign-in must be re-run; the error message carries the exact command.
An External OAuth client in Testing mode expires its refresh token after 7 days, so that account's sign-in is re-run about weekly.
If Google declines to return a refresh token, revoke prior access on the [Google Account permissions page](https://myaccount.google.com/permissions), then re-run.
An "API has not been used in project" error means step 1 of Setup is still open for that client's project.
