// Post-mutation verification - checks the changed state of a create, update, or
// delete once the page has re-rendered, typically after a fresh load (SKILL.md
// Key Rules, "Verifying a change").
// `networkidle` fires when the network goes quiet, but virtualized SPA grids (Outlook
// calendar, Teams, SharePoint lists) re-render asynchronously after a server response.
// Checking DOM state immediately after networkidle produces false negatives: the mutated
// item still appears because the render pass hasn't flushed yet.
//
// RECOMMENDED USAGE: Import instead of copying:
//
//   const { verifyAfterMutation } = require('browser-chauffeur-helpers');
//
// Example - confirm a deleted event is gone after a fresh load:
//
//   await page.reload();
//   await page.getByRole('grid').waitFor();   // the list has rendered, so absence means gone
//   const gone = await verifyAfterMutation(page, async () => {
//     const events = await page.getByRole('button', { name: /Budget review/ }).count();
//     return events === 0;
//   });
//   if (!gone) throw new Error('Verify FAILED: event still present after delete');
//
// Options:
//   settleMs (default 1500) - max ms to wait for networkidle before each check
//   retries  (default 3) - how many times to retry before declaring failure
//
// Returns true if predicate eventually passes, false after all retries exhausted.

async function verifyAfterMutation(page, predicate, { settleMs = 1500, retries = 3 } = {}) {
  for (let attempt = 0; attempt < retries; attempt++) {
    // 1. Wait for any in-flight network requests to complete (real network condition).
    //    If the page is already idle or stays busy past settleMs, catch and continue.
    await page.waitForLoadState('networkidle', { timeout: settleMs }).catch(() => {});

    // 2. Yield two animation frames so the SPA's pending render pass flushes.
    //    requestAnimationFrame is a real browser event, not a timed delay.
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));

    // 3. Check the predicate against current DOM/page state.
    const passed = await predicate().catch(() => false);
    if (passed) return true;

    if (attempt < retries - 1) {
      console.log(`  [verify] Predicate not yet satisfied (attempt ${attempt + 1}/${retries}), retrying...`);
    }
  }
  console.log('  [verify] Predicate failed after all retries');
  return false;
}

module.exports = { verifyAfterMutation };
