---
name: google-drive
description: Search, copy, share, and upload Google Drive files, and read or edit Google Slides decks (insert a photo onto a slide found by its title, set or append text in a placeholder), via the Drive and Slides APIs - no browser. Serves several Google accounts side by side with --account, each with its own token and a wrong-account guard. Use for any programmatic Drive or Slides work on an account the claude.ai Drive connector isn't signed in to, and for any Slides edit.
---

# Google Drive + Slides via the Drive and Slides APIs

`scripts/google-drive.js` drives Google Drive and Google Slides through Google's official Node client (`googleapis`).
It reuses the Google Cloud OAuth "Desktop app" clients the `gmail` plugin registered - one OAuth client, many scopes - with its own token per account under `~/.claude/google-drive/`.
The token carries two scopes:
- **`drive`** - search, copy, share, and upload any file the account can reach (the narrower `drive.file` sees only files this app created, so it can't open an existing template or folder).
- **`presentations`** - read and edit Slides decks.

## Accounts

No flag serves the default account; `--account=<name>` on any command serves a named account instead.
The repo that uses this skill records which account names exist and whose Drive each one is.
A named account reads its own OAuth client from `GMAIL_OAUTH_CLIENT_ID_<NAME>` / `GMAIL_OAUTH_CLIENT_SECRET_<NAME>` (`<NAME>` is the account name uppercased, hyphens as underscores), falling back to the shared `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`.
This is the same client naming the `gmail` skill uses, so an account already set up there brings its client along.

A consumer `@gmail.com` account needs an External OAuth client, since an Internal client only consents accounts in its own Workspace org.
An External client left in Testing mode lists the account as a test user, and Google expires its refresh token after 7 days, so that account's sign-in is re-run about weekly; publishing the client to production removes the expiry.

Every token records the address it was authorized for, and every command first checks that the token still authorizes that address, so an operation never lands in the wrong Drive.

## Setup (per machine, one-time per account)

Claude Code installs the dependencies (`googleapis`, `google-auth-library`) from the plugin-root `package.json` whenever it installs or updates the plugin.

1. **Enable the Drive and Slides APIs** on the Cloud project each OAuth client belongs to: `console.cloud.google.com/apis/library/drive.googleapis.com` and `.../slides.googleapis.com`, select the project, click Enable.
2. **Sign in** - `node scripts/google-drive-auth.js [--account=<name>] --expect-email=<address>`, driven via **browser-chauffeur**.
   It prints an `AUTH_URL:` line and serves `http://localhost:8713/callback`; the account owner approves the consent screen themselves, and the token caches to `~/.claude/google-drive/oauth-token[-<name>].json`.
   The flow refuses to save a token for any address other than `--expect-email`.
   After this, `google-drive.js` runs silently for that account.

## Commands - `google-drive.js`

Every command takes `--account=<name>` for a named account; the listing commands also take `--json`.

### Drive

- **Who am I:** `--whoami` prints the signed-in address - the cheap auth check.
- **List files:** `--list [--folder=<id>] [--name=<text>] [--top=50]`.
  With `--folder`, the files in that folder; with `--name`, files whose name contains the text; with neither, the most recently modified files.
- **File info:** `--info --file=<id>` prints the name, type, parent folders (id and name), and link.
- **Copy:** `--copy --file=<id> --title=<new title> [--folder=<id>]` copies into `--folder`, or into the source's own folder when omitted.
- **Link sharing:** `--share-link --file=<id> --role=reader|commenter|writer` grants "anyone with the link" access and prints the link.
  Link sharing names no person, so Google sends no notification email.
- **Upload:** `--upload --path=<local file> [--folder=<id>] [--title=<name>]`.

### Slides

A deck is `--deck=<presentation id>`; a slide is picked by `--slide-title=<text>` or `--slide-index=<n>` (0-based).
A title matches the slide whose title placeholder (or, lacking one, first text box) equals the text, case-insensitively, and falls back to a slide with any text box containing it; a title matching more than one slide is an error.

- **Read a deck:** `--slides-read --deck=<id>` lists every slide with its index, id, and title, and each element's id, kind, placeholder type, text, and box (position and size in points).
  Read it first to learn the element ids the other two commands take.
- **Insert a photo:** `--slides-insert-image --deck=<id> <slide> (--image-path=<local file> | --image-file=<drive id>) [--box-shape=<element id> | --x= --y= --width= --height=]`.
  The photo is fitted inside the box with its aspect ratio kept: `--box-shape` borrows an existing element's box, explicit points set one directly, and the default is centered at 40% of the page.
  A local path is uploaded first, into `--folder` or else the deck's own folder.
  The Slides API fetches an image only from an anonymously reachable URL, so the command grants the Drive image link access for the insert and removes it right after (Slides keeps its own copy); an image that was already link-shared stays that way.
- **Set text:** `--slides-set-text --deck=<id> <slide> (--shape=<element id> | --placeholder=BODY|TITLE|SUBTITLE) (--text=<t> | --text-file=<path>) [--append]`.
  Replaces the shape's text, or with `--append` adds the text as a new paragraph after what's there.

## Auth-error handling

A "Not signed in" error, `invalid_grant`, or the account guard tripping each means that account's sign-in must be re-run; the error message carries the exact command.
If Google declines to return a refresh token, revoke prior access at `https://myaccount.google.com/permissions`, then re-run.
An "API has not been used in project" error means step 1 of Setup is still open for that client's project.
