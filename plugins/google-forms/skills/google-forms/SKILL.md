---
name: google-forms
description: Create a Google Form via the Forms API — no browser. Create the form, lay out instruction sections, and add short-answer questions programmatically. Reuses the gmail plugin's OAuth client with a separate token scoped to the forms.body API. A file-upload question is the one item the Forms API can't create; the skill guides the single manual step to add it. Use when building a Google Form for intake or a survey instead of clicking through the Forms UI.
---

# Google Forms — Programmatic Form Creation via the Forms API

Create and lay out a Google Form through the Forms API's `forms.create` and `forms.batchUpdate` endpoints, using Google's own official Node client library (`googleapis`) rather than hand-built REST calls: `scripts/google-forms.js`.
Builds the form, its instruction sections, and its short-answer questions in code, so an intake form or survey comes together without clicking through the Forms editor.

Reuses the same Google Cloud OAuth "Desktop app" client the `gmail` plugin's filter path already registered (`GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`) - one OAuth client, many scopes.
This path requests `https://www.googleapis.com/auth/forms.body`, a scope the other Google clients' cached tokens were never consented for, so it keeps its own token cache (`~/.claude/google-forms/oauth-token.json`) separate from the gmail, google-docs, and google-sheets caches.

## The one thing the API can't do: file-upload questions

The Forms API cannot create a **file-upload question** - a documented, long-standing gap ([issuetracker.google.com/issues/229136447](https://issuetracker.google.com/issues/229136447)).
Everything else a form needs is scriptable here; the file-upload question is added by hand in the Forms editor, one question, about thirty seconds.
`--add-file-upload` prints those exact click steps and the form's edit URL so the manual step is unmistakable rather than a silent failure.

Two properties of a file-upload question, worth knowing before you rely on one:

- A respondent **must be signed in to a Google account** to upload - uploads are never fully anonymous.
- Forms auto-creates the destination Drive folder in the form owner's Drive the moment the question is added, and saves every upload there.

## Setup (per machine, one-time)

Claude Code installs the script dependencies (`googleapis`, `google-auth-library`) automatically from the plugin-root `package.json` and lockfile whenever it installs or updates the plugin.

1. **Enable the Forms API** on the Cloud project the OAuth client belongs to: `console.cloud.google.com/apis/library/forms.googleapis.com`, select the project, click Enable.
2. **One-time sign-in** - `node scripts/google-forms-auth.js`, driven via **browser-chauffeur**: it prints an `AUTH_URL:` line, serves `http://localhost:8712/callback`, and on consent exchanges the code and caches the token to `~/.claude/google-forms/oauth-token.json` (machine-local).
   Approve consent as the intended Google account.
   The account owner completes this consent step themselves (granting a new scope to an app is their call) - browser-chauffeur opens the URL, they click Allow.
   After this, `google-forms.js` runs silently (the client auto-refreshes the access token).

**Secrets stay machine-local**, like the gmail OAuth path - the Forms API is reachable only where `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET` are set and this token is cached.

## Commands - `google-forms.js`

- **Create a form:** `node google-forms.js --create-form --title="..." [--document-title="..."]`
  `title` is the form's visible header; `document-title` is its Drive file name, defaulting to the title.
  Prints the `formId`, the edit URL (`docs.google.com/forms/d/<id>/edit`), and the responder URL.
  A form is created with only the title (all `forms.create` accepts); every item below is a follow-up.
- **Add an instruction section:** `node google-forms.js --add-text --form-id=<id> --title="Section heading" --description-file=<path to md/txt> [--index=N]`
  A heading plus a body block for instructions - the file's contents become the body.
  Appends to the end of the form unless `--index` (0-based) picks a slot; repeat to build several sections.
- **Add a short-answer question:** `node google-forms.js --add-short-answer --form-id=<id> --title="Your name" [--required] [--index=N]`
  A single-line text question; `--required` makes it mandatory.
  Appends to the end unless `--index` is given.
- **Add a file-upload question (guidance):** `node google-forms.js --add-file-upload --form-id=<id>`
  Prints the one-click manual steps and the edit URL, since the API can't create this item (see above).
- **Show a form's structure:** `node google-forms.js --show-form --form-id=<id>`
  Dumps the form's title, edit and responder URLs, and every item - its index, type, and title - for verification.

## Auth-error handling

A `google-forms.js` "Not signed in" error means the one-time sign-in must be (re-)run: `node scripts/google-forms-auth.js` via browser-chauffeur.
If Google declines to return a refresh token, revoke prior access at `https://myaccount.google.com/permissions` first, then re-run so a fresh one is issued.
