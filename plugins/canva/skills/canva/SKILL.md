---
name: canva
description: Work in a Canva account two ways. The Connect API (no browser) creates folders, uploads an image/PDF as an asset, lists a folder, and moves items. Browser editing (Playwright over CDP) edits a design's pages - read a page's layers and text, swap and place images to exact px, move text boxes, retype a word, take full-resolution slide screenshots, and run Magic Expand in a scratch design. Use for uploading or organizing files in Canva, and for any edit to an existing Canva design.
---

# Canva - Connect API and browser editing

Two halves, each with its own script:

- **Connect API** (`scripts/canva.js`): assets and folders, no browser. The rest of this file covers it.
- **Browser editing** (`scripts/canva-edit.js`): edits a design's pages, which the Connect API can't do.
  **Before any edit to a design, read [references/browser-editing.md](references/browser-editing.md).**
  It covers the dedicated browser the editing needs, waking a frozen editor, batching and checking each edit, Magic Expand, and the editor's traps.
  Run `canva-edit.js` through browser-chauffeur; its header lists every command, and the reference has the placement-plan format.

## Connect API

Upload assets and manage folders in a Canva account through Canva's public Connect API (`api.canva.com`), via `scripts/canva.js`.

This is its own OAuth 2.0 + PKCE integration registered against the target Canva account - not a shared client like the Google plugins in this repo.
Canva mandates PKCE on every integration, confidential or not.

### What the Connect API can and can't do

- **Asset Upload API** - upload an image/PDF into the account as a Canva asset. Supported here.
- **Folder API** - create a folder, list a folder's items, move an item into a folder. Supported here.
- **Design creation / Autofill API** - create a design from a Brand Template and fill named fields.
  Not implemented here; a separate addition if ever needed.
- **Editing an existing design's pages** has no Connect API endpoint; browser editing covers it.
- **Per-person sharing** is not in the public API - Canva's per-collaborator share model is a UI (and Business/Enterprise team-membership) feature with no Connect API endpoint.
  Sharing a folder or design with a specific person's email stays a manual step via browser-chauffeur, not something `canva.js` can do.

### First-time setup

**Before the first use on a new machine, read [references/setup.md](references/setup.md).**
It covers enabling MFA on the owning account, registering the Canva app, and the one-time OAuth sign-in.

The Connect API side has no npm dependencies - Node's built-in `fetch` and `crypto` cover the OAuth flow and the REST calls.
The browser side uses the `playwright-core` and `browser-chauffeur-helpers` that browser-chauffeur installs.

### Commands - `canva.js`

- **Create a folder:** `node canva.js --create-folder --name="..." [--parent-folder-id=root]`
  `parent-folder-id` defaults to `root`; pass an existing folder's id to nest one folder inside another.
- **Upload an asset:** `node canva.js --upload-asset --file=<path> [--name="..."] [--folder-id=<id>]`
  `name` defaults to the file's basename, truncated to Canva's 50-character cap.
  A fresh upload always lands in the account's default Uploads location first; passing `--folder-id` moves it into that folder once the (asynchronous) upload job succeeds - `canva.js` polls the job to completion before returning.
- **List a folder's items:** `node canva.js --list-folder --folder-id=<id>`
- **Move an item to a folder:** `node canva.js --move-to-folder --item-id=<id> --to-folder-id=<id>`

### Auth-error handling

A `canva.js` "Not signed in to Canva" error means the one-time sign-in must be (re-)run: `node scripts/canva-auth.js` via browser-chauffeur.
On a machine where the app was never registered, see [references/setup.md](references/setup.md) instead.
