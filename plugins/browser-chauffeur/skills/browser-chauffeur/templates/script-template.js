// Browser automation script template: a complete script that satisfies every
// rule in ../script-quality-standards.md. It takes the CDP port Phase 0
// validated via --cdp-port, opens its own tab with openTab, and closes it with
// closeTab when done (SKILL.md Phase 1 and Phase 3 hold the tab rules).

const { connectBrowser, dismissOverlays, openTab, closeTab, screenshotOnFailure, exitAfterFlush } = (() => {
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

async function run() {
  // Bounded connect: a hung tab fails fast with an error naming it
  // (connect-browser.js) instead of hanging the script.
  const browser = await connectBrowser(cdpPort);
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

run().then(() => exitAfterFlush(0), e => { console.error(e.message); exitAfterFlush(1); });
