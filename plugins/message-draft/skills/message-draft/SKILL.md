---
name: message-draft
description: Draft a message and NEVER send it. Three modes — `teams` (a Teams chat, staged in the web composer via browser-chauffeur), `slack` (a Slack message/reply, typed into the Slack composer via browser-chauffeur and left as an auto-saved draft), and `outlook` (a work-email reply or new email, created as an Outlook draft via the `ms-rest` skill's REST API). Use whenever staging a Teams chat, a Slack message, or a work Outlook email for human review. LinkedIn is never automated — hand the drafted text to the user to paste in himself. (Supersedes teams-message and slack-message.)
---

# message-draft

Stage a **draft** and stop — a human reviews and sends. Pick a mode:

- **`teams`** — a 1:1 or group Teams chat, staged in the web composer (you drive **browser-chauffeur**,
  never Playwright directly).
- **`outlook`** — a work-email reply or new email, created as an Outlook **draft** via the **`ms-rest`**
  skill's REST API (no browser composer). `ms-rest` is the WORK-account Outlook plugin; don't
  confuse it with the personal `ms-graph` skill.
- **`slack`** — a Slack DM, group DM, channel message, or threaded reply, typed into the Slack composer
  (you drive **browser-chauffeur**) and left as Slack's auto-saved draft.

**LinkedIn is never automated.** LinkedIn suspended Russell's account for automation in July 2026 —
never drive browser-chauffeur/Playwright against linkedin.com for any reason. For a LinkedIn reply,
write the message text (through the `document-authoring` voice pass below) and hand it to the user
to paste into the LinkedIn composer and send himself.

**Voice:** Before composing any message, invoke the **`document-authoring`** skill to write the
content in Russell's voice. Pass the drafted text to the mode steps below for staging.

**Voice gate (stage-time — load-bearing):** After the draft is staged, read it back from where it
lives (Teams/Slack: the composer; Outlook: `ms-rest get <draftId>`) and re-apply the **`document-authoring`**
Conversational writing rules as a review pass. If anything was trimmed, re-stage the gated version
(Teams/Slack: re-type it; Outlook: re-create the draft from the gated body). Report `voice-gate=passed` in
the done-criteria once it has run.
For Teams and Slack this read-back runs inside the browser subagent (it holds the composer), and the subagent reports `voice-gate=passed` back in its `DONE` result.

**Review gate (mint a receipt for the reviewed body):** every mode is harness-enforced through
`writing-review`'s stage gate. Compose the final body into a file, run the Verify loop against that file,
then mint its receipt (see `writing-review`'s **The stage gate** for the `verify_gate.py mint` command).
For **Outlook**, that file is the `--json` payload the create-draft / create-reply command already uses.
For **Teams** and **Slack**, the orchestrator writes the reviewed body to a `.tmp` file and mints its receipt there, then hands that `--body-file` path to the browser subagent (below).
The subagent invokes every browser-chauffeur run with `--body-file=<that file>` alongside the usual `--cdp-port`, so the gate sees the body on the command line and blocks a composer run whose body has no receipt.
Report the receipt hash in the done-criteria.

## `teams` and `slack` stage in a browser subagent

The `teams` and `slack` modes drive browser-chauffeur through a long open-tab then find-target then identity-gate then compose then verify flow.
Run that whole flow inside a browser subagent, per browser-chauffeur's **"Running in a subagent"** section - one in-process subagent for the single coherent task of staging this one message.
The driving turns stay entirely in the subagent's isolated context, and the orchestrator (the session that invoked message-draft) gets back only the result.
The `outlook` mode has no browser flow to isolate - run it inline as its mode steps describe.

**The split - what stays in the orchestrator, what goes into the subagent:**

- **Orchestrator, before it spawns the subagent:** author the body in Russell's voice (the **Voice** step), run the `document-authoring` pass, and complete the **Review gate** step (above).
  That is the human-facing writing, and it stays on the main thread.
  The receipt the Review gate mints must exist before the composer runs, so finish it before spawning the subagent.
- **Browser subagent:** the composer staging - every step of **Mode: `teams`** or **Mode: `slack`** below.
  It opens its own tab, finds and identity-gates the target, types the reviewed body read from `--body-file`, inserts links, runs the stage-time voice read-back, saves and verifies the draft, and leaves it staged.

**Every load-bearing invariant below still holds inside the subagent** - identity-gate before and during typing, never a bare Enter, preserve an existing draft rather than typing over it, content-is-ground-truth read-back.
The subagent boundary moves where the flow runs, not what it must guarantee.

**The subagent returns one of two results** (browser-chauffeur's return contract):

- **`DONE`** - the draft is staged.
  Return the recipient, the conversation the draft sits in, a screenshot of the staged composer, and the done-criteria line.
  The subagent never presses Send - message-draft stages and stops, so a `teams`/`slack` task's endpoint is a staged draft with nothing sent.
- **`HELP_NEEDED`** - a Teams or Slack sign-in wall (or other human-only gate) blocked the run.
  Return the reason and a `findTab` locator so the orchestrator can resume the same tab, and leave the tab open on the correct page.

**On `HELP_NEEDED`, the orchestrator surfaces the gate through `AskUserQuestion`** - naming the site and that the browser is open on the right tab - waits for Russell to sign in, then resumes the subagent via SendMessage.
The subagent re-finds its tab with the `findTab` locator it returned and continues from where it paused.

**Spawn brief (copy into the Agent call):**

```
Invoke the message-draft skill's <teams|slack> mode and browser-chauffeur, and stage this one message inside your own context.
Target: <recipient / conversation>.
Body: read it from <path to the reviewed --body-file>, and pass --body-file=<that file> on every browser-chauffeur run so the composer run is gated.
Links: <display text + URL, or none>.
Run the full mode flow yourself - open your own tab, find and identity-gate the target, type the reviewed body (Shift+Enter line breaks, never a bare Enter), insert links, run the stage-time voice read-back, save and verify the draft, and leave it staged.
Hold every load-bearing invariant: identity-gate before and during typing, never type over an existing draft, content-is-ground-truth read-back.
Return one of two results:
  DONE - recipient, the conversation the draft is staged in, a screenshot of the staged composer, and the done-criteria line.
    Never press Send.
  HELP_NEEDED - the gate (a Teams/Slack sign-in wall), the URL, a findTab predicate that re-finds the tab, and how far you got.
    Leave the tab open on the correct page.
Never send, and never prompt Russell directly - hand a gate back to me.
```

## Behavioral preferences

- **Prefer replying to an existing thread over composing a fresh message.** When a relevant thread
  exists, draft a reply on it rather than a new email; only compose new when there's genuinely no
  thread to reply to.
  **Slack carve-out:** Slack's Thread feature sidebars a reply away from the main conversation view,
  unlike a Teams reply or an email reply, which stay inline. In a small DM or group DM, prefer posting
  a new top-level message in the main conversation over starting a Slack Thread reply, so it's visible
  to everyone without opening a side panel. Reserve actual Slack Threads for busier channels, where
  keeping the main feed clean matters more than single-glance visibility.

## Load-bearing invariants (apply to ALL modes — violating these has typed into the WRONG place)

1. **The composer is not unique — target the `:visible` one and bind every keystroke to it.**
   Web apps keep many editors mounted (Teams keeps every recent chat's `div[data-tid="ckeditor"]`
   in the DOM). Use browser-chauffeur's element-bound composer typing (it owns the mechanics — see
   browser-chauffeur → **Composing in rich editors**); never hand-roll raw key/text events, or text
   leaks into another conversation.
2. **Identity-gate before AND during typing.** Confirm the active conversation header matches the
   intended recipient (Teams: first+last name; Outlook: the expected sender/subject) BEFORE the
   first keystroke, and re-assert before each segment. Abort on mismatch.
3. **Content is ground truth.** After typing, read the composer back and verify the expected
   greeting + body (+ links) are present. Let selector drift fail loudly, not silently.
4. **Never press a bare Enter in the composer — it sends.** Line breaks are Shift+Enter; use
   browser-chauffeur's bare-Enter-refusing composer primitives. (Enter is fine inside a link-insert
   dialog.)
5. **A composer with text already in it holds a draft — read it first, never type on top of it.**
   Before the first keystroke, read the target composer back (`el.innerText`). If it already contains
   text, that's a draft the user left in this conversation. Preserve it: surface it verbatim for
   review and clear it only with an explicit OK — typing on top would append into one garbled message,
   and clearing without asking would silently discard words the user wrote. In an autonomous run with
   no one to review, do NOT overwrite — leave the draft and flag the conversation instead. (Clearing a
   truly abandoned draft also collapses the duplicate "new chat" rail entry it spawned and unblocks
   clean future sends.)
6. **Selectors below are last-known-good (web UIs, 2026-06) — expect drift.** The invariants
   don't drift; rediscover selectors live via browser-chauffeur (screenshot → inspect) when they do.

## Mode: `teams`

**These steps run inside the browser subagent** (see **`teams` and `slack` stage in a browser subagent**).
The orchestrator has already minted the reviewed body's receipt into the `--body-file` it hands over.

Addresses either a **1:1 chat** (recipient name + email) or a **group/meeting chat** (chat name).
Inputs: the address, message body, optional hyperlinks (display text + URL), optional @mentions.

0. **Open your OWN Teams tab — never adopt an existing one.** Open a fresh `teams.microsoft.com` tab
   of your own (browser-chauffeur owns the how — see "you MUST open your own tab"); the persistent
   profile means it loads already signed in. **Do not enumerate tabs and pick "the loaded Teams tab
   with the most text"** — that tab may belong to another Claude Code instance or the user, and
   driving it types into the wrong place (Enter sends in Teams). Reuse that one tab for every later
   step of this draft. Wait for the app shell to hydrate (the chat rail) and ensure keyboard events
   land on this tab before interacting.
1. **Find the target.**
   - **1:1:** Press `Alt+Shift+N` (new message), wait ~1.5s, then click the **people-picker input**
     (`input#people-picker-input` or `input[aria-label="Enter name, chat, channel, email or tag"]`).
     ⚠️ Do NOT use `input[data-tid="AUTOSUGGEST_INPUT"]` — that is the global search bar at the
     top of the app and routes to search results, not a 1:1 chat composer. Type the recipient's
     **email** (most precise), then **poll** `[role="option"]` until an option's `innerText`
     includes the recipient's **last name** before clicking — default suggestions render first and
     open the wrong person. Name tolerance: display name may differ (`Matt` vs `Matthew`) — match
     last name + first-name prefix.
   - **Group/meeting:** Use the global search bar (`input[data-tid="AUTOSUGGEST_INPUT"]`), type the
     chat name or a distinctive substring, and poll for a chat/group option whose text contains it.
2. **Identity-gate.** Confirm the chat heading (an `h1`/`h2`/`h3` containing the name —
   `[data-tid="chat-header-title"]` does NOT exist in this build) matches before typing. 1:1: last
   name + first-name prefix; group/meeting: a case-insensitive substring of the chat name.
3. **Compose.** The composer is `div[data-tid="ckeditor"]` (target the `:visible` one). Check it for
   an existing draft per invariant 5 (Teams auto-restores a left draft here), clear it, then type the
   body; line breaks are Shift+Enter, never a bare Enter. Source the body from the reviewed `--body-file`
   and pass that flag on the browser-chauffeur run, per **Review gate** above, so the run is gated.
4. **Hyperlinks.** Ctrl+K opens an Insert-link dialog; fill `[data-tid="insertHyperlink-displayText"]`
   and `[data-tid="insertHyperlink-linkAddress"]` (⚠️ `data-tid`, not `id`), then click
   `[data-tid="insertHyperlink-insertButton"]` (⚠️ NOT `role=button` with text "Insert" — that
   selector fails). A typed GitHub repo URL also auto-unfurls a card.
5. **@mentions (when asked).** Type `@` then the first few chars of the name, wait for the mention
   autocomplete dropdown, and click the matching suggestion so a real mention chip mounts. Typing
   `@Name` as plain text does NOT notify the person — the chip must come from the dropdown.
6. **Leave as draft.** Do NOT press Enter / click Send. Teams shows a **Draft** badge per
   conversation (persists within the live browser session). Verify content, then stop.

## Mode: `slack`

**These steps run inside the browser subagent** (see **`teams` and `slack` stage in a browser subagent**).
The orchestrator has already minted the reviewed body's receipt into the `--body-file` it hands over.

Stages a draft in the **Slack composer** (web) for a DM, group DM, channel message, or threaded reply.
Slack has no draft API — typing into the composer and stopping leaves Slack's own per-conversation
auto-saved **draft**. You drive **browser-chauffeur**; never Playwright directly. This skill never sends;
sending a reviewed Slack message is the separate, gated `slack.js --send` step in the **`slack`** skill,
reserved for Russell's explicit per-message OK, parallel to how Outlook's send is `ms-rest send-draft`.

Inputs: the target (a person's name for a DM, or a channel/conversation, or — when replying to a captured
drainer item — its `channel` + `ts` + optional `threadTs` + permalink), the message body, optional links.

0. **Open your OWN Slack tab — never adopt an existing one.** Open a fresh `app.slack.com/client/<teamId>`
   tab of your own (browser-chauffeur owns the how); the persistent profile loads it already signed in. If
   a sign-in wall shows, have the user sign in, then continue. Reuse that one tab for every later step.
1. **Open the target conversation.**
   - **From a captured item** (the reliable path): open the message's **permalink** (the item's `url`) —
     it lands directly in the right DM/channel/thread. For a thread reply, the permalink opens the thread.
   - **By person/channel name:** press `Ctrl+K` (quick switcher — it searches ALL members, unlike the
     paginated `users.list` API), type the full name or channel, wait ~1.5s for autocomplete, and open the
     top match with Enter. A person's Slack handle can differ from their real name — confirm it's them.
