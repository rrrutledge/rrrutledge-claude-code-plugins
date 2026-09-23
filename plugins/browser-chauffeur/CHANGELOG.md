# Changelog

## [1.16.0] - 2026-09-23

### Changed
- **A tab is now owned by the Claude session that opened it via `CLAUDE_PID` - the session's own claude process - and `BROWSER_CHAUFFEUR_OWNER_PID` is retired.**
  `CLAUDE_PID` is injected by Claude Code into every tool subprocess and is the same pid across all of a session's tool calls (each call runs in its own short-lived process, so the opening script's own pid would differ call to call), and it lives exactly as long as the session.
  That makes it the one stable per-session owner - the exact role `BROWSER_CHAUFFEUR_OWNER_PID` was created to fill - so it now covers every session identically with nothing to set up: a launched handoff tab, a headless `claude --bg` drainer worker, and a `claude` started in a terminal all get session-lifetime tab ownership automatically.
  `tab-registry.js` `ownerInfo` and `chauffeur.py --close-owned` both resolve the owner from `CLAUDE_PID`, falling back to the short-lived node process only outside a Claude session.
  `scripts/launch-session.ps1` no longer exports `BROWSER_CHAUFFEUR_OWNER_PID`/`BROWSER_CHAUFFEUR_OWNER_START`, and the per-terminal `$PROFILE` line that used to set the owner pid is no longer needed - the recycle guard the exported start time provided is now covered by reading `CLAUDE_PID`'s liveness directly plus the idle/count sweep, and any tab record still carrying a recorded start is honored on read for the rollout window.
  The single behavior change: a tab is reclaimed when its claude session ends rather than persisting across a claude restart in the same terminal - which for the dedicated automation browser is the tidier outcome.

## [1.15.2] - 2026-09-03

### Fixed
- **`chauffeur.py`'s tab-registry and state-file reads/writes now open every JSON file with `encoding="utf-8"`, instead of Python's platform-default text encoding.** On Windows that default is the system codepage (commonly cp1252), so a registered tab whose title or URL carries a non-ASCII character (e.g. an ellipsis in a "Redirecting…" page title) could crash `TabRecord.content()` with a `UnicodeDecodeError` reading its own state back — surfacing as `--close-owned` and the orphan sweep failing outright. Every JSON `open()` in `chauffeur.py` and `cleanup-browser.py` is now explicit about UTF-8, and the two read paths that already caught `JSONDecodeError` also catch `UnicodeDecodeError` so a still-malformed file degrades to "no state" instead of crashing the caller.

## [1.14.0] - 2026-07-25

Stops a hung or failed automation script from lingering as an orphaned process.
A script talks to the browser over a live CDP socket, and that open socket holds Node's event loop open, so a script that threw or hung never exited on its own: it survived until the browser itself closed hours later, and a long session's worth of them piled up faster than they drained and could exhaust the machine's memory.

### Added
- **Requiring the helpers now installs a process guard that force-exits a script the moment its work can no longer finish.** Every automation script requires `browser-chauffeur-helpers`, so the guard reaches every script, including a quick ad-hoc one that skips the template's own exit path. Two nets: an unhandled-rejection handler exits promptly when a script's top-level promise rejects with no `.catch()` (a bare IIFE), rather than leaving it hanging behind the socket; and a wall-clock watchdog force-exits a script still alive after a timeout (default 30 minutes, override with `BROWSER_CHAUFFEUR_SCRIPT_TIMEOUT_MS`). The watchdog is generous by design so a legitimately long single script — a large batch, or a wait for a long media playback — is never cut off, and it's unref'd so it never delays a script that finishes cleanly. A script that must run past the window calls the new `cancelScriptWatchdog()` export.
- **A `✅ REQUIRED: Guaranteed Process Exit` rule in the script quality standards, checked in Phase 5 validation.** Scripts wrap their body in `try { ... } finally { await browser.close(); }` and end with `run().catch(e => { console.error(e.message); process.exit(1); })`, so a throw or a rejection can never leave the process alive behind the open socket. This is the prompt exit; the process guard is the backstop when a script skips it.

## [1.13.0] - 2026-07-25

