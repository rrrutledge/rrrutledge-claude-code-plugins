---
name: browser-chauffeur
description: |-
  ⚠️ CRITICAL: NEVER use Playwright MCP tools directly (mcp__playwright__*). ALWAYS invoke this skill instead.
  
  This skill is REQUIRED for ALL browser automation — screenshots, form filling, navigation, data extraction, verification, etc. Playwright MCP requires re-authentication every time and lacks the error recovery, quality patterns, and session persistence that this skill provides.
  
  Use browser-chauffeur when:
  - Taking screenshots of websites
  - Navigating to, checking, or accessing ANY website (admin panels, dashboards, web apps, docs sites, etc.)
  - Looking up information on a website
  - Verifying something on a website (user access, settings, configurations, status, etc.)
  - Logging into a website or checking authentication status
  - Creating or running browser automation scripts
  - Filling forms, clicking buttons, or any website interaction
  - You determine that visiting a website would help answer the user's question or complete their task
  
  This skill handles persistent browser launch, login session reuse across tasks, CDP setup, autonomous error recovery, diagnostic screenshots, and script quality enforcement. The persistent browser preserves logins — users authenticate once, and all future tasks reuse that session.
  
  Examples that REQUIRE this skill:
  - User asks: "Take a screenshot of the admin page"
  - User asks: "Check if Jim has access to the dev environment in Okta admin"
  - User asks: "What's the latest version number?" → You determine checking the docs site would answer this
  - User asks: "Is the API key configured correctly?" → Navigate to settings page to verify
  - User asks: "Create a script to automate form submission"
  
  DO NOT use Playwright MCP (mcp__playwright__browser_navigate, browser_snapshot, etc.) — always invoke this skill instead.
allowed-tools: Bash, Write, Edit, Read
---

# Browser Chauffeur

You are operating the browser on the user's behalf. You are the chauffeur — you drive, they direct. Your job is to reach the destination reliably, adapt when the route changes, and never give up without trying an alternate route first.

## How it works

Every task uses the same flow: ensure a persistent Edge or Chrome browser is running with CDP enabled, validate that the target page loads, write or run a Node.js script that connects via `playwright.chromium.connectOverCDP(...)`, watch the output, and recover autonomously if anything fails. The persistent browser keeps its profile across tasks, so logins and sessions survive — the user signs in once, and all subsequent tasks reuse those sessions.

## Two usage modes

**One-off or exploratory tasks** — Browser-chauffeur executes the task directly. It generates an ad-hoc script in `.tmp/`, runs it, watches for errors, and recovers autonomously. The script is disposable — it lives in `.tmp/` and is not committed anywhere.

**Recurring automation** — Create an instruction-driven spec: a SKILL.md file that documents the business rules, invariants, selectors (as last-known-good hints), and safety rails. Each time the skill is invoked, browser-chauffeur generates a fresh ad-hoc script in `.tmp/` from those instructions. This is more resilient than a committed script because selectors that drift get fixed in the spec, not by patching a brittle committed file. See `teams-message` as an example. **Do not commit browser automation scripts** — commit the spec (SKILL.md), not the generated scripts.

## Running in a subagent (keep the driving turns out of the main thread)

A browser run is long: dozens to hundreds of screenshot then decide then click turns.
Every one of those turns re-reads the whole growing conversation, and each screenshot or DOM read is used once to pick the next click and never referenced again.
So when a live session (an orchestrator working with the user, a skill like message-draft or apply-for-job) needs browser work, run that work inside a subagent.
The subagent's isolated context absorbs the whole driving conversation, and the orchestrator gets back only the result.

**Spawn one subagent per coherent browser task, and let it run the entire Phase 0-5 loop inside its own context.**
A "task" is a whole flow (stage this message, submit this application, extract this value), not a single step.
Splitting one flow across several subagents buys almost nothing (a screenshot bills about 1,600 tokens regardless of size) and costs real re-orientation, since each fresh subagent has to re-read the page to learn where it is.
Keep the loop together; split only at the seams below.

**Default to an in-process subagent (the Agent tool), not a launched session.**
It runs in the orchestrator's own process with a separate context window, executes in the background, and can be continued later via SendMessage with its context intact.
Because it shares the orchestrator's owner PID, any tab it opens stays owned by the still-alive orchestrator session, so the tab survives a pause at a human gate (see the help-channel below).
Reserve a launched session (a separate terminal tab) for fan-out: many independent browser tasks with no shared login and no mid-run gate, the way the drainer runs one headless worker per inbox item.

### The return contract

A browser subagent ends by returning one of two structured results, and it never prompts the user itself.

- **`DONE`** - the task finished.
  Return the outcome (pass/fail), any values quoted from the page, a final screenshot path, and, when the task's endpoint is an irreversible action, the flow staged up to but not including the Send/Submit/Pay/Confirm button, with a screenshot of that staged state.
- **`HELP_NEEDED`** - the run reached a gate only the user can clear: a login wall, a CAPTCHA, MFA to a phone or authenticator app, an unresolvable consent wall, or an in-page action that cannot be done headless.
  Return the reason, the URL and a stable tab locator (the `findTab` predicate, so the same tab is re-findable), exactly what the user must do, and how far the run got.
  Leave the tab open on the correct page.

The subagent handles an email-delivered MFA code itself: it fetches the code from the connected mailbox and continues, exactly as **User Intervention** below prescribes.

### The help-channel

On `HELP_NEEDED`, the orchestrator (the live thread with the user) surfaces the request through `AskUserQuestion`, the same visible, blocking prompt this skill already uses, naming the site, the gate, and that the browser window is open on the right tab.
The user acts directly in the persistent browser.
When they confirm, the orchestrator continues the subagent via SendMessage; the subagent re-finds its tab with the `findTab` predicate it returned, re-orients with a fresh read to confirm it is past the gate, and resumes from Phase 2.
If the subagent cannot be continued, the orchestrator spawns a fresh continuation subagent seeded with "the login is done; resume the flow at step N on the tab matching <predicate>", and the persistent tab makes either path work.

**The subagent stages up to the final button and hands the staged state back; it never presses Send/Submit/Pay/Confirm.**
The orchestrator surfaces the staged result, and the user presses it.
The irreversible final act stays on the user's desk, whether or not the driving conversation runs in a subagent.