2. **Identity-gate before typing.** Read the conversation header (`[data-qa="channel_name"]`,
   `.p-view_header__channel_title`) and confirm it matches the intended recipient/channel BEFORE the first
   keystroke. On mismatch, don't type — re-navigate. Typing into a stranger's DM is the failure to avoid.
3. **Compose.** Click the composer (`[data-qa="message_input"] .ql-editor`,
   `.ql-editor[contenteditable="true"]`, `div[role="textbox"][contenteditable="true"]` — target the
   `:visible` one), check it for an existing draft per invariant 5, clear it (Ctrl+A, Backspace), then
   type the body. Line breaks are **Shift+Enter**; a bare **Enter sends**, so never press it. (Use
   browser-chauffeur's bare-Enter-refusing composer primitives.) Source the body from the reviewed
   `--body-file` and pass that flag on the browser-chauffeur run, per **Review gate** above, so the run
   is gated.
4. **Links.** Slack renders a pasted URL as a link automatically; for anchor-text links, type the phrase
   and apply a link via the composer's link affordance (Ctrl+K inside the Slack composer opens its
   link dialog — distinct from the quick switcher, which is Ctrl+K when no composer is focused).
5. **Save the draft, then leave it.** Do NOT press Enter / click Send.
   A Slack composer draft is saved only once the composer loses focus and Slack's save request fires, so typing and then immediately navigating to another conversation (or ending the script) discards it before it ever saves.
   After typing, blur the composer (`el.blur()`) and let the save settle (`page.waitForLoadState('networkidle')`) before moving to the next conversation or finishing.
   Verify it saved the hard way: reload the page and confirm the body is still in the composer.
   A draft that survives a reload is saved server-side, so it syncs to the user's own Slack - the sidebar shows a **Draft** badge on the conversation and it appears under **Drafts & sent**, which is where they review and send it.
   A draft that is empty after the reload never saved; re-type it, blur, and re-verify.
   When staging several drafts in one run, save-and-verify each one before opening the next, since a not-yet-saved draft is the one a navigation silently drops.

