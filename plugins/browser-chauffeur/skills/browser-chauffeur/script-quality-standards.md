# Script Quality Standards

**All browser automation scripts must comply with these requirements.** Reference this file when writing AND when validating scripts (Phase 5 in SKILL.md). See `templates/script-template.js` for a complete reference script that satisfies all of these.

## ❌ BANNED: Fixed Delays

**Never use:**
```javascript
await page.waitForTimeout(1000);
await new Promise(r => setTimeout(r, 1000));
```

**Why:** Wastes time when ready sooner, fails when page is slow. You don't know what you're waiting for.

**Always use element/condition-based waits:**
```javascript
await locator.waitFor({ state: 'visible', timeout: 5000 });
await page.waitForLoadState('networkidle');
await page.waitForFunction(() => document.readyState === 'complete');
```

**Exception:** Short poll loops (≤300ms interval) with a deadline when no waitable locator exists.

## ❌ BANNED: Retry Loops in Scripts

**Never add retry logic inside a script:**
```javascript
// ❌ WRONG - script retrying its own failures
for (let attempt = 1; attempt <= 3; attempt++) {
  const result = await doAction(page, item);
  if (result.success) break;
  console.log(`Retry ${attempt}/3...`);
}
```

**Why:** Retries paper over the real problem. If the script does the right thing - waits for the correct DOM signals before acting - it works the first time, just like a human using the UI. When a wait times out, the script should fail clearly, and browser-chauffeur's recovery loop diagnoses the failure (via screenshots), fixes the script, and re-runs it. Scripts are the directions; the chauffeur handles road closures.

**Instead, wait for the right signal:**
```javascript
// ✅ CORRECT - wait for the element that proves readiness
await deleteBtn.waitFor({ state: 'visible', timeout: 5000 });
await deleteBtn.click();
```

## ✅ REQUIRED: Get tabs via `openTab` / `findTab`

Create every tab with `openTab(context, url)`, and reuse **your own session's** tab with `findTab(context, predicate)` - never bare `context.newPage()` or `context.pages().find(...)`. `openTab` registers the tab against its owning session so the sweep can reclaim it; an unregistered tab has no owner and is only cleaned up once it goes idle or the count cap is hit. `findTab` is owner-scoped: it returns only a tab this session opened (`null` otherwise, so you `openTab` a fresh one), so parallel sessions on the same URL never grab each other's tab - a bare `pages().find(...)` would. Close tabs you created with `closeTab` in a `finally`; never pass a tab you only *found* to `closeTab`. **SKILL.md Phase 1 has the full rationale** - this is the one place it's spelled out.

## ✅ REQUIRED: Verification Code

**Every script must output explicit success/failure:**
```javascript
// ❌ WRONG - no verification
console.log('Done.');

// ✅ CORRECT - explicit verification
if (failCount === 0) {
  console.log('Verification passed ✅');
} else {
  console.error(`Verification FAILED - ${failCount} errors`);
}
```

This enables Phase 4 autonomous recovery.

## ✅ REQUIRED: Semantic Selectors

Use `aria-label`, `role`, visible text - **never CSS class selectors** (they change across deployments).

## ✅ REQUIRED: Browser Connection

Scripts receive `--cdp-port=<port>` from Claude. Connect with the hardened `connectBrowser()` helper from `script-template.js`, which wraps `chromium.connectOverCDP('http://localhost:<port>')` in a 30s `Promise.race` timeout - **never call `connectOverCDP` bare**, or the script can hang forever when the persistent profile is overloaded or has a wedged renderer (see SKILL.md "Resilient Connection"). No browser detection logic in scripts. They do **not** contain browser detection, login validation, or the Edge/Chrome fallback - that is handled by Claude interactively during Phase 0 before any script runs.

## ✅ REQUIRED: Navigation

Scripts must navigate to their target URL themselves - don't assume the browser is already there. Since Phase 0 already validated that the target loads, navigating again is just a reload and keeps the script self-contained.

## ✅ REQUIRED: Guaranteed Process Exit

Every script - a quick one-off as much as one built from the full template - must guarantee its process exits when its work ends. A script connects to the browser over a live CDP socket, and that open socket keeps Node's event loop alive: if a step throws or hangs, `browser.close()` never runs, the socket stays open, and the process lingers as an orphan (with the shell and console host that launched it) until the browser closes hours later. Accumulate a session's worth of these and they exhaust memory.

Wrap the whole body in `try { ... } finally { await closeTab(page); await browser.close().catch(() => {}); }`, and end the file with the guaranteed-exit line so a rejection can never leave the process hanging:

```javascript
run().catch(e => { console.error(e.message); process.exit(1); });
```

A bare `(async () => { ... })()` IIFE with no top-level `.catch()` fails this: a throw anywhere inside leaves the rejection unhandled while the socket keeps the process alive. Either use the `run()`-plus-`.catch()` shape above, or attach `.catch(e => { console.error(e.message); process.exit(1); })` to the IIFE's own promise.

**Backstop (automatic, no action needed):** requiring `browser-chauffeur-helpers` installs a process guard - an unhandled-rejection handler that force-exits, and a wall-clock watchdog (default 30 min, override with `BROWSER_CHAUFFEUR_SCRIPT_TIMEOUT_MS`) that force-exits a genuinely hung script. This catches a script that skips the pattern above, but it's the safety net, not the plan: a correct script still exits promptly through its own `finally`. A script that legitimately runs past the watchdog window (a long media wait) calls `cancelScriptWatchdog()` from the helpers.

## Additional Requirements

- `console.log` after each major step for progress tracking
- Check `page.frames()` when `body.innerText` is unexpectedly short
- Use `page.route()` for request interception (not `frame.route()` - it doesn't exist)
- Include `dismissOverlays(page)` after navigation (see Common Patterns in SKILL.md)
- Save a diagnostic screenshot in catch blocks (see Common Patterns in SKILL.md)