### When one task runs very long

If a single task's own context grows large (a genuinely hundreds-of-turns flow), the subagent re-hands-off to a continuation subagent at a clean page boundary, the same way a long session re-hands-off at a phase boundary.
Human gates are already natural seams, since the run pauses there anyway.

### Spawn brief (copy this into the Agent call)

```
Invoke the browser-chauffeur skill and run this one browser task inside your own context: <task>.
Run the full Phase 0-5 loop yourself - launch/reuse the browser, orient, execute, verify, recover.
Return one of two results:
  DONE - outcome, any values quoted from the page, a final screenshot path, and (if the endpoint is irreversible) the flow staged up to but NOT pressing the Send/Submit/Pay/Confirm button, with a screenshot of that staged state.
  HELP_NEEDED - reason (login / CAPTCHA / MFA-to-phone / in-page action that can't be done headless), the URL, a findTab predicate that re-finds the tab, what the user must do, and how far you got. Leave the tab open on the correct page.
Never press an irreversible button, and never prompt the user directly - hand the gate or the staged step back to me.
```

### When to run inline instead

Run browser-chauffeur directly in the current context for a quick, bounded check (a single screenshot, one status read) where spawning a subagent costs more than it saves, and when you are already inside a subagent (do not nest another).

## Prerequisite Check

Verify Node.js is available and run the setup script to ensure `playwright-core` and the helpers shim are installed:

```bash
node --version && node plugins/browser-chauffeur/skills/browser-chauffeur/templates/setup.js
```

The setup script installs `playwright-core` to `~/.claude/browser-chauffeur/` if it is not already accessible, and writes a `browser-chauffeur-helpers` shim there so all scripts can find their dependencies via a fallback path — no manual `npm install` or `npm link` required. If the user already has `playwright-core` in their project `node_modules`, setup detects that and skips the install.

Adjust `plugins/browser-chauffeur/...` to the actual path where the skill is mounted if needed.

**Every ad-hoc script goes in the project's `.tmp/` — never in `~/.claude/browser-chauffeur/` itself.** That shared directory only exists to hold the fallback `node_modules`; it is not a script workspace, and dropping scripts there breaks the calling project's permission-auto-approval (which is scoped to the project directory) and clutters a directory other sessions share. Because Node's module resolution walks up from the *requiring file's own directory* — not from the current working directory — a script sitting in `.tmp/` cannot resolve a bare `require('browser-chauffeur-helpers')` or `require('playwright-core')` against the fallback install; there is no `node_modules` between `.tmp/` and the shared directory for Node to find. **Always use the try/catch fallback pattern from `templates/script-template.js`** (never a bare `require(...)`), which resolves from the project's own `node_modules` first and falls back to the shared install:

```js
const { chromium } = (() => {
  try { return require('playwright-core'); }
  catch { return require(require('path').join(require('os').homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'playwright-core')); }
})();
const { openTab, findTab, closeTab } = (() => {
  try { return require('browser-chauffeur-helpers'); }
  catch { return require(require('path').join(require('os').homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'browser-chauffeur-helpers')); }
})();
```

---

## Phase 0: Browser Launch (do this before Phase 1)

**Reuse the persistent browser if one is already running.** A single dedicated browser stays open across tasks — each task gets its own tab, and logins survive between tasks. This is separate from the user's personal browser so their tabs are never disturbed.

**Step 1 — Ensure the persistent browser is running**

```bash
python plugins/browser-chauffeur/skills/browser-chauffeur/templates/chauffeur.py
```

Adjust the path to wherever your skill is mounted. This auto-detects Edge first (better Windows SSO integration), falls back to Chrome, and manages port selection and profile automatically. If a persistent browser is already running, it prints the existing connection info and exits immediately. If not, it launches a new one with a persistent profile at `~/.claude/browser-chauffeur/profile/`. The state is stored globally at `~/.claude/browser-chauffeur/state.json` so all Claude instances can discover and reuse the same browser.

**Automatic tab sweep.** When reusing an existing browser, this script keeps the tab count low. Reclaiming is driven by idleness; ownership just lets it clean up promptly. Three layers: (1) **owner reap** — a tab whose owning Claude session has ended is closed right away. Each tab is tied to the session that opened it (its `CLAUDE_PID`), not the short-lived script (a session fires many scripts), so a tab stays alive as long as its session runs and is cleaned up when that session ends. (2) **Idle age-out** — any tab idle longer than the TTL (default 12h, override with `BROWSER_CHAUFFEUR_TAB_TTL`) is closed, regardless of owner, catching genuinely abandoned tabs. (3) **Count cap** — if more than `MAX_TABS` (default 15, override with `BROWSER_CHAUFFEUR_MAX_TABS`) remain, close the least-recently-active until back under it. Activity is tracked automatically — a tab created or reused via `openTab`/`findTab`, or one whose URL/title the sweep sees change between runs, counts as recently active, so an in-use tab is never the one closed. It never closes the browser's last page. Layers 2–3 also cover tabs opened without the `openTab` helper (which have no owner to reap). This keeps `connectOverCDP` fast and reliable and stops the browser accumulating enough tabs to crash (see **Resilient Connection**). It's automatic — you don't invoke it directly.

**Tying tab ownership to a session.** A tab's owner is the Claude session that opened it, identified by `CLAUDE_PID` - the session's own claude process, which Claude Code injects into every tool subprocess. Its key property is that it is the same pid across all of a session's tool calls (each tool call runs in its own short-lived process, so the opening script's `process.pid` would differ call to call), and it lives exactly as long as the session. So a tab is reclaimed precisely when its session ends, with nothing to set up. This works the same for every kind of session - a launched handoff tab, a headless `--bg` drainer worker, or a `claude` you start in a terminal yourself. Only when `CLAUDE_PID` is absent (something ran outside a Claude session) does ownership fall back to the short-lived node script, so that tab is reclaimed shortly after the opening script exits rather than at session end.

**Save the PORT from the output** — you'll pass it to scripts via `--cdp-port`. The PID and PROFILE_DIR are printed for diagnostics but you don't need to track them — the browser stays running and the profile persists.