### Changed
- **The `browser-chauffeur-helpers` shim finds the active helpers when it is required, instead of naming one version's path.** The plugin installs under a version-numbered directory, so a path chosen at setup time stopped being right the moment the plugin updated: scripts kept loading the previous version's helpers until something re-ran setup or re-pointed the shim by hand, and a version bump that changed helper behaviour silently didn't take. The shim now asks the plugin registry which version each scope actually has installed — preferring the entry governing the current project, so a project deliberately held back still gets its own version — and falls back to the newest version in the plugin cache, then to the path that was current when it was written. Versions are ranked numerically, so 1.12.0 outranks 1.8.1. An update now applies on its own, with no setup run and nothing to re-point.

## [1.12.0] - 2026-07-25

Makes the count cap stop evicting tabs a running session still needs, and gives owner identity enough precision that a recycled PID can't keep a dead session's tabs alive.

### Changed
- **The count cap gives up unclaimed tabs before a live session's.** It ordered purely by idleness, so once the browser was over the ceiling it would close whichever tab had been quiet longest — including one a running session was mid-flow on, while a tab nobody owned survived for having been touched more recently. Eviction is now tiered: tabs with no owner go first, and a live session's tabs are touched only if the browser is still over the ceiling afterwards. Least-recently-active still decides within each tier, which is what keeps a tab you are working in — including one you opened by hand, since interacting with it moves its URL or title and the sweep marks it active.
- **Owner identity is the session's PID plus its process start time.** Windows hands out PID numbers again after a process exits, so a PID being live was never evidence that the session which recorded it was still running: a later, unrelated process inheriting the number made a dead session's tabs look owned and alive, leaving them to sit until the 12h idle age-out. Start time is unique per process, so comparing it identifies the impostor. The launcher records it beside the PID; a record written without one still works, falling back to matching the PID alone. Comparison allows two seconds of slack, since the value is written by PowerShell and read back through the Win32 API — a real recycle cannot hide inside that window, because the replacement process starts no earlier than the moment the original exited.
- **Liveness is read from the OS directly instead of by running a process lister.** Asking Win32 costs about 4 microseconds against a subprocess spawn, and it answers the start-time question in the same call. It also distinguishes a PID that no process holds from one this user may not query: the second case now reads as unknown and leaves ownership alone, where a process lister could only say "absent" and risk reaping a live session's tabs.

### Fixed
- **A rename that Windows refuses no longer drops the write and strands the temp file.** Records are written to a temp file and renamed into place so no reader ever sees a half-written file, but Windows refuses that rename while another process has the target open. On a single attempt the update was silently lost and the temp left on disk for good. The rename now retries briefly across that moment, and the temp is removed either way.

## [1.11.0] - 2026-07-25

Keeps tab ownership intact when several Claude sessions drive the browser at once.
Ownership itself was already sound — a session could not navigate or reuse another session's tab — but the state recording that ownership lost entries whenever two sessions wrote at the same moment, and a tab with no entry has no owner, so it escaped both `--close-owned` and the sweep's owner reap and lingered until the idle/count backstop.
That is where the ownerless tabs accumulating in the browser were coming from.
Measured on a 6-process, 18-tab run: 5 of 18 tabs lost their record before, 0 of 18 after.

### Changed
- **Tab state is one file per tab, not one file for all of them.** `~/.claude/browser-chauffeur/tabs/<ownerPid>-<targetId>.json`, with the file's mtime as that tab's last-activity time. Recording a tab in a single shared file meant reading the whole list, appending, and writing it all back, so two sessions doing that at the same moment both started from the same list and the second write silently discarded the first one's tab. Now every writer only ever touches the file for the tab it is acting on: there is nothing of anyone else's in it to lose, so the race cannot happen and no lock is needed to prevent it. Owner and target are in the filename, so "which tabs are mine" and "whose session has ended" are answered by listing the directory with no file read at all — and `ls` now shows the browser's whole tab state by hand. Tabs already open at the upgrade are adopted on the next sweep, so they age out on idleness rather than being reaped the moment their session ends; that resolves itself as those tabs close.
- **Owner liveness costs one process listing per sweep instead of one per tab.** Each tab used to spawn its own `tasklist`, so the sweep slowed down as sessions added tabs — exactly backwards, since every session runs a sweep at launch. A full launch and sweep now takes 5–6s where it took 12s at a comparable tab count. One listing is taken up front and every tab answered from it.

