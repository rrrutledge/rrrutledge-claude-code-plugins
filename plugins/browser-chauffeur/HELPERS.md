# Browser Chauffeur Helpers

Shared utilities for browser automation scripts.

## Setup

No manual setup needed. Run the browser-chauffeur setup script once (SKILL.md calls this automatically as part of the prerequisite check):

```bash
node plugins/browser-chauffeur/skills/browser-chauffeur/templates/setup.js
```

This installs `playwright-core` and writes a `browser-chauffeur-helpers` shim to `~/.claude/browser-chauffeur/node_modules/`. Scripts fall back to that location automatically when the package isn't installed in the current project.

The shim looks up the active plugin version each time it is required - from the plugin registry, falling back to the newest version in the plugin cache - so a plugin update takes effect on its own. Setup does not need re-running after an upgrade.

## Usage

Start every script from `skills/browser-chauffeur/templates/script-template.js`.
It resolves `playwright-core` and the helpers, connects, opens and closes its tab, and exits, the way `skills/browser-chauffeur/script-quality-standards.md` requires; the helpers below are what it imports.

## Available Helpers

### `dismissOverlays(page)`

Dismisses common overlay dialogs (cookie banners, "What's new" modals, etc.) that appear on first visit.

- **Parameters**: `page` - Playwright page object
- **Returns**: Promise<void>
- **When to call**: SKILL.md Common Patterns, **Overlay Dismissal**

### `screenshotOnFailure(context, label)`

Takes a diagnostic screenshot when automation fails. Useful in catch blocks.

- **Parameters**: 
  - `context` - Playwright browser context
  - `label` - String label for the screenshot file
- **Returns**: Promise<void>
- **Saves to**: `.tmp/diag-{label}-{timestamp}.png`

### `openTab(context, url)`

Opens a new tab, registers it in the shared tab registry under the current Claude session, and navigates to `url` if provided. It is how every script creates a tab (SKILL.md Phase 1, **Getting a tab**). It also attaches a page-scoped `popup` listener, so any tab this page later spawns itself (`target="_blank"`, `window.open`, ctrl-click) is registered under the same session automatically - capture it with `await page.waitForEvent('popup')` around the click if you need to drive it.

- **Parameters**: `context` - Playwright browser context; `url` - optional URL to navigate to
- **Returns**: Promise<Page>

### `closeTab(page)`

Closes a tab opened with `openTab` and unregisters it. Parks on `about:blank` instead of closing when it's the browser's last tab, so it never exits the persistent browser. Which tabs to pass it, and when: SKILL.md Phase 3, **Tab cleanup**.

- **Parameters**: `page` - a Playwright page returned by `openTab`
- **Returns**: Promise<void>

### `findTab(context, predicate)`

Reuses **your own session's** tab that matches the predicate, and marks it active so a tab you keep returning to holds its place in the eviction order and isn't reaped as idle. It only ever returns a tab this session opened; a tab another Claude session opened, or one the user opened by hand, is never returned even when it matches the predicate - so two sessions on the same URL never grab each other's tab. When you own no matching tab it returns `null`, and you open your own with `openTab`.

- **Parameters**: `context` - Playwright browser context; `predicate` - `(page) => boolean`
- **Returns**: Promise<Page | null> - your own matching tab (the most-recently-active if you own several), or `null` (then open one with `openTab`)

### `touchTab(context, page)`

Marks a tab **you own** active (the lower-level primitive `findTab` uses). Call it directly when you hold a page you'll keep using across a long flow and want to refresh its activity. It refreshes only a tab already registered to this session - it never adopts an unregistered tab or claims one another session owns.

- **Parameters**: `context` - Playwright browser context; `page` - the Playwright page
- **Returns**: Promise<void>

### `cancelScriptWatchdog()`

Cancels the process watchdog that requiring the helpers installs (see **Process guard** below). Call it only in a script that legitimately needs to run past the watchdog window - for example, one that waits out a long media playback.

- **Parameters**: none
- **Returns**: void

### Process guard

Requiring `browser-chauffeur-helpers` installs a process guard that keeps a hung or failed script from lingering as an orphan. A script talks to the browser over a live CDP socket, and that open socket holds Node's event loop open, so a script that throws or hangs never exits on its own - it survives until the browser closes hours later, and a session's worth of them can exhaust memory. Two nets, both automatic:

- **Watchdog** - force-exits a script that is still alive after a wall-clock timeout (default 30 minutes, override with `BROWSER_CHAUFFEUR_SCRIPT_TIMEOUT_MS`). Generous by design so a legitimately long single script isn't cut off; the timer is unref'd, so it never delays a script that finishes cleanly. A script that must run even longer calls `cancelScriptWatchdog()`.
- **Unhandled rejection** - a script whose top-level promise rejects with no `.catch()` (a bare IIFE) is exited promptly with the error printed, rather than left hanging behind the socket.

The guard is the safety net, not the plan: a well-formed script still exits promptly through its own `finally { await browser.close(); }` and `run().catch(e => { console.error(e.message); process.exit(1); })` - see **Guaranteed Process Exit** in `script-quality-standards.md`.

### Where tab state lives

One file per open tab, at `~/.claude/browser-chauffeur/tabs/<ownerPid>-<targetId>.json`, with the file's mtime as that tab's last-activity time.
The owning session and the CDP target are both in the filename, so `ls` answers most questions on its own - which tabs a session owns, which owners are no longer running, what the browser is holding.
The file's contents (`url`, `title`) are there for the sweep's change detection and for reading by hand.

Splitting the state per tab is what lets concurrent sessions record tabs without coordinating: each writer only ever touches the file for the tab it is acting on, so no session can drop another's tab and no lock is needed.

### Login detection

The session decides login state by reading a screenshot (SKILL.md Phase 0, **Step 2 - Snapshot the target**).
For a mid-flow check (the session may have expired), a script screenshots the page and prints the path for the recovery loop to read:

```javascript
const path = `.tmp/session-check-${Date.now()}.png`;
await page.screenshot({ path });
console.log(`SCREENSHOT: ${path}`);
console.log(`FINAL_URL: ${page.url()}`);
```

## Why Import Instead of Copy?

**Old pattern (bad)**:
- Every script had its own inline copy of helpers
- Improvements never propagated to existing scripts  
- Had to manually update dozens of scripts when fixing bugs

**New pattern (good)**:
- Scripts import from a central module
- Improvements automatically benefit all scripts
- Single source of truth for shared utilities

## Adding New Helpers

1. Create the helper function in `templates/your-helper.js`
2. Export it from `helpers.js`
3. Document it in this file
4. Update the template in `templates/script-template.js` to show the import pattern
