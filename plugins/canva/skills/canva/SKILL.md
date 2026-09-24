---
name: canva
description: Upload assets and manage folders in a Canva account via the Canva Connect API - no browser. Create a folder, upload an image/PDF as an asset (optionally straight into that folder), list a folder's contents, or move an item between folders. Use when uploading files into Canva or organizing them into folders programmatically instead of dragging them through the UI.
---

# Canva - Asset Upload & Folders via the Connect API

Upload assets and manage folders in a Canva account through Canva's public Connect API (`api.canva.com`), via `scripts/canva.js`.

This is its own OAuth 2.0 + PKCE integration registered against the target Canva account - not a shared client like the Google plugins in this repo.
Canva mandates PKCE on every integration, confidential or not.

## What the Connect API can and can't do

- **Asset Upload API** - upload an image/PDF into the account as a Canva asset. Supported here.
- **Folder API** - create a folder, list a folder's items, move an item into a folder. Supported here.
- **Design creation / Autofill API** - create a design from a Brand Template and fill named fields.
  Not implemented here; a separate addition if ever needed.
- **Per-person sharing** is not in the public API - Canva's per-collaborator share model is a UI (and Business/Enterprise team-membership) feature with no Connect API endpoint.
  Sharing a folder or design with a specific person's email stays a manual step via browser-chauffeur, not something `canva.js` can do.

## Setup (per machine, one-time)

0. **Enable MFA on the account that will own the app** - Canva gates Connect API / "Outside Canva" app setup behind MFA, and an authenticator app is the only method it offers.
   The MFA option itself only appears once the account has a password: an account that normally signs in by emailed code must first set one at `https://www.canva.com/login/reset`, then turn on the authenticator app under Settings > Login & security.
1. **Register an integration** at `https://www.canva.com/developers/apps` (verify this URL against the current dev portal - it moves) while signed in as the target Canva account.
   Create an app, add the redirect URI `http://127.0.0.1:8713/callback`, and request scopes `asset:read asset:write folder:read folder:write` - Canva auto-saves each change.
   This is an interactive step for the account owner (or done via browser-chauffeur against their already-authenticated session) - creating the app and clicking through the one consent screen.
   On a Canva Teams (non-Enterprise) plan, set the app's "Who can use your app?" to **Public** - Private is Enterprise-only and the choice is permanent once made.
   A Public app left in Draft still works for the owner without Canva's review.
2. **Save the Client ID and Client Secret** the instant they're generated, per the Secrets policy: leave the page open for the account owner to copy themselves rather than echoing the value in a terminal.
   Store them as `CANVA_CLIENT_ID` / `CANVA_CLIENT_SECRET` in the PowerShell profile (`$PROFILE`) - never in a repo file or `settings.json`.
3. **One-time sign-in** - `node scripts/canva-auth.js`, driven via **browser-chauffeur**: it prints an `AUTH_URL:` line, serves `http://127.0.0.1:8713/callback`, and on consent exchanges the code (PKCE) for tokens and caches them to `~/.claude/canva/oauth-token.json` (machine-local).
   The account owner completes the consent click themselves.
   After this, `canva.js` runs silently - the client auto-refreshes the access token (Canva refresh tokens are single-use; each refresh's new refresh token overwrites the cache immediately).

ISC's working app: "personal-ai-pod" (app ID `AAHOGJAl0pU`), owned by russ@innersourcecommons.org.
An earlier app under info@ (`AAHOGLcPtl8`) sits unused, because info@ has no MFA enabled.

No npm dependencies - Node's built-in `fetch` and `crypto` cover the OAuth flow and the REST calls.

## Commands - `canva.js`

- **Create a folder:** `node canva.js --create-folder --name="..." [--parent-folder-id=root]`
  `parent-folder-id` defaults to `root`; pass an existing folder's id to nest one folder inside another.
- **Upload an asset:** `node canva.js --upload-asset --file=<path> [--name="..."] [--folder-id=<id>]`
  `name` defaults to the file's basename, truncated to Canva's 50-character cap.
  A fresh upload always lands in the account's default Uploads location first; passing `--folder-id` moves it into that folder once the (asynchronous) upload job succeeds - `canva.js` polls the job to completion before returning.
- **List a folder's items:** `node canva.js --list-folder --folder-id=<id>`
- **Move an item to a folder:** `node canva.js --move-to-folder --item-id=<id> --to-folder-id=<id>`

## Auth-error handling

A `canva.js` "Not signed in to Canva" error means the one-time sign-in must be (re-)run: `node scripts/canva-auth.js` via browser-chauffeur.
