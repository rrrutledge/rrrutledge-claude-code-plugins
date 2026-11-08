// Cleanup stale dirty-editor state - call at the top of any multi-step batch script
// to clear leftover dialogs from a previous aborted run. When a script aborts mid-flow
// (e.g., Save click times out), the editor may be left open with dirty changes. The next
// run then hits a "Discard changes?" dialog that intercepts pointer events and blocks
// all subsequent clicks.
//
// RECOMMENDED USAGE: Import instead of copying:
//
//   const { cleanupStaleState } = require('browser-chauffeur-helpers');
//
// Call before your main automation loop:
//
//   await cleanupStaleState(page);
//
// Options:
//   maxIterations (default 6) - max dialog-dismissal rounds before giving up

async function cleanupStaleState(page, { maxIterations = 6 } = {}) {
  const PRIORITY = ['Cancel', 'Discard', 'OK', 'Close'];
  // :is() lets us match multiple roles in one compound selector
  const POPUP_SEL = ':is([role="dialog"], [role="alertdialog"], [role="menu"])';

  // Returns the count of currently visible popups - used to verify dismissal actually worked
  const visiblePopupCount = () => page.waitForFunction(
    (sel) => Array.from(document.querySelectorAll(sel)).filter(el => {
      const cs = getComputedStyle(el);
      return cs.display !== 'none' && cs.visibility !== 'hidden' && el.offsetParent !== null;
    }).length,
    POPUP_SEL,
    { timeout: 2000 }
  ).then(h => h.jsonValue()).catch(() => 0);

  const waitUntilNoPopups = () => page.waitForFunction(
    (sel) => Array.from(document.querySelectorAll(sel)).every(el => {
      const cs = getComputedStyle(el);
      return cs.display === 'none' || cs.visibility === 'hidden' || el.offsetParent === null;
    }),
    POPUP_SEL,
    { timeout: 3000 }
  ).catch(() => {});

  for (let i = 0; i < maxIterations; i++) {
    const count = await visiblePopupCount();
    if (count === 0) break;

    let dismissed = false;
    for (const label of PRIORITY) {
      // Use Playwright locator (not page.evaluate + element.click) so the click goes
      // through the framework event system - raw DOM clicks bypass React/Fluent UI handlers.
      // hasText does a substring match, which handles Fluent UI icon-glyph prefixes on text.
      const btn = page.locator(`${POPUP_SEL} button`).filter({ hasText: label }).first();
      if ((await btn.count()) > 0 && await btn.isVisible().catch(() => false)) {
        await btn.click();
        // Verify the popup actually disappeared before moving on
        await waitUntilNoPopups();
        console.log(`  [cleanup] Dismissed stale popup via "${label}"`);
        dismissed = true;
        break;
      }
    }

    if (!dismissed) {
      // No matching priority button - press Escape as last resort
      await page.keyboard.press('Escape');
      console.log('  [cleanup] Pressed Escape to close stale popup');
      // Verify via DOM state before looping again
      await waitUntilNoPopups();
    }
  }
}

module.exports = { cleanupStaleState };