If the script reports "Reusing existing browser," CDP is already responding — skip the wait. If it launched a fresh browser, wait 3–5s then verify:
```bash
curl -s http://localhost:<port>/json/version
```

**Known quirk — Edge sync dialog:** The "We are now syncing your browsing data" dialog on first launch is rendered in Edge's browser chrome layer, outside the page DOM. Playwright cannot see or dismiss it. It does **not** block script execution — scripts can interact with page elements behind it. Do not waste time trying to close it. This only appears once since the profile persists.

**Known quirk — Edge welcome popup window:** On the very first launch, Edge may spawn a small "welcome" popup as a separate browser window. This appears as a separate CDP context and may sort before the main maximized window in `browser.contexts()`. The templates handle this by selecting the context that already has a real `http`/`https` page, falling back to `contexts()[0]`. This only happens once per profile.

**First-run overlays:** On the first launch, the profile may show in-page overlays (cookie consent banners, "What's new" modals). These DO block element waits — dismiss them via the pattern in **Common Patterns → Overlay Dismissal** below. Since the profile persists, dismissed overlays stay dismissed.

**Step 2 — Snapshot the target**

Confirm the browser can actually reach the target app, not an expired-session login wall or SSO challenge.

```bash
node plugins/browser-chauffeur/skills/browser-chauffeur/templates/snapshot-target.js --cdp-port=<port> --url=<target-url>
```