### Fixed
- **The sweep no longer erases records for tabs it did not touch.** It built its picture of the state up front, then spent seconds on process listings and tab closes, then wrote that now-stale picture back over the file — deleting every tab another session had recorded in the meantime. With per-tab files it never rewrites anything but the tabs it is actually acting on, so a tab recorded mid-sweep is untouched by construction. `--close-owned` had the same flaw and now reads and removes only its own session's files.
- **A failed process listing no longer reads as "every session ended."** Owner reap treated an unanswerable liveness question as a dead owner, so a single failed probe could reap every session's tabs at once. Liveness is now three-valued: unknown leaves ownership alone and lets the idle and count layers do the work.
- **A tab adopted mid-registration no longer ends up recorded twice.** When a sweep saw a brand-new tab before its session had recorded it, it adopted it as ownerless, and the session's own record then landed alongside. The sweep now re-checks for an existing record before adopting, prefers the owned file over an ownerless one when both exist, and cleans up the loser once the tab closes.

## [1.10.3] - 2026-07-22

### Fixed
- **`dismissOverlays` could click a real page action button, not just an overlay.** Playwright's `getByRole` name match is a case-insensitive substring match by default, so the un-exact `'Close'` lookup matched any button whose accessible name merely contained that word — including GitHub's "Close pull request" button, which the helper then clicked for real on a page that had no overlay at all. Now uses `exact: true` for the literal-text lookups (`Got it`, `Dismiss`, `Close`), and additionally requires the matched button to actually sit inside something overlay-shaped (a `dialog`/`alertdialog` role, a fixed/sticky-positioned stacking element, or a modal/cookie/consent/toast-named container) before clicking it — a plain in-page button is left alone regardless of its label.

## [1.10.1] - 2026-07-15

### Changed
- **Email-delivered MFA codes are no longer a user-intervention trigger.** When a site sends its one-time code to a mailbox Claude already has read access to (Gmail/Outlook/IMAP), fetch the code from that mailbox and complete the login directly instead of pausing with `AskUserQuestion`. Escalation is now reserved for MFA that lands somewhere unreachable — SMS, an authenticator app, a phone call.

## [1.10.0] - 2026-07-06

Stops one Claude session from grabbing and navigating another session's tab. Previously two sessions on the same site (e.g. both editing the same LinkedIn profile) could clobber each other's work when one session's tab reuse matched the other's tab by URL.

### Changed
- **`findTab` is now owner-scoped.** It returns a matching tab only when that tab's registry entry is owned by the current session; a tab another session opened, or one the user opened by hand (unregistered), is never returned even when it matches the predicate — `findTab` yields `null` instead, so the caller opens its own tab with `openTab`. When the session owns several matching tabs (repeated `openTab`, or a click-spawned popup), it returns the most-recently-active. Predicate matches are filtered first, so only the few matches pay the per-tab CDP targetId lookup. Every existing call site is safe unchanged — a caller that owns no match simply opens a fresh tab as before.
- **`findTab` no longer adopts the tab it finds.** Ownership is created only by `openTab` and by popup registration; the reuse path never claims a tab it didn't open. `touchTab` correspondingly refreshes only a tab already owned by this session and never adopts an unregistered tab or claims one owned by another session.

## [1.9.1] - 2026-07-03

### Changed
- **Renamed `templates/launch-browser.py` → `templates/chauffeur.py`.** The script is the browser-lifecycle multitool — it launches/reuses, sweeps, and closes owned tabs (`--close-owned`) — so the verb "launch" clashed with the shutdown-style modes. `chauffeur` names the thing rather than one action, so every mode reads sensibly, and it's distinctive to this plugin rather than a generic word another project might collide with. All references updated (SKILL.md, `tab-registry.js`, drainer `worker-core.md` + `run-poller.py`).

## [1.9.0] - 2026-07-03

Stops the persistent browser accumulating tabs until it crashes. Tabs are now owned by the Claude session that opened them and cleaned up promptly when that session's window closes; an idle age-out (12h) and a hard tab-count ceiling (15) back that up for tabs that outlive their session or were opened without the `openTab` helper.

