// Browser Chauffeur Helpers
// Central export of all shared browser automation utilities
//
// Usage in scripts:
//   const { dismissOverlays, screenshotOnFailure } = require('browser-chauffeur-helpers');
//
// This module is aliased as 'browser-chauffeur-helpers' so scripts can
// import without knowing the full plugin path.
//
// Login detection: deliberately not provided. Detect login state with the
// LLM via screenshot inspection — see snapshot-target.js (start-of-flow)
// and the HELPERS.md "Login detection" section (mid-flow pattern).

const { dismissOverlays } = require('./skills/browser-chauffeur/templates/overlay-dismissal');
const { screenshotOnFailure } = require('./skills/browser-chauffeur/templates/screenshot-on-failure');
const { cleanupStaleState } = require('./skills/browser-chauffeur/templates/cleanup-stale-state');
const { verifyAfterMutation } = require('./skills/browser-chauffeur/templates/verify-after-mutation');
const { openTab, closeTab, findTab, touchTab, registerTab, unregisterTab } = require('./skills/browser-chauffeur/templates/tab-registry');
const { captureFullPageScreenshot } = require('./skills/browser-chauffeur/templates/fullpage-screenshot');

// --- Process guard (installed on require) ---
// A chauffeur script connects to the browser over a live CDP WebSocket, and that
// open socket keeps Node's event loop alive. So a script that hangs on a never-
// resolving await — a click that never lands, a browser.close() that stalls on an
// overloaded browser — never exits on its own: no crash, no timeout, just a
// lingering node process (plus the shell and console host that launched it) that
// lives until the browser itself closes, hours later. During a long automation
// session these pile up faster than they drain and can exhaust memory.
//
// Every chauffeur script requires this module (for openTab/closeTab/dismissOverlays
// et al.), so installing the guard here means it protects every script — including
// a quick ad-hoc one that skips the template's own try/finally exit path. Two nets:
//
//   1. Watchdog — a hung script is force-exited after a bounded wall-clock time.
//      Unref'd so it never delays a script that finishes cleanly (an unref'd timer
//      doesn't hold the loop open, so the process exits the moment its work drains).
//      During a hang the loop stays alive via the CDP socket, so the unref'd timer
//      still fires — exactly the case we're targeting. Generous default (30 min) so
//      a legitimately long single script — a batch of many items, or a wait for a
//      long training video to finish — is never cut off; the rare longer job raises
//      BROWSER_CHAUFFEUR_SCRIPT_TIMEOUT_MS or calls cancelScriptWatchdog().
//   2. Unhandled rejection — a bare IIFE with no top-level .catch() leaves a thrown
//      error unhandled; the socket then keeps the process alive instead of letting
//      it fall over. This handler prints the error and exits(1) promptly, so a
//      failing script frees its process at once rather than waiting out the
//      watchdog, and behaves the same whichever way Node is configured to treat
//      unhandled rejections.
const SCRIPT_TIMEOUT_MS = Number(process.env.BROWSER_CHAUFFEUR_SCRIPT_TIMEOUT_MS) || 30 * 60 * 1000;

let watchdogTimer = null;
if (SCRIPT_TIMEOUT_MS > 0) {
  watchdogTimer = setTimeout(() => {
    console.error(
      `SCRIPT WATCHDOG: forcing exit after ${SCRIPT_TIMEOUT_MS}ms — a browser ` +
      `operation hung and the CDP connection kept the process alive. If this ` +
      `script is meant to run longer, raise BROWSER_CHAUFFEUR_SCRIPT_TIMEOUT_MS ` +
      `or call cancelScriptWatchdog() from browser-chauffeur-helpers.`
    );
    process.exit(1);
  }, SCRIPT_TIMEOUT_MS);
  // Don't let the watchdog itself keep an otherwise-finished script alive.
  watchdogTimer.unref();
}

// Escape hatch for a script that genuinely needs to run past the timeout (e.g.
// waiting out a very long media playback). Call once the long wait is expected.
function cancelScriptWatchdog() {
  if (watchdogTimer) {
    clearTimeout(watchdogTimer);
    watchdogTimer = null;
  }
}

process.on('unhandledRejection', (reason) => {
  console.error('UNHANDLED REJECTION — exiting:', (reason && reason.stack) || reason);
  process.exit(1);
});

module.exports = {
  dismissOverlays,
  screenshotOnFailure,
  cleanupStaleState,
  verifyAfterMutation,
  openTab,
  closeTab,
  findTab,
  touchTab,
  registerTab,
  unregisterTab,
  cancelScriptWatchdog,
  captureFullPageScreenshot,
};
