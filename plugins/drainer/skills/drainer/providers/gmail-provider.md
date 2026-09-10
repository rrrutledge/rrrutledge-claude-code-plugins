# gmail provider — personal/Workspace Gmail (Gmail REST API over OAuth)

A provider for a **Gmail or Google Workspace** mailbox read and cleared through the **Gmail REST API**
over **OAuth**. All API calls go through the **`gmail`** skill's `gmail.js` (don't reimplement them here);
it owns the OAuth client, the message/draft/label operations, and the Drafts + All Mail mechanics.
Implements `../engine/provider.md`; classify by `../engine/triage.md`.
id prefix: `gmail-`; body file: `<id>.email.md`.

> Two-file provider: the **reading** mechanics (enumerate, stable id, capture-writing) live in the
> sibling **`gmail-adapter.py`** that the poller drives. This doc is the **worker-facing** prose —
> AUTH-GLANCE, the captured item shape, CLEAR, JUNK-LEARNING, DRAFT-MODE.

> This is the Gmail-REST counterpart to the Graph-based `outlook-graph-provider.md`. Use it for a Google
> account with the gmail skill's one-time OAuth sign-in completed.

**Shared email rules:** See `email-base.md` for CAPTURE shape, SITUATIONAL-CHECK decision logic,
DRAFT-MODE voice rules, and JUNK-LEARNING priority order. This file covers only the Gmail-specific
mechanisms.

## Config (in `.claude/drainer.local.md` → `providers.gmail`)
No config — auth is the gmail skill's cached OAuth token, minted once via its sign-in. The only env vars
are the OAuth client id/secret (`GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`); the token itself
lives at `~/.claude/gmail/oauth-token.json` and auto-refreshes. Optionally `GMAIL_SIGNATURE_HTML` (an
HTML snippet) — when set, `gmail.js` appends it to every staged draft automatically.

The `gmail` `gmail.js` lives at `<gmail-skill>/scripts/gmail.js` — run it with `node`.

## AUTH-GLANCE
Run `node gmail.js --check`. If it prints "Signed in as … Inbox has N message(s)." you're connected. If
it errors with `insufficient permission` / `invalid_grant` / "Not signed in", the cached token lost its
scopes or was revoked — re-run the gmail skill's one-time sign-in (`node gmail-auth.js` via
browser-chauffeur), then retry. Never surface a raw auth error to the user.

## SITUATIONAL-CHECK mechanism
Use `mcp__claude_ai_Gmail__search_threads` with `subject:"<subject>"` (no `in:` filter) — this covers
All Mail in both directions. Read newest-first. CLEAR archives to All Mail (not Trash), so any reply
the contact sent after capture is always findable here. Also run `node gmail.js --list-drafts` to check
for an existing draft before staging a new one.

## CAPTURE
See `email-base.md` for the shared two-file shape. Gmail-specific: `messageId` is the RFC822
Message-ID header (with angle brackets). The `url` opens the message in Gmail by that id.

## CLEAR
`node gmail.js --archive=<messageId>` — removes the message from the inbox and keeps it in **All Mail**
(reversible; narrate it). Archiving, not trashing, is the drainer's clear: a drained item has
been dealt with, not discarded. The poller also calls this (via the adapter's `clear`) to archive an
fyi/junk message the moment it's triaged, so it leaves the inbox without waiting for the digest; the
digest's own CLEAR on approval then re-archives it, a harmless no-op.

`--reply` looks the original up across All Mail, so it can thread and quote whether the
message is still in the inbox or has already been archived — archive order relative to drafting no longer
matters.

## JUNK-LEARNING (the first-reach rule — Gmail-specific)
The first-reach stop (per `email-base.md`'s rule-first order, including its show-literal-rule gate): a
**Gmail filter**. A Gmail filter is a raw search query, and a bare quoted phrase in it matches the
**whole message, body included** (`--add-body`) — so body matching is fully supported. Wrapping a phrase
in `subject:(…)` (`--add-subject`) is the *fence* that scopes it to the subject, which you use to keep a
phrase that also appears in wanted mail (a generic footer line) from triggering. So scope to the subject
when the phrase isn't distinctive, and match the body when it is. Use `from:X subject:Y` for
company-specific noise. Once Russell has OK'd the shown rule, append it with the **`gmail`** skill's
`filters.js` (same OAuth token, settings scope):
`node filters.js --append-filter=<id> --add-subject='"<phrase>"'` for a subject phrase, or `--add-body`
for a body phrase — `--list-filters` first to find the bucket's id. Create a new bucket with
`--create-filter --query='…' --archive --mark-read` only for a genuinely new mechanism.

## REPORT-PHISHING (not yet available - design follow-up)
Gmail has no report-phishing command yet, so a `kind: phishing` Gmail item falls back to the ordinary
junk stop at digest time (a filter, per JUNK-LEARNING above) and the digest notes that reporting isn't
available for this source. The intended action is a new `gmail.js --report-spam=<messageId>` that adds
the `SPAM` label (`messages.modify` addLabelIds SPAM): Gmail treats a move into Spam as a spam report and
retrains its filter, and it's reversible from the Spam folder — the REST equivalent of Outlook's
`--report-phish`. Build it in the `gmail` skill, then wire it here the same way the outlook-graph provider
wires `--report-phish`.

## DRAFT-MODE CLI commands
Follow all voice and reply-vs-fresh rules in `email-base.md`, then use these Gmail commands.

**When `GMAIL_SIGNATURE_HTML` is set (see the `gmail` skill's Setup step 5), the `--body-file`
content must end on its last substantive sentence — never add a name/sign-off line of your own
(`Russ`, `Thanks, Russ`, a full name/title/org block).** The auto-appended signature already names
the sender, so writing one in too duplicates it — the `gmail` skill's own docs flag this as the
single most common mistake when staging a draft here. Check the last line of every `--body-file`
against this before running `--reply`/`--draft-new`, even when mimicking the signature style of a
quoted message earlier in the thread.

- **Reply-all on the thread:** `node gmail.js --reply --message-id=<messageId> --body-file=<file>`
  — sets In-Reply-To/References, appends the quoted original, and CCs all original To+CC recipients.
  Thread off the **most recent message** (see `email-base.md`). Pass the latest message-id even
  when that message is one the user sent; `--reply` searches All Mail so no inbox restore is needed.
- **Fresh note:** `node gmail.js --draft-new --to="<addr>" --subject="<subj>" --body-file=<file>
  [--cc="<addrs>"]`