### Added
- **Session-scoped tab ownership.** A tab is owned by the **Claude session** that opened it, not the short-lived node script — a single session fires many scripts (act, screenshot, retry), so the tab must outlive any one of them. `openTab`/`touchTab` record an owner PID from `BROWSER_CHAUFFEUR_OWNER_PID` (falling back to the node process when unset); `scripts/launch-session.ps1` exports it as the session's long-lived host PID, and for interactively-started sessions you set `OWNER_PID` in your shell profile. Registry field `nodePid` → `ownerPid` (old entries still read).
- **Click-spawned tabs are owned automatically.** `openTab`/`findTab` attach a page-scoped `popup` listener, so a tab a page opens itself (`target="_blank"`, `window.open`, ctrl-click) is registered under the same session and chained (a popup's own popups are caught too). The listener is on the specific page, so the new tab is correctly attributed to the session that owns the opener — a context-level `page` listener would fire for every tab in the shared browser with no way to tell which one you opened. Such tabs are now first-class owned tabs (owner-reap and `--close-owned` handle them), not just age/count-backstop fodder.
- **Three-layer sweep in `launch-browser.py`** (`sweep_orphan_tabs` → `sweep_tabs`), run on every browser reuse. Reclaiming is driven by idleness; ownership just enables prompt cleanup: (1) **owner reap** — a tab whose owning session has ended is closed right away (the courtesy), so a tab lives as long as its session's window is open; (2) **idle age-out** — any tab idle longer than `TAB_TTL_SECONDS` (default 12h) is closed regardless of owner, catching genuinely abandoned tabs; (3) **count cap** — if more than `MAX_TABS` (default 15) remain, close the least-recently-active until back under it, ownership-agnostic. It never closes the browser's last page. Safe because the chauffeur browser is a dedicated automation instance, separate from the user's personal browser. Both thresholds are env-overridable (`BROWSER_CHAUFFEUR_TAB_TTL`, `BROWSER_CHAUFFEUR_MAX_TABS`).
- **Least-recently-active eviction.** The idle age-out and count cap go by last activity, so an in-use tab is never the one closed. Activity is tracked automatically: `openTab`/`registerTab` stamp `lastActive`, and the sweep bumps it whenever it sees a tab's URL/title change between runs — no cooperation needed from the scripts that touched the tab. New `findTab(context, predicate)` / `touchTab(context, page)` helpers (from `browser-chauffeur-helpers`) mark a *reused* tab active and claim it for the current session; `findTab` calls `touchTab` for you, so it's rarely called directly.
- **`launch-browser.py --close-owned` — level-1 self-cleanup.** A session's ideal end state is to close its own tabs, so the sweep stays a rare backstop rather than the routine cleanup. `--close-owned` closes every registry tab owned by the current session (matched on `BROWSER_CHAUFFEUR_OWNER_PID`, legacy `nodePid` honored) in one call — never another session's tab, never the user's, never the browser's last page. `closeTab` still handles the single tab one script opened; `--close-owned` is the whole-session sweep to run when a session is finished with the browser. The drainer wires this in: workers close their own browser tabs when an item is truly done (auto-handle: before self-terminating; needs-you: after the user's human step and any follow-up).

### Changed
- SKILL.md — Phase 0 documents the three-layer sweep and how tab ownership is set (launcher vs. your shell profile for interactive sessions); Phase 1 is the single canonical statement that `openTab`/`findTab` are the only sanctioned ways to get a tab; "Resilient Connection" covers why an unregistered tab has no owner.
- `script-quality-standards.md` — concise required entry for `openTab`/`findTab` that points to Phase 1 (no duplicated rationale).
- `templates/tab-registry.js`, `templates/script-template.js`, `HELPERS.md` — header/example text upgraded from "prefer `openTab`" to "always `openTab`, never bare `newPage`"; HELPERS.md documents `openTab`/`closeTab`/`findTab`/`touchTab`.
- `scripts/launch-session.ps1` (repo launcher) — exports `BROWSER_CHAUFFEUR_OWNER_PID` (the session's host process) so every tab a session opens is owned by that session and cleaned up when its window closes.

## [1.8.0] - 2026-06-08

Makes `connectOverCDP` reliable on the persistent profile by addressing the root cause — tab accumulation — instead of only the symptom.

### Added
- **Orphan-tab sweep.** `launch-browser.py` now reclaims tabs left behind by chauffeur scripts that crashed before cleanup. New `templates/tab-registry.js` records each created tab's CDP `targetId` and the creating process's PID in `~/.claude/browser-chauffeur/created-tabs.json`. On every reuse, the launcher closes a tracked tab **only when its creating process is gone** (a genuine orphan), via the raw CDP HTTP `/json/close` endpoint (which never auto-attaches, so it can't hang). It never touches an active session's tab, never touches tabs the user opened (those are never registered), and never closes the browser's last page. This keeps the open-target count low so `connectOverCDP` stays fast and reliable.
- **`openTab(context, url)` / `closeTab(page)` helpers** (exported from `browser-chauffeur-helpers`) bundle tab creation with registration and tab close with unregistration, so a script can't open a tab without making it reclaimable or close one without cleaning up the registry. `registerTab`/`unregisterTab` remain available as lower-level primitives. `closeTab` also parks on `about:blank` instead of closing when it's the browser's last tab, so it never exits the persistent browser.
- `script-template.js` uses `openTab`/`closeTab` (self-cleanup on every run; the sweep is the backstop for crashes).

### Fixed
- `connectOverCDP` no longer hangs indefinitely when the persistent profile is overloaded or has a wedged renderer. Playwright auto-attaches to every open target to build its page tree, and one stuck renderer blocks the whole handshake past any timeout (Playwright's own `{ timeout }` bounds only the socket connect, not target enumeration). `connectBrowser()` in `script-template.js` now races the connect against a 30s hard `Promise.race` timeout as a backstop, so it fails fast with an actionable error instead of hanging forever.
- Removed dangerous recovery guidance. The previous draft told the AI to run `cleanup-browser.py --reset` on a connect timeout — which kills the shared browser for **every** concurrent session and wipes all logins. Recovery is now non-destructive: re-run the launcher (sweeps orphans), retry once, then escalate to the user; never auto-reset the shared browser to fix one's own connect.

### Changed
- SKILL.md — Phase 0 documents the automatic sweep; new "Resilient Connection" section explains the root-cause + backstop design; Phase 3 tab-cleanup and the Phase 4 recovery branch updated for the registry and non-destructive recovery.
- `script-quality-standards.md` — scripts must use the hardened `connectBrowser()` wrapper and register/unregister tabs they create.

## [1.7.1] - 2026-06-04

### Changed
- SKILL.md Phase 3 Step 6: replaced "write a reusable script using `script-template.js`" with guidance to create an instruction-driven spec (SKILL.md with business rules, invariants, selectors, safety rails) for recurring automation needs. Browser-chauffeur creates ad-hoc scripts in `.tmp/` that adapt when selectors drift, rather than a single brittle monolith. See the `teams-message` skill as an example.

## [1.7.0] - 2026-05-29

### Fixed
- Scripts no longer fail with `Cannot find module 'playwright'` on machines where playwright is not installed in the current project. `playwright-core` (the lightweight API-only package) is now auto-installed to `~/.claude/browser-chauffeur/` on first use via the new `setup.js` script, and all templates fall back to that location when the package is not found in the local `node_modules`.
- Switched from `playwright` (full package including bundled browser binaries) to `playwright-core` (API only). Browser-chauffeur connects to an existing Edge/Chrome via CDP — it never launches browsers through Playwright — so the full package was unnecessary.

### Added
- `templates/setup.js` — new one-time setup script. Installs `playwright-core` to `~/.claude/browser-chauffeur/` if not already accessible, and creates a `browser-chauffeur-helpers` shim in the same location. The SKILL.md prerequisite check now runs this script instead of a bare `require` check.

### Changed
- SKILL.md prerequisite check replaced with `node setup.js` — self-healing, no manual `npm install` needed.
- README prerequisite section corrected: removed the incorrect "Requires the playwright MCP plugin" instruction. `playwright-core` is handled automatically.
- HELPERS.md setup section rewritten: removed the hardcoded `npm link` and environment-specific path instructions; describes the auto-install mechanism instead.

## [1.6.0] - 2026-05-28

### Changed
- **BREAKING:** `validate-target.js` renamed to `snapshot-target.js`. The script no longer decides whether the user is logged in — it navigates, waits for URL/network/DOM stability, saves a screenshot, and prints `SCREENSHOT: <path>` + `FINAL_URL: <url>`. The caller (Claude) reads the screenshot with the Read tool and decides. Replaces the previous `VALIDATION_OK` / `VALIDATION_FAILED: potential login page detected` verdict. Output marker changed from `VALIDATION_READY` to `SNAPSHOT_READY`.
- SKILL.md Phase 0 Step 2 rewritten: always read the screenshot — no false-positive recovery branch needed because there is only one decision point (the LLM looking at the screenshot).
- Added `--target-anchor` short-circuit using `locator.waitFor({ state: 'visible' })` for slow-hydrating SPAs.
- Added `waitForUrlStable` inside `snapshot-target.js` (no consecutive URL changes for 2.5s, max 15s). Handles transient SSO pages (Okta "Verifying your identity") that would otherwise be screenshotted mid-redirect.

### Removed
- **BREAKING:** `templates/login-detection.js` deleted entirely. The `isLoginPage(page)` and `waitForLoadedOrLogin(page, options)` helpers no longer export from `browser-chauffeur-helpers`.
  - **Why:** text/DOM heuristics produced both false positives (slow-hydrating SPAs flagged as login pages) and false negatives. Two real-world misses: Trello's "Sign up to see this board" wall has no password field and >100 chars so the detector said "not login"; Okta's "Verifying your identity" SSO transit page has 190 chars (above the too-short threshold) and no password field so the detector also said "not login." Both required user intervention but slipped through as `VALIDATION_OK`.
  - **Migration:** screenshot the page and let the LLM inspect it. For start-of-flow: use `snapshot-target.js` — output is a screenshot path + final URL, and the chauffeur decides. For mid-flow session-expiry checks: `await page.screenshot({ path })` + `console.log` the path; the recovery loop reads it.

## [1.5.0] - 2026-05-27

### Fixed
- `launch-browser.py` no longer crashes on Windows (cp1252 consoles) when printing success/error status. Replaced `✓` and `⚠️` with ASCII equivalents `[OK]` and `[!]`. The browser launched fine — only the success print crashed, leaving callers without PORT/PID output.

### Added
- `cleanupStaleState(page)` helper in `templates/cleanup-stale-state.js` — call at the top of any multi-step batch script to clear leftover dialogs from a previous aborted run. Detects visible `[role="dialog"]`, `[role="alertdialog"]`, and `[role="menu"]` elements, clicks safe-close buttons in priority order (`Cancel` → `Discard` → `OK` → `Close`), falls back to Escape, loops until clear. Exported from `browser-chauffeur-helpers`.
- `verifyAfterMutation(page, predicate, opts)` helper in `templates/verify-after-mutation.js` — safe post-mutation verification for SPAs with virtualized grids. Waits a settle period then retries the predicate up to N times before declaring failure. Prevents false negatives from checking DOM before the SPA's render pass flushes. Exported from `browser-chauffeur-helpers`.
- Anti-Pattern 7 in `anti-patterns.md`: **Fluent UI icon-button exact-match failures** — `getByRole('button', { name: 'Save', exact: true })` and `/^Save$/` regex filters consistently miss Fluent UI buttons because a private-use Unicode font glyph is prepended to `textContent`. Fix: use `page.locator('button:has(span.fui-Button__icon)').filter({ hasText: 'Save' }).first()`. Applies to Outlook web, Teams web, SharePoint, OneDrive.
- Anti-Pattern 8 in `anti-patterns.md`: **Confirmation dialogs without `role` attribute** — some apps render centered `<div>` dialogs with no `role`, causing `[role="dialog"]` detection to return nothing and the confirmation step to be skipped silently. Fix: geometry-based modal detector (`getBoundingClientRect` centering check + button count). Signal: primary action click produces no navigation or network request.
- SKILL.md Common Patterns now documents `cleanupStaleState` and `verifyAfterMutation` with usage guidance.

## [1.4.0] - 2026-05-27

### Fixed
- `validate-target.js` no longer false-positives on logged-in SPAs that hydrate incrementally (Outlook, Teams, large React apps). Login detection now polls for up to 8s when body text is short but no password field is present, waiting for the app shell to render before declaring failure.

### Added
- `--target-anchor=<css-selector>` flag on `validate-target.js` to short-circuit polling as soon as a known app-shell element renders (e.g. `--target-anchor='[data-app-section="CalendarModuleSurface"]'` for Outlook calendar).
- `waitForLoadedOrLogin(page, options)` helper exported from `browser-chauffeur-helpers` for any script that needs the same polling behavior. Single-shot `isLoginPage` is unchanged.

## [1.3.0] - 2026-05-19

### Fixed
- `validate-target.js` now leaves the login page open indefinitely when validation fails, instead of closing it after a fraction of a second. Users can now actually complete the login before the page disappears.
- Browser state now stored globally at `~/.claude/browser-chauffeur/state.json` (instead of `./.tmp/browser-chauffeur.json`) so all Claude instances can discover and reuse the same persistent browser session, **eliminating repeated logins to the same sites**.
- Persistent profile now at `~/.claude/browser-chauffeur/profile/` (instead of `./.tmp/cdp-profile-chauffeur/`) for global access. **Login once, reuse everywhere.**
- `launch-browser.py` now verifies the CDP port actually responds after launch. Detects Edge process sharing preventing port binding and provides troubleshooting steps.
- Browser reuse logic now only checks if CDP port responds (not PID match). Edge process sharing can change PIDs, but as long as CDP is alive, browser is reusable. Fixes instance 2+ failing to reuse instance 1's browser.

### Added
- `cleanup-browser.py` utility for profile management:
  - `--size`: Report persistent profile size and warn if over 1GB
  - `--clean-old`: Remove accumulated fresh-mode profiles from `.tmp/`
  - `--reset`: Kill persistent browser and delete profile (forces fresh login)
  - `--all`: All of the above
- Profile Management section in SKILL.md documenting when and how to reset the browser

## [1.2.0] - 2026-05-15

### Added
- Persistent browser mode (now the default) — a single browser stays running across tasks so logins and sessions survive. Each task opens its own tab and closes it when done.
- `launch-browser.py` automatically detects an already-running persistent browser and reuses it instead of launching a new one. State is tracked in `.tmp/browser-chauffeur.json`.
- Port finding integrated into `launch-browser.py` for persistent mode — no separate `find-port.py` step needed.

### Changed
- Scripts now always create a new tab (`context.newPage()`) instead of reusing an existing page. Tab is closed in `finally` with a guard to keep at least one tab open so the browser doesn't exit.
- Phase 0 simplified from 3 steps (find-port → launch → validate) to 2 steps (ensure-browser → validate).
- Phase 3 no longer kills the browser or deletes the profile — the persistent browser and its sessions stay alive.
- `--fresh` flag available on `launch-browser.py` for one-off sessions that need a clean profile (old behavior).
- Corrected "How it works" to remove incorrect claim about inheriting cookies from the Windows profile — sessions persist because the same browser profile is reused, not because cookies transfer.

## [1.1.2] - 2026-05-13

### Fixed
- `launch-browser.py` now prints `PROFILE_DIR=<path>` alongside `PID=<pid>` so callers can clean up the temp profile after use
- SKILL.md Phase 3 now mandates deleting the temp profile directory after closing the browser — Chromium does not auto-clean user data directories, causing profiles to accumulate and fill disk (discovered as ~46 GB of stale `cdp-profile-*` directories)
- Removed incorrect claim that "The browser will auto-clean on exit"

## [1.1.1] - 2026-05-11

### Fixed
- Edge welcome popup window (a separate small browser window on fresh profiles) no longer hijacks automation — context selection in `validate-target.js` and `script-template.js` now prefers the context with a real http page rather than blindly taking `contexts()[0]`
- Added `--suppress-message-center-popups` and Edge feature disables (`msEdgeSyncPromoRollout`, `msEdgeWelcomePageEnabled`) to `launch-browser.py` to reduce popup frequency

## [1.0.0] - 2026-04-30

### Added
- Initial release
- Two-mode browser automation: MCP Playwright tools (Mode A) and Node.js CDP scripts (Mode B)
- Phase 0: Automated browser detection, CDP launch, and SSO validation
- Phase 0.5: Script runner with mandatory output analysis
- Phase 1-3: Orient, step-by-step execution, and wrap-up methodology
- Phase 4: Script failure recovery loop with screenshot-driven debugging
- Phase 4.5: Script completion analysis with autonomous recovery for partial failures
- Self-healing patterns: overlay dismissal, iframe detection, screenshot-on-failure
- Full script template with connection, overlay, and verification patterns
