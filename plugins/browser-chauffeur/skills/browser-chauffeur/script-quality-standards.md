# Script Quality Standards

The rules for what a browser automation script's code contains live here, apart from getting and closing tabs and resolving packages, which SKILL.md holds (**Tabs and Packages** below points to each).
Write each script to these rules, and check it against them before it runs (SKILL.md Phase 5).
`templates/script-template.js` is a complete script that satisfies all of them.

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
await page.waitForURL(/\/done/);
await page.waitForLoadState('networkidle');
await page.waitForFunction(() => document.readyState === 'complete');
```

**Exception:** a short poll (≤300ms interval) with a deadline, exiting as soon as its condition is met, when no waitable locator exists.

## ❌ BANNED: Retrying an Action

**Never repeat an action inside a script to paper over its failure:**
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

Re-checking a condition after an action, without repeating the action, is a wait rather than a retry; `verifyAfterMutation` (SKILL.md Key Rules, **Verifying a change**) is that kind of wait.

## ✅ REQUIRED: Semantic Selectors

Select by `role`, label (`getByLabel`, `aria-label`), and visible text.
CSS classes an app generates or names for its own styling change across deployments and tenants, so they never appear in a selector.
A component library's published static class names are stable API and count as semantic: Fluent UI's `fui-*` classes are the case this skill relies on (`anti-patterns.md` Anti-Pattern 7).

**Strict mode.** A locator matching more than one element throws.
When a wrapper `div` and its `button` share a label or text, select the button by role: `getByRole('button', { name: '...' })` (`anti-patterns.md` Anti-Pattern 1).
Use `.first()` only when you've confirmed the matches are genuinely equivalent.

**Broaden to the HTML tag when role finds nothing.** Older or semantic UIs use real `li`, `a`, and `span` elements as action targets with no `role` attribute, and role-based selectors skip them.
When `getByRole` or `[role="..."]` returns nothing for an element you can see, select by the underlying tag; `anti-patterns.md` Anti-Pattern 5 has the snippet that finds the true tag.

## ✅ REQUIRED: Actions Through the Locator API

Perform every action that should trigger a server call or framework event through Playwright's locator API: `locator.click()`, `locator.fill()`, `locator.dispatchEvent('click')`.
Use `page.evaluate` to *read* DOM state.
A native `element.click()` inside `evaluate` bypasses framework synthetic event handlers (React, Fluent UI): the UI may update while no API call fires, and the change vanishes on refresh (`anti-patterns.md` Anti-Pattern 2).

**The one exception: an out-of-process iframe.** The locator API reaches only the page target `connectOverCDP` attached to, so content inside a cross-origin iframe is clicked with a DOM `.click()` through `Runtime.evaluate` on a `CDPSession` attached to the iframe's own target (SKILL.md Phase 1, **Out-of-process iframes**).

## ✅ REQUIRED: Visibility Checks, Not Presence

When checking whether an element is *shown* (a dialog, menu, backdrop, tooltip), filter by computed visibility (`display`, `visibility`, `offsetParent`).
Many frameworks keep dialog DOM mounted and toggle `display: none` to hide closed steps, so a bare `querySelector(...) !== null` returns true for an element the user can't see (`anti-patterns.md` Anti-Pattern 4).

## ✅ REQUIRED: Tabs and Packages

- Get every tab through `openTab` or `findTab` (SKILL.md Phase 1, **Getting a tab**).
- Close the tabs the script opened with `closeTab` in a `finally` (SKILL.md Phase 3, **Tab cleanup**).
- Resolve `playwright-core` and `browser-chauffeur-helpers` with the try/catch fallback (SKILL.md **Prerequisite Check**).

## ✅ REQUIRED: Browser Connection

Scripts receive `--cdp-port=<port>` from Claude, validated during Phase 0.
Phase 0 also owns browser launch and the login check, so a script carries no browser detection or login validation of its own.

Connect with the hardened `connectBrowser()` from `script-template.js` (SKILL.md **Resilient Connection** gives the reason).

## ✅ REQUIRED: Navigation

Scripts must navigate to their target URL themselves - don't assume the browser is already there. Since Phase 0 already validated that the target loads, navigating again is just a reload and keeps the script self-contained.

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

Phase 4 reads these lines to decide whether a run succeeded.
What counts as evidence that a mutation succeeded is SKILL.md Key Rules, **Verifying a change**.

## ✅ REQUIRED: Guaranteed Process Exit

Every script - a quick one-off as much as one built from the full template - must guarantee its process exits when its work ends. A script connects to the browser over a live CDP socket, and that open socket keeps Node's event loop alive: if a step throws or hangs, `browser.close()` never runs, the socket stays open, and the process lingers as an orphan (with the shell and console host that launched it) until the browser closes hours later. Accumulate a session's worth of these and they exhaust memory.

Wrap the whole body in `try { ... } finally { await closeTab(page); await browser.close().catch(() => {}); }`, and end the file with the guaranteed-exit line so a rejection can never leave the process hanging:

```javascript
run().catch(e => { console.error(e.message); process.exit(1); });
```

A bare `(async () => { ... })()` IIFE with no top-level `.catch()` fails this: a throw anywhere inside leaves the rejection unhandled while the socket keeps the process alive. Either use the `run()`-plus-`.catch()` shape above, or attach `.catch(e => { console.error(e.message); process.exit(1); })` to the IIFE's own promise.

The helpers' process guard is a backstop behind this rule, not a substitute for it (`HELPERS.md` **Process guard**).

## ✅ REQUIRED: Helper Calls and Logging

- `console.log` after each major step for progress tracking.
- `dismissOverlays(page)` after navigation, before any app-specific wait (SKILL.md Common Patterns, **Overlay Dismissal**).
- `screenshotOnFailure(context, label)` in catch blocks (SKILL.md Common Patterns, **Screenshot on Failure**).
- A `page.frames()` check when `body.innerText` is unexpectedly short (SKILL.md Phase 1, **Iframe detection**).
- `page.route()` for request interception, on the page object; frames have no `route()` method, so interception for content inside a frame is set on its page too.