## Mode: `outlook`

Creates an Outlook **draft** for the WORK account via the **`ms-rest`** skill's REST API — no browser
composer. The draft lands un-sent in **Drafts** (reviewable on web and mobile) with clickable links;
for a reply, the quoted original sits below the new text. This skill never sends — `ms-rest send-draft`
is gated separately and reserved for Russell's explicit OK.

Two shapes:
- **Reply** to an existing message — needs that message's Outlook REST `id`.
- **New email** — needs subject + recipients.

`ms-rest` ships inside its plugin's versioned cache, not in any repo's `.claude/`. Address its script
through the cache glob (run the newest if several are cached) from the repo root:
`node ~/.claude/plugins/cache/*/ms-rest/*/skills/ms-rest/outlook-mail.js <verb>`.

1. **Auth-glance.** `node ~/.claude/plugins/cache/*/ms-rest/*/skills/ms-rest/outlook-mail.js token`. `Token OK ✅` → ready. If it
   reports no token, follow `ms-rest` AUTH-GLANCE (sign in to Outlook web in the CDP browser once),
   then retry. Never surface a token error — fix it and continue.
2. **Resolve the target (reply only).** If you were handed the source `messageId` (e.g. from the
   channel-watch CAPTURE record), use it. Otherwise locate the message with
   `outlook-mail.js enumerate` (and `get <id>` to confirm), and **identity-gate**: the message's
   sender + subject must match the intended target. Abort on mismatch.