The script navigates, waits for URL stability (handles transient SSO redirects like Okta's "Verifying your identity" page), waits for network idle and DOM stability, then saves a screenshot. It outputs:

```
SNAPSHOT_READY
SCREENSHOT: .tmp/snapshot-target-<timestamp>.png
FINAL_URL: <where the browser actually landed>
```

**The script makes no decision — you do.** Always:

1. **Read the screenshot** with the Read tool.
2. **Decide visually**:
   - **Logged in** — app shell visible, real content, no login form or sign-up wall → record the CDP port and proceed with the next script.
   - **Not logged in** — a login form is visible, a wall like "Sign up to see this board" / "Sign in to access X", or the `FINAL_URL` is on an auth provider (e.g. `*.okta.com`, `login.microsoftonline.com`, `accounts.google.com`, `id.atlassian.com`) → use `AskUserQuestion` immediately to prompt the user to sign in. The page is left open. Re-run `snapshot-target.js` once they confirm. Once they sign in, the persistent profile preserves the session for all future tasks.
   - **Ambiguous** (loading spinner, blank page) — re-run `snapshot-target.js` once more; if still ambiguous, prompt the user via `AskUserQuestion` to confirm.

**Never try to detect login state from script output** (text matching, DOM selectors, URL heuristics). Always inspect the screenshot with the Read tool. See HELPERS.md → Login detection for why.

**For slow-hydrating SPAs** where the screenshot-stability loop times out before the app is ready, re-run with `--target-anchor=<css-selector>` pointing at a known app-shell element. Example:

```bash
--target-anchor='[data-app-section="CalendarModuleSurface"]'   # Outlook calendar
```

**The CDP port you pass to `snapshot-target.js` is the same one you pass to every subsequent script.** Scripts don't repeat browser detection or login checks — that's your job during Phase 0.

---

## Phase 0.5: Running Scripts From Other Skills

**Never run a browser automation script directly and walk away — always have this skill loaded so the recovery loop is active.** When another skill provides a pre-written script (from its own `scripts/` directory), invoke browser-chauffeur first to get a validated CDP port, then run the script through it. Browser-chauffeur handles error detection, recovery, and re-runs — the other skill's script provides the directions, this skill handles execution.

The pattern:

1. **Invoke this skill** before running the script
2. **Complete Phase 0** to get a validated CDP port
3. **Run the script** via Bash: `node scripts/<task>.js --cdp-port=<port>`
4. **ALWAYS do Phase 4 analysis** — parse the output for errors regardless of exit code
5. **If errors detected** — autonomously enter the Phase 4 recovery loop (diagnose, fix, re-run)
6. **If 100% success** — report results. Scripts automatically close their own tab in the `finally` block; the persistent browser stays running for future tasks.
7. **If recovery exhausted** — show diagnostics and ask for help

**Critical:** Exit code 0 ≠ success. Many scripts complete with errors in their output. Phase 4 analysis is MANDATORY after every script run, not optional.

**Other skills should reference this skill minimally.** For example, a skill that extracts data from a web app should say: "Invoke the browser-chauffeur skill to run `node scripts/<task-name>.js`" — nothing more. Browser-chauffeur handles all error detection, recovery, and reporting automatically.

This ensures the AI is always watching the road, not just handing off directions and hoping for the best.

---

## Resilient Connection

`chromium.connectOverCDP(...)` auto-attaches to **every** open CDP target to build its context/page tree. On the persistent profile — which accumulates tabs across sessions — a large target set, or a **single wedged renderer**, can make that handshake hang **indefinitely**. Playwright's own `{ timeout }` option does not help: it bounds only the socket connect, not the post-connect target enumeration.

Two layers keep this reliable:

1. **Root cause — keep the tab count low.** Scripts create every tab with `openTab` (from `browser-chauffeur-helpers`), which registers it against the owning Claude session, and `closeTab` unregisters on clean close; `chauffeur.py` reclaims tabs whose owning session has ended on every reuse. A tab opened without `openTab` is never registered, so it isn't tied to a session — which is why `openTab` is the only sanctioned way to open a tab (see Phase 1). As a backstop for any tab that outlives its session or slips through unregistered, the same sweep ages out idle tabs past the TTL and enforces a hard ceiling on total tabs (evicting the least-recently-active). So a healthy profile stays small and `connectOverCDP` stays fast — this is what makes it reliable, not just recoverable.

2. **Backstop — fail fast, never hang.** `connectBrowser()` in `script-template.js` races `connectOverCDP` against a 30s hard timeout (`Promise.race`), so if the profile is still wedged the script gets an actionable error instead of hanging forever. Every ad-hoc script you generate must use this hardened `connectBrowser()` — never call `chromium.connectOverCDP(...)` bare.

**If the connect times out anyway:** re-run `chauffeur.py` (it sweeps orphans), then retry the script once. If it still times out, the overload is from tabs the sweep won't touch — an active session's tabs or the user's own. **Do not reset the shared browser to fix your own connect** — `cleanup-browser.py --reset` kills the browser for every concurrent session and wipes all logins. Instead, surface the situation to the user via `AskUserQuestion` and let them decide whether to close tabs or reset.

---

## Phase 1: Orient

Before touching anything:

1. **Check the multi-tab environment** — list all open tabs to understand what's already running:
   ```javascript
   const pages = context.pages();
   console.log('Current tabs:');
   pages.forEach((p, i) => console.log(`  Tab ${i}: ${p.url()}`));
   ```
   
2. **Get and save your tab reference** — target the specific tab you need by URL pattern, never by position:
   ```javascript
   const { findTab, openTab } = require('browser-chauffeur-helpers'); // resolve via the try/catch pattern in Prerequisite Check / templates/script-template.js

   // Reuse YOUR OWN session's tab by URL — findTab returns only a tab this
   // session opened, and marks it active so a tab you keep returning to isn't
   // reaped as idle. A tab another session (or the user) opened is never
   // returned, so you can't grab and clobber someone else's work.
   let myTab = await findTab(context, p => p.url().includes('example.com/my-section'));

   // Own no matching tab yet → findTab returned null → open your own. openTab
   // creates AND registers it in one step (so the orphan sweep can reclaim it if
   // this script crashes).
   if (!myTab) {
     myTab = await openTab(context, 'https://example.com/my-section');
   }

   // Save this reference - it stays valid even if other tabs open/close
   ```
   **Why:** Tab positions shift as tabs are created/closed. Other Claude sessions may be working in parallel — possibly on the very same URL. Targeting by index is unreliable, and a bare `context.pages().find(...)` would hand you whichever session's tab matched first, so you could navigate another session's tab out from under it. **Get a tab only two ways: `openTab(context, url)` for a fresh one you own, or `findTab(context, predicate)` to reuse one you already own** — never a bare `pages().find(...)`. `openTab` bundles registration so the tab is tracked and reclaimable (see **Resilient Connection**) and navigates for you; `findTab` is owner-scoped so it can only ever return your own tab. Save the page object reference and reuse it throughout the script. Tabs you *found* (didn't create) are not yours — don't register or close them.

   **Never open a tab with `context.newPage()` or by calling `page.goto()` on a fresh page you created yourself** — an unregistered tab escapes the orphan sweep entirely, so it lingers until the TTL/count backstop reaps it or the browser crashes under the accumulated count. `openTab` is the only sanctioned way to create a tab.

   **Tabs a click spawns are owned automatically.** When a page you got from `openTab`/`findTab` opens a new tab itself — a `target="_blank"` link, `window.open`, a ctrl-click — that tab is registered under your session with no extra work, because `openTab`/`findTab` attach a page-scoped `popup` listener. So a click-spawned tab is a first-class owned tab: owner-reap and `--close-owned` handle it, not just the slow age/count backstop. If you want to *drive* that new tab, capture it with `const popup = await page.waitForEvent('popup')` around the click; you don't need to register it yourself.

3. Navigate to the target page or state (if not already there)
4. Read page state with `myTab.evaluate(() => document.body.innerText)`, or enumerate `myTab.frames()` if the body is unexpectedly short (see **Iframe detection** below)
5. If the text content doesn't clarify structure (complex visual layout), take a screenshot with `myTab.screenshot()` and read the image with the Read tool
6. Identify the first action needed

**Never act before orienting.** You cannot guess selectors on a page you haven't inspected.

### Iframe detection (critical for SPAs)

Many enterprise apps (Teams, SharePoint) render content in iframes. If `document.body.innerText` returns almost nothing (< 200 chars) on a page that should have content:

```javascript
const frames = page.frames();
console.log('Frames:', frames.map(f => f.url()));
for (const f of frames) {
  const text = await f.evaluate(() => document.body.innerText).catch(() => '');
  if (text.length > 200) console.log('Frame', f.url(), text.slice(0, 500));
}
```

Find the frame with the real content and target all locators at that frame object instead of the page. Note: `frame.route()` does not exist — routing only works on the page object.

### Out-of-process iframes (cross-origin, site isolation)

When `frameLocator(...).locator(...)` times out at 30s on a page with nested iframes — common in SCORM players, Workday Learning, SuccessFactors, and other LMS wrappers — the iframe is probably **out-of-process**. Chrome's site isolation runs each cross-origin iframe as a separate OS process with its own CDP target. Playwright's `connectOverCDP` connects only to the page target; the iframe's target is independent.

**How to detect:** `page.frames()` shows the iframe with an empty URL, or `page.accessibility.snapshot()` returns only the parent frame's tree without the iframe's content.

**How to attach directly:**

```javascript
const { CDPSession, httpJson, evalIn } = require('./templates/cdp-session');

// 1. List all CDP targets — look for type=iframe
const targets = await httpJson(`http://localhost:${cdpPort}/json`);
const iframeTarget = targets.find(t => t.type === 'iframe' && t.url.includes('target-domain.com'));

// 2. Open a direct WebSocket to the iframe's debugger URL
const session = new CDPSession(iframeTarget.webSocketDebuggerUrl);
await session.ready;

// 3. Collect execution contexts (one per same-origin nested frame inside the iframe)
const ctxs = [];
session.on(m => { if (m.method === 'Runtime.executionContextCreated') ctxs.push(m.params.context); });
await session.send('Runtime.enable');
await new Promise(r => setTimeout(r, 1500)); // let existing contexts arrive

// 4. Find the context you want (filter by origin) and evaluate in it
const ctxId = ctxs.find(c => c.origin.includes('target-domain.com'))?.id;
const title  = await evalIn(session, ctxId, 'document.title');
```

`playwright-core/lib/utilsBundle` exports the `ws` package, so no extra `npm install` is needed.

**How to click in the iframe — always use `Runtime.evaluate`, never OS coordinates:**

```javascript
// ✅ By known ID (if non-empty — see Anti-Pattern 8)
await evalIn(session, ctxId, `document.getElementById(${JSON.stringify(buttonId)}).click()`);

// ✅ By visible text (preferred for popups/dialogs where IDs may be empty)
await evalIn(session, ctxId, `
  ([...document.querySelectorAll('button')]
    .find(b => /^Continue$/i.test(b.textContent.trim())))
  .click()
`);
```

See **Anti-Patterns → OS Coordinates Don't Reach Cross-Origin Iframe Content** and **Tab Key Is Unreliable Across Cross-Origin Iframe Boundaries** for the failure modes this replaces.

---

## Phase 2: Step-by-Step Execution

For each step in the desired flow:

1. **Interact** — use the saved tab reference from Phase 1 with Playwright locators (`myTab.getByRole(...)`, `frame.locator('[aria-label="..."]')`). The page object stays valid even if other tabs open/close. If the tab itself was closed, Playwright will throw an error automatically.

2. **Screenshot after dropdown/menu clicks** — **CRITICAL:** After clicking any button that should reveal a dropdown menu, context menu, or modal dialog:
   - Wait briefly (500ms-1s) for the UI to appear
   - Take a screenshot with `myTab.screenshot({ path: '.tmp/diag-after-click.png' })`
   - Read the screenshot with the Read tool to SEE what menu items are actually visible
   - Query the DOM for menu items AFTER you've visually confirmed the menu is open

   **Why:** DOM queries alone can miss visual menus. The screenshot shows you exactly what the user would see, including menu items, their text, and layout. This prevents blindly clicking buttons and querying for items that may not be rendered yet or may be in unexpected locations.

3. **Verify** — re-read page state immediately after. Confirm the expected change happened: new element appeared, field value set, URL changed, success message shown. If the page state after verification is unexpected (CAPTCHA, login page, error page, or an unrecognized screen), treat it as a blocker — if it requires human action (CAPTCHA, login), follow the **User Intervention** section; otherwise apply Phase 4 recovery.

4. **If blocked** — re-read page state immediately to diagnose. Common blockers:
   - Modal or overlay in front of the target → dismiss it first (see **Overlay Dismissal**), then retry
   - Element not yet rendered → wait with `locator.waitFor()` or `myTab.waitForSelector()`
   - Stale locator (element re-rendered since last query) → re-query for a fresh handle

5. **If layout-dependent** — take a screenshot and read it with the Read tool to see visual context

6. Move to the next step **only after** the current step is confirmed

### Lazy-loaded content (scroll-and-stabilize)

Some SPAs (Articulate Rise, OpenSesame, content platforms) load page sections as the user scrolls. A "Continue" button may be absent or disabled until the full lesson content has rendered. Call the `scrollAndStabilize(frame)` helper from `templates/scroll-stabilize.js` before looking for the advance button.

---

## Phase 3: Wrap Up

1. Take a final read confirming the full flow succeeded

2. **Tab cleanup** — close only tabs you created, using the paired `openTab`/`closeTab` helpers:
   ```javascript
   const { openTab, closeTab } = require('browser-chauffeur-helpers'); // resolve via the try/catch pattern in Prerequisite Check / templates/script-template.js
   const myTab = await openTab(context, 'https://example.com');  // create + register
   try {
     // Your automation work
   } finally {
     await closeTab(myTab);   // close (or park, if last tab) + unregister
   }
   // Tabs you FOUND (didn't create) are never passed to closeTab — leave them open.
   ```
   **Why:** Other Claude sessions may be using other tabs. The user may need to review the results. Only close tabs you explicitly created for this specific task. `openTab`/`closeTab` bundle registration so it can't be forgotten: if this script crashes before `finally`, the next `chauffeur.py` reclaims the orphaned tab (it closes a tracked tab only when its creating process is gone) — see **Resilient Connection**. `closeTab` also parks on `about:blank` instead of closing when it's the last tab, so it never exits the persistent browser. The `script-template.js` reference already wires this in.
   
   **When to leave tabs open:**
   - User needs to log in (can't automate)
   - User needs to review/approve something
   - User asked you to "open" something for them
   - Tab was already open when you started (you found it, didn't create it)

   **Closing all your session's tabs at the end (level-1 self-cleanup).** `closeTab` handles the tab one script opened. When a whole **session** is finished with the browser — every task done and the user no longer needs any staged tab open — close everything it opened in one call:
   ```bash
   python plugins/browser-chauffeur/skills/browser-chauffeur/templates/chauffeur.py --close-owned
   ```
   It closes only tabs owned by this session (matched on its `CLAUDE_PID`) - never another session's, never the user's, never the browser's last page. This is the ideal: sessions clean up after themselves, so the sweep (owner reap → idle age-out → count cap) stays the rare backstop it's meant to be. Only leave a tab open while the user still needs it (a login, a review, an in-progress form).

3. **Leave the browser running.** The persistent browser stays open for future tasks — this is how logins survive across tasks. **NEVER** kill all browser processes (e.g., `taskkill //IM msedge.exe`, `Get-Process msedge | Stop-Process`, `pkill msedge`) — that destroys both the persistent chauffeur browser and the user's personal browser sessions.

4. **Do NOT delete the profile.** The persistent profile at `~/.claude/browser-chauffeur/profile/` stores the user's logins and sessions. Deleting it would force them to re-authenticate on every task. If you need to reset the browser (clear all logins and start fresh), see the profile cleanup utilities below.

5. Report what was accomplished to the user. Base your report on what you read from the final page state — do not summarize from memory or inference. If specific values were requested (a title, a field value, a count), quote them directly from the page content.

6. **For recurring automation, don't commit the script.** See **Two usage modes** above — create a SKILL.md spec instead. Ad-hoc scripts go in `.tmp/` and are regenerated each run.

**Exception:** If the task ended in a failure that requires user intervention (Phase 4 escalation), leave the browser AND tab open so the user can see and interact with the current state.

---

## Profile Management

The persistent profile at `~/.claude/browser-chauffeur/profile/` stores all logins, cookies, and browser state. Over time, this can grow large (1GB+). Use the cleanup utility when needed:

```bash
# Check profile size
python plugins/browser-chauffeur/skills/browser-chauffeur/templates/cleanup-browser.py --size

# Clean up old temporary profiles from .tmp/ (from before persistent mode)
python plugins/browser-chauffeur/skills/browser-chauffeur/templates/cleanup-browser.py --clean-old

# Reset the persistent browser (kill it and delete the profile) — forces fresh login on next use
python plugins/browser-chauffeur/skills/browser-chauffeur/templates/cleanup-browser.py --reset

# All of the above
python plugins/browser-chauffeur/skills/browser-chauffeur/templates/cleanup-browser.py --all
```

**When to reset:**
- Profile has grown over 2GB and browser startup is slow
- Browser is in a bad state (crashes, won't connect, corrupted data)
- Need to test a flow from a fresh login state
- Switching between different accounts/environments

After reset, the next task will launch a fresh browser and the user will need to log in again.

---

## User Intervention — Proper Alerting (MANDATORY)

Sometimes the browser hits a blocker that only a human can resolve. These all fall into one category: **the user needs to go do something in the browser window.** Whether it's a CAPTCHA, a login page, an MFA prompt, or a cookie consent wall that can't be auto-dismissed — the response is the same.

**NEVER just log to the terminal and hope the user sees it.** The user may not be watching terminal output, especially during long-running batch scripts. You MUST use `AskUserQuestion` to create a visible, blocking prompt.

### What triggers user intervention

- **CAPTCHAs / human verification** — Cloudflare "Verify you are human", Zillow "Press & Hold", reCAPTCHA, hCaptcha, Arkose Labs. Cannot be solved programmatically. Do NOT retry or switch browsers — escalate immediately.
- **Login pages** — the user isn't signed in, or the session expired mid-run. You may try the other browser first (Edge/Chrome fallback), but if that also shows a login page, escalate immediately.
- **MFA prompts requiring the user's phone or authenticator app** — an SMS code, a push notification, a TOTP app. Nothing you can reach; escalate.
- **Unresolvable consent/terms walls** — cookie banners or terms pages that can't be auto-dismissed by the overlay dismissal pattern.

### Email-delivered MFA codes — fetch them yourself, don't escalate

When a site's MFA step sends a one-time code to an email address you have read access to (a connected Gmail/Outlook/IMAP mailbox), this is **not** a user-intervention case — complete it yourself:

1. Note the destination the page names ("We just sent a code to `user@example.com`") and the field waiting for it.
2. Search that mailbox for the just-sent code — sender/subject naming the site or "verification"/"authorization code"/"one-time passcode" is usually enough; sort by most recent, since the same sender may have older codes from prior attempts.
3. Enter the code and submit, the same way you'd fill any other field.
4. If the mailbox search comes up empty after a reasonable wait, or the code is rejected, retry the search once (delivery lag) before falling back to `AskUserQuestion` — don't spin indefinitely.

Only escalate an MFA prompt when the code goes somewhere you can't reach (SMS, an authenticator app, a phone call) — see the bullet above.

### Required `AskUserQuestion` pattern

```
AskUserQuestion with options:
- "Done, I solved it" — user completed the action
- "Can't find the browser" — user needs help locating the window
```

Include in the question text: what site triggered the blocker, what kind of challenge it is (CAPTCHA, login, MFA), and that they need to switch to the browser window.

### If user can't find the browser

Attempt to bring the window to the foreground, then re-prompt with `AskUserQuestion`.

### Monitoring long-running scripts

When running a batch script that may encounter these blockers mid-run:

1. Set up a **Monitor** on the script's output watching for intervention keywords (`CAPTCHA DETECTED`, `login page`, `Sign in`, etc.)
2. When the Monitor fires, **immediately use `AskUserQuestion`** — don't wait for the script to finish
3. After the user confirms resolution, verify the script continued past the blocker

### After the user resolves the blocker

- Leave the browser open — the user needs the same session
- Re-run or continue the script — the browser session should now be past the blocker
- If the same blocker reappears, escalate again with `AskUserQuestion` — don't silently retry

---

## Phase 4: Failure Recovery (ALWAYS run after a script exits)

When a script completes — **regardless of exit code** — analyze the output and recover autonomously. **Exit code 0 ≠ success.** Many scripts complete with errors in their output. Trust verification output and visual evidence, not exit codes. **Don't ask permission to debug — that's your job as the chauffeur.**

### Step 1: Parse output for success/failure signals

Read the full output and categorize:

- **Explicit success** — `Verification passed`, `✅`, `All checks passed` AND no error patterns → proceed to reporting.
- **Human action required** (see **User Intervention**) — diagnostic screenshot shows a CAPTCHA, login page, or MFA prompt; or output contains `CAPTCHA DETECTED` or similar → use `AskUserQuestion` immediately. Do NOT retry autonomously.
- **Explicit failure** — `Verification FAILED`, `VERIFY FAIL:`; error counts (`5 errors`, `3 collision(s)`, `12 errors remain`); error keywords (`Error:`, `still present`, `not found`, `timeout`, `could not`); or items reported as "still present" or "not moved/deleted" → trigger autonomous recovery (Step 2).
- **Ambiguous (missing verification)** — no "Verification passed" or "Verification FAILED" in output, but has completion indicators (`Done`, `Summary:`, task-specific output) → likely an older script without verification. If output looks clean (no errors/exceptions), treat as success. If output contains exceptions or is suspiciously short (< 50 chars), investigate.
- **Crashed/incomplete** — exception stack trace in output, output ends mid-step (no completion message), or very short output with no summary → trigger autonomous recovery (Step 2).

### Step 2: Autonomous recovery loop

When errors are detected, **you are the debugger**. Do not show the user an error and ask what to do. Diagnose, fix, and re-run. Don't ask user permission — enter the loop.

1. **Read the diagnostic screenshot** — scripts save screenshots to `.tmp/diag-*.png` on failure. Use the Read tool to view the image. This tells you what the browser was actually showing: an overlay, a login page, a CAPTCHA, a changed UI, or something else entirely. Take additional screenshots of the failure page if needed.

2. **Diagnose the cause** from what you see:
   - **CAPTCHA, login page, or MFA prompt** → requires human action. Follow **User Intervention** — use `AskUserQuestion` immediately. For login pages, you may also try the other browser (Edge/Chrome fallback) first, but if that also shows a login page, escalate.
   - **Overlay or modal blocking the UI** → add dismissal logic to the script (see **Overlay Dismissal**), re-run.
   - **UI changed** (different label, restructured DOM, new element) → inspect the current page state to find the new selector, update the script, re-run.
   - **New required step** (e.g., a consent prompt, a "What's new" tour) → add handling for it, re-run.
   - **Selector timing issues** (element not yet visible), **elements scrolled out of view**, or **the expected element is in a different frame** → take a fresh screenshot, read it, find the correct selector or frame.
   - **connectOverCDP timeout** — output contains `connectOverCDP did not complete within ...ms` → the persistent profile is overloaded or has a wedged renderer (see **Resilient Connection** above). Re-run `chauffeur.py` to sweep orphaned tabs, then retry the script once. If it still times out, the overload is from tabs the sweep won't touch (an active session's or the user's) — escalate via `AskUserQuestion`. Do NOT auto-reset the shared browser; that kills every concurrent session and wipes all logins.
   - **Media still playing** — if the script's stuck-detection fired but the page has active audio/video (`!audio.paused && !audio.ended && audio.duration > 1`), the page isn't stuck — narration is in progress. Wait for the media's remaining duration plus a buffer before re-evaluating. Only declare genuinely stuck when both conditions are true: no advance button AND no active media.
   - **None of the above looks right** → the failure may be a known anti-pattern. **Read `anti-patterns.md`** and check whether your symptoms match: a locator returning multiple matches in strict mode, a `page.evaluate` click that updates the UI but doesn't persist server-side, a click that "succeeds" but produces no DOM change, a `[role="dialog"]` presence check that returns true after the dialog closed, or a `getByRole('button')` returning nothing for a visibly-clickable element. Each entry has a tested fix.

3. **Use diagnostic patterns** from `templates/diagnostic-patterns.js` to inspect failing selectors — element visibility test, button enumeration, timing comparisons.

4. **Fix the script** — edit the failing section based on what you diagnosed. Don't guess — base every fix on what you actually saw in the screenshot or page state.

5. **Re-run the script** — execute it again and verify it passes the point that previously failed.

6. **Repeat** through the loop until verification passes OR you've exhausted options (3+ iterations with different approaches, or you've tried both browsers and dismissed all visible overlays). Then escalate via `AskUserQuestion` (see **User Intervention**) explaining what you found and what you need.

### Step 3: Reporting

**Only report to user when:**
- ✅ **100% success achieved** → "Fixed N issues: [brief summary]. Verification now passing."
- ❌ **Exhausted all recovery options** → **Leave the browser open at the failure state** (Phase 3 Exception covers this) and tell the user to look at the browser window — the visible page often makes the issue obvious at a glance, faster than any diagnostic summary. Then show your diagnostics, explain what you tried and what you found, and ask for help.

**Rule:** Never tell the user "the script failed." Always read the diagnostic screenshot, diagnose, fix, and retry at least once before involving the user.

### Common error patterns (signal "autonomous recovery needed")

```
Error: Could not X
VERIFY FAIL: "Event name" still present
Verification FAILED — some events still remain
Delete button not found in popup
Timeout 30000ms exceeded
could not find Start date field
X moved, Y errors
Summary shows non-zero error counts
```

### When running scripts from other skills

If another skill runs a script and it fails, that skill should follow this same recovery loop. The script saves diagnostic screenshots specifically so that whatever is running it — whether browser-chauffeur or another skill — can read the screenshot with the Read tool, see what went wrong, and fix it autonomously. The screenshots are not for the user; they are for you.

---

## Phase 5: Script Validation (before running any ad-hoc script)

**CRITICAL:** When you write a new ad-hoc browser automation script (in `.tmp/`), **immediately validate it before running** or reporting completion. Scan it for violations of `script-quality-standards.md`: fixed delays (`waitForTimeout`, `setTimeout`), missing verification code, CSS class selectors, missing browser connection logic, or a missing guaranteed-exit path (a bare `(async () => { ... })()` IIFE with no top-level `.catch()` leaks an orphaned process when a step throws or hangs — use the `run()`-plus-`.catch(... process.exit(1))` shape with `browser.close()` in a `finally`). If you find any, edit the script to fix them, explain what was wrong and what you fixed, then re-scan to confirm clean. **Do not ask permission** — violations are always wrong.

---

## Common Patterns

**Stale State Cleanup** — Before the main loop in any multi-step batch script, call `cleanupStaleState(page)` from `browser-chauffeur-helpers`. If a previous run aborted mid-flow (e.g., a Save click timed out), the editor may still be open with dirty changes. The next run then hits a "Discard changes?" dialog that intercepts pointer events and silently blocks all subsequent clicks. This helper detects visible `[role="dialog"]`, `[role="alertdialog"]`, and `[role="menu"]` elements, clicks safe-close buttons in priority order (`Cancel` → `Discard` → `OK` → `Close`), falls back to Escape, and loops until no popup remains.

**Post-Mutation Verification** — After any create, update, or delete action on a SPA, do not verify immediately. `networkidle` fires when the network quiets, but virtualized grids (Outlook calendar, Teams, SharePoint lists) re-render asynchronously after the server response. Use `verifyAfterMutation(page, predicate, { settleMs, retries })` from `browser-chauffeur-helpers` instead. It waits `settleMs` (default 1.5s), runs the predicate, and retries up to `retries` times (default 3) before declaring failure. Return `true` from the predicate when the expected post-mutation state is confirmed.

**Overlay Dismissal** — A fresh browser profile will show first-run overlays — Edge sync prompts ("We are now syncing your browsing data"), cookie consent banners, "What's new" modals. These block the real UI and cause element waits to time out. See `templates/overlay-dismissal.js` for the `dismissOverlays(page)` helper. Call it immediately after navigating to the target app, **before** waiting for app-specific elements. Include it in every script.

**Screenshot on Failure** — Scripts save screenshots to `.tmp/diag-*.png` on failure so the recovery loop can read them. See `templates/screenshot-on-failure.js` for the `screenshotOnFailure(context, label)` helper. Use it in catch blocks when the app fails to load, and in any browser fallback loop so each failed attempt produces a screenshot for debugging.

**Virtualized Grid Scrolling** — To render off-screen rows in a virtualized grid/list (Vaadin Grid, react-window, ag-grid, and similar — they mount only visible rows and recycle DOM nodes as you scroll), call the component's own scroll-to API directly via `page.evaluate()`: `grid.scrollToIndex(n)` for Vaadin Grid, or the analogous method on whichever virtualization library the page uses. Verify the target content actually appears in `document.body.innerText` afterward before moving on. Locate and click a row's control within one single `page.evaluate()` call rather than splitting the two across separate script invocations — a virtualized grid's scroll position and recycled row contents can shift between runs, so measuring a position in one script and clicking it in the next can land on a different row than intended. When the resulting action is destructive or hard to undo, read back its confirmation dialog (or resulting state) to confirm it names the item you meant before proceeding.

**Coordinate Clicks in CSS Pixels** — Get click targets via `getBoundingClientRect()` inside `page.evaluate()`, which always returns CSS pixels matching what `page.mouse.click()` expects. Don't read coordinates off a saved screenshot image — screenshots can be rendered at a different pixel density (`devicePixelRatio`, e.g. 1.25x on Windows), so numbers eyeballed from an image land in the wrong place on the live page.

**Framework Tooltip Discovery** — When a control's purpose isn't obvious from its own `title`/`aria-label`, check for the UI framework's dedicated tooltip element by tag name before concluding no such control exists. Vaadin, for instance, renders tooltips as separate `<vaadin-tooltip text="...">` elements that aren't always linked back to their control via a `for`/`id` pair — `[...document.querySelectorAll('vaadin-tooltip')].map(t => t.getAttribute('text'))` surfaces text a plain attribute scan on the button itself will miss.

**Common Anti-Patterns** — Most of these failure modes are now prevented by positive rules in **Key Rules** below (Selectors, Strict mode, Selector fallback, Actions vs reads, Visibility-not-presence). `anti-patterns.md` is the symptom-recognition reference for when a failure has already occurred — covers: text-based filter ambiguity, `page.evaluate` clicks not triggering React/Fluent UI synthetic events, diagnosing click failures from screenshots not error text, phantom `display:none` dialogs left in the DOM, role-vs-tag selector gaps (semantic HTML buttons that aren't `[role="button"]`), Fluent UI icon-button exact-match failures (use `:has(span.fui-Button__icon)` instead), and role-less confirmation dialogs (use geometry-based modal detector when `[role="dialog"]` returns nothing).

---

## Key Rules

**Selectors:** Prefer `aria-label`, `role`, and text-based selectors over CSS classes. CSS classes change across deployments and tenants; semantic attributes don't.

**Strict mode:** A locator matching multiple elements throws. Prefer `getByRole('button', { name: '...' })` over bare `aria-label` when both a wrapper `div` and the `button` itself share the same label. Use `.first()` only when you've confirmed the elements are genuinely equivalent.

**Selector fallback:** If `getByRole` or `[role="..."]` returns nothing for a visible element, broaden to the underlying HTML tag (`li`, `a`, `span`). Older or semantic UIs use real elements as action targets without a `role` attribute, and role-based selectors skip them entirely. To discover the true tag, enumerate by *direct* text content (not `innerText`, which inherits children's text).

**Waits:** Never use fixed delays — this includes both Playwright's `waitForTimeout` and raw `new Promise(r => setTimeout(r, N))`. Use `locator.waitFor()`, `page.waitForURL()`, `page.waitForLoadState()`, or `page.waitForFunction()` instead. Fixed delays make scripts slower when the page is fast and still flaky when it's slow. The only acceptable `setTimeout` is a short poll interval (≤300 ms) inside an active loop that exits as soon as a condition is met.

**Actions vs reads:** Use Playwright's locator API (`locator.click()`, `locator.fill()`, `locator.dispatchEvent('click')`) for any action that should trigger a server call or framework event. Reserve `page.evaluate` for *reading* DOM state. A native `element.click()` inside `evaluate` bypasses framework synthetic event handlers (React, Fluent UI) — the UI may update visually while no API call fires, and the change vanishes on refresh.

**Verification:** Always re-read page state after save/submit actions. A form can silently fail to save. Trust nothing without checking.

**Variant UIs:** The same application can render completely different DOM on different tenants or account types. When a selector fails in a new context, re-read there and find the equivalent element — never assume one tenant's selectors work on another.

**Overlay detection:** When a click doesn't land, check for modal overlays or portal elements blocking the target before retrying. Dismiss them first.

**Visibility, not presence:** When checking whether an element is *shown* (dialogs, menus, backdrops, tooltips), filter by computed visibility (`display`, `visibility`, `offsetParent`), not by `querySelector(...) !== null`. Many frameworks (Google products, Fluent UI, Material) keep dialog DOM mounted and toggle `display: none` to hide closed steps — bare presence checks return true for elements the user can't see, and recovery loops will spin trying to "close" something that's already closed.

**Two-pass element reveal:** Some UIs hide form inputs behind clickable summary rows. If expected inputs aren't visible, click the summary row, re-read, then find the revealed inputs.

**Hard verification after mutating actions:** After any create, update, or delete action, navigate away from the page and back (or close and reopen it) to confirm the change persisted server-side. DOM changes alone (element disappearing, success toast appearing) do NOT prove the server processed the action — the UI may have updated optimistically while the API call silently failed or never fired. Never report a mutating action as successful until you've seen the result survive a fresh page load.

**InnerText vs Screenshot:** `innerText` / accessibility queries are primary — they give actionable content and element structure. Screenshots are the fallback — use them when spatial/visual layout matters (diagnosing why a click doesn't land, reading a visual overlay).

**DOM state for progress detection:** Never use pixel-diff or screenshot comparison to detect SPA progression. Pixel values change for irrelevant reasons (background animation, playback progress bar, animated UI elements). Find a DOM-readable indicator — page counter (`Screen X of Y`), URL fragment, breadcrumb, lesson number — and compare its value before and after a click. If the indicator doesn't change, the click had no effect.

---

## Script Quality Standards

All scripts must comply with `script-quality-standards.md` — banned patterns (fixed delays), required patterns (verification, semantic selectors, CDP connection, navigation), and additional requirements (progress logging, frame fallback, overlay dismissal, screenshot-on-failure). See `templates/script-template.js` for a complete reference script that satisfies all of these.
