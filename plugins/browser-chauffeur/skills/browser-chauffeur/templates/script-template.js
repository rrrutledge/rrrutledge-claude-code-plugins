// Browser automation script template. Scripts receive a validated CDP port
// from browser-chauffeur Phase 0 via --cdp-port. They do NOT contain browser
// detection, fallback, or target-load validation - that is handled by Claude
// interactively during Phase 0 before any script runs.
//
// Each script opens its own tab with openTab (never bare newPage - openTab
// registers the tab so the launcher's sweep can reclaim it), works in it, and
// closes it with closeTab when done. The browser is persistent - it stays
// running across tasks so logins survive. closeTab guards against closing the
// last tab (which would exit the browser and lose all sessions).

const { chromium } = (() => {
  try { return require('playwright-core'); }
  catch { return require(require('path').join(require('os').homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'playwright-core')); }
})();

const { dismissOverlays, openTab, closeTab, screenshotOnFailure } = (() => {
  try { return require('browser-chauffeur-helpers'); }
  catch { return require(require('path').join(require('os').homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'browser-chauffeur-helpers')); }
})();

// --- browser connection ---
// Splits only on the FIRST "=" - a bare .split('=')[1] truncates any value that
// itself contains "=" (a URL query string, a CSS attribute selector). Any
// ad-hoc script adding its own --flag=value argument (e.g. --url=) should copy
// this helper rather than reintroducing the bare split pattern.
function argValue(flag) {
  const prefix = `--${flag}=`;
  const arg = process.argv.find(a => a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : undefined;
}

const cdpPort = argValue('cdp-port') || '9222';

// connectOverCDP auto-attaches to EVERY open target to build its page tree. On a
// persistent profile that has accumulated many tabs - or has a single wedged
// renderer - that enumeration can hang indefinitely (Playwright's own `timeout`
// option only bounds the socket connect, not post-connect target enumeration).
// Race it against a hard timeout so a wedged browser fails fast with an
// actionable error instead of hanging forever.
const CONNECT_TIMEOUT_MS = 30000;

async function connectBrowser() {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(
        `connectOverCDP did not complete within ${CONNECT_TIMEOUT_MS}ms on port ${cdpPort}. ` +
        `The persistent profile likely has too many open tabs or a wedged renderer. ` +
        `Reset the profile with cleanup-browser.py --reset (then re-launch and log in again), or retry.`
      )),
      CONNECT_TIMEOUT_MS,
    );
  });
  try {
    return await Promise.race([chromium.connectOverCDP(`http://localhost:${cdpPort}`), timeout]);
  } finally {
    clearTimeout(timer);
  }
}

async function run() {
  const browser = await connectBrowser();
  const contexts = browser.contexts();
  const context = contexts.length > 1
    ? (contexts.find(ctx => ctx.pages().some(p => p.url().startsWith('http'))) ?? contexts[0])
    : (contexts[0] ?? await browser.newContext());
  // openTab creates the tab AND registers it in one step, so a later launch can
  // reclaim it if this script crashes before closeTab runs (see tab-registry.js).
  const page = await openTab(context);

  const results = { succeeded: [], failed: [] };

  try {
    // Script navigates to its target URL. The browser is already validated
    // for SSO during Phase 0, so navigation will succeed.
    // await page.goto('https://target-url-here.example.com');

    // Dismiss first-run overlays before app-specific waits
    await dismissOverlays(page);

    // --- steps go here ---
    // Use semantic selectors: getByRole, getByLabel, locator('[aria-label="..."]')
    // Use element-based waits: locator.waitFor(), page.waitForURL(), page.waitForLoadState()
    // Check page.frames() if content appears empty - it may be in an iframe
    // Track results as you go (push to succeeded/failed arrays)

    // --- VERIFICATION (required) ---
    // Re-check the goal: did the task actually complete?
    // Example: navigate back to source, confirm items moved/deleted
    // Example: validate extracted data has required fields

    const failCount = results.failed.length;
    const successCount = results.succeeded.length;

    console.log(`Summary: ${successCount} succeeded, ${failCount} failed`);

    if (failCount === 0) {
      console.log('Verification passed ✅');
    } else {
      console.error(`Verification FAILED - ${failCount} errors remain`);
      results.failed.forEach(item => console.error('  -', item));
    }
  } catch (e) {
    await screenshotOnFailure(context, 'run-failed');
    throw e;
  } finally {
    // closeTab closes (or parks, if it's the last tab) AND unregisters in one
    // step. Reaching finally means this tab is not an orphan; only tabs whose
    // script crashed before this point stay registered for the sweep to reclaim.
    await closeTab(page);
    await browser.close().catch(() => {});
  }
}

run().catch(e => { console.error(e.message); process.exit(1); });