3. **Write the body to a file.** Author the body in Russell's voice (the **Voice** step above), as
   **HTML** with real `<a href="URL">anchor text</a>` links (never a bare URL in prose). Write it to
   a temp file, e.g. `.tmp/outlook-draft.json`:
   - Reply: `{ "comment": "<p>…new reply text…</p>" }`
   - New email: `{ "subject": "…", "body": "<p>…</p>", "to": ["a@b.com"], "cc": [] }`
4. **Create the draft.**
   - Reply: `node ~/.claude/plugins/cache/*/ms-rest/*/skills/ms-rest/outlook-mail.js create-reply <messageId> --json .tmp/outlook-draft.json`
   - New: `node ~/.claude/plugins/cache/*/ms-rest/*/skills/ms-rest/outlook-mail.js create-draft --json .tmp/outlook-draft.json`
   It prints `{ draftId, webLink, folder:"Drafts", sent:false }`.
5. **Voice gate + read-back.** `node ~/.claude/plugins/cache/*/ms-rest/*/skills/ms-rest/outlook-mail.js get <draftId>` and confirm
   the body reads back correctly (greeting + body + clickable links; for a reply, the quoted original
   below the new text). Re-apply the `message-rules` Conversational rules; if anything was
   trimmed, rewrite the body file and re-run step 4 (the new draft supersedes the old — delete the
   stale one with `delete <draftId>` if needed).
6. **Leave as draft.** Never send. Report where the draft lives (Drafts; `webLink` is the deep link).

## Done-criteria (report this back)

`mode=<teams|slack|outlook> recipient=<who> drafted=true sent=false voice-gate=passed links=<n>` plus a
one-line note of where the draft lives (Outlook: the `webLink`/draftId; Teams/Slack: the conversation the
draft is staged in). If identity-gate failed or sign-in was needed, say so and that nothing was staged.
For **Teams** and **Slack**, this line is what the browser subagent returns in its `DONE` result.
The orchestrator relays it.
For a LinkedIn reply, report `mode=linkedin drafted=true staged_for_manual_send=true` and give the user
the composed text to paste in himself.
