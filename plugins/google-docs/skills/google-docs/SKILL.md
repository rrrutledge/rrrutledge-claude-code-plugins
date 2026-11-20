---
name: google-docs
description: Read/write Google Docs content (table rows, paragraphs, hyperlinks) via the Docs API - no browser. Read a doc as text, insert text after a paragraph, replace text, append or delete table rows, and set a cell to plain text, a bulleted list, or a short clickable hyperlink. Reuses the gmail plugin's OAuth client with a separate token scoped to the documents API. Use when editing a Google Doc's content programmatically instead of pasting by hand or driving a browser.
---

# Google Docs - Programmatic Content Edits via the Docs API

Edit a Google Doc's content through the Docs API's `batchUpdate` endpoint, using Google's own official Node client library (`googleapis`) rather than hand-built REST calls: `scripts/google-docs.js`.
Built for appending rows to a tracking table, but generic to any Google Doc.

Reuses the same Google Cloud OAuth "Desktop app" client the `gmail` plugin's filter path already registered (`GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`) - one OAuth client, many scopes.
This path requests `https://www.googleapis.com/auth/documents`, a scope the gmail client's cached token was never consented for, so it keeps its own token cache (`~/.claude/google-docs/oauth-token.json`) separate from `~/.claude/gmail/oauth-token.json`.

## Setup (per machine, one-time)

Claude Code installs the script dependencies (`googleapis`, `google-auth-library`) automatically from the plugin-root `package.json` and lockfile whenever it installs or updates the plugin.

1. **Enable the Docs API** on the Cloud project the OAuth client belongs to: `console.cloud.google.com/apis/library/docs.googleapis.com`, select the project, click Enable.
2. **One-time sign-in** - `node scripts/google-docs-auth.js`, driven via **browser-chauffeur**: it prints an `AUTH_URL:` line, serves `http://localhost:8711/callback`, and on consent exchanges the code and caches the token to `~/.claude/google-docs/oauth-token.json` (machine-local).
   Approve consent as the intended Google account.
   The account owner completes this consent step themselves (granting a new scope to an app is their call) - browser-chauffeur opens the URL, they click Allow.
   After this, `google-docs.js` runs silently (the client auto-refreshes the access token).

**Secrets stay machine-local**, like the gmail OAuth path - the Docs API is reachable only where `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET` are set and this token is cached.

## Commands - `google-docs.js`

Every command takes `--doc-id=<id>` (the doc id from its URL, `docs.google.com/document/d/<id>/edit`) and an optional `--table-index=0` (which table on the page, when a doc has more than one; default the first).

- **Append rows to a table:** `node google-docs.js --append-rows --doc-id=<id> --rows-file=<path to JSON>`
  `rows-file` is a JSON array of rows, each row an array of per-column cell strings; row length must match the target table's column count.
  Cells are inserted as plain text.
- **Delete rows from a table:** `node google-docs.js --delete-rows --doc-id=<id> --row-indices=1,2,3`
  `row-indices` are 0-based table-row positions (row 0 is usually the header).
  Each deletion runs in its own call, highest index first, so earlier deletions never shift the position of a not-yet-deleted row.
- **Set a cell to a bulleted list:** `node google-docs.js --set-cell-bullets --doc-id=<id> --row-index=<n> --col-index=<n> --items-file=<path to JSON>`
  `items-file` is a JSON array of strings - replaces the cell's entire content with one bulleted paragraph per item.
- **Set a cell to a short hyperlink:** `node google-docs.js --set-cell-link --doc-id=<id> --row-index=<n> --col-index=<n> --text="short label" --url="https://..."`
  Replaces the cell's entire content with one line of clickable hyperlinked text - the short label is what's shown; clicking it opens the URL.
- **Set a cell to plain text:** `node google-docs.js --set-cell-text --doc-id=<id> --row-index=<n> --col-index=<n> --text="..." [--bold]`
  Replaces the cell's entire content with one line of plain text, optionally bold - useful for renaming a header cell.
- **Read the doc as text:** `node google-docs.js --read --doc-id=<id>`
  Prints `revisionId: <id>` on the first line, then one line per paragraph; table cells print as `[table T row R col C] text`.
- **Insert text after a paragraph:** `node google-docs.js --insert-after --doc-id=<id> --match="paragraph starts with" --text="..."`
  Inserts at the end of the first paragraph (table cells included) that starts with `--match`.
  A leading `\n` in `--text` makes a new paragraph that inherits the matched paragraph's bullet; the two characters backslash-n in `--text` or `--replace` become a newline.
  Reading and matching cover the doc's first tab only.
  The doc is read just before the insert and the write carries that revision id, so a concurrent edit rejects the batch - re-run it.
- **Replace all matching text:** `node google-docs.js --replace-all --doc-id=<id> --find="old" --replace="new" [--match-case]`
  Prints how many occurrences changed; an empty `--replace` deletes the matched text.

## Auth-error handling

A `google-docs.js` "Not signed in" error means the one-time sign-in must be (re-)run: `node scripts/google-docs-auth.js` via browser-chauffeur.
If Google declines to return a refresh token, revoke prior access at `https://myaccount.google.com/permissions` first, then re-run so a fresh one is issued.
