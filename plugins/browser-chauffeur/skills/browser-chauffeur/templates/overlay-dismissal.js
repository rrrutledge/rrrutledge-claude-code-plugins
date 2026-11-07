// Overlay dismissal - call after navigating to a target app, before
// waiting for app-specific elements. A fresh browser profile will show
// first-run overlays (Edge sync prompts, cookie banners, "What's new"
// modals) that block the real UI and cause element waits to time out.
//
// RECOMMENDED USAGE: Import this module instead of copying:
//
//   const { dismissOverlays } = require('browser-chauffeur-helpers');
//
// This ensures all scripts automatically get improvements when this module
// is updated. The old pattern of copying/inlining helpers meant improvements
// never propagated to existing scripts.
//
// SAFETY: Playwright's getByRole name matching is a case-insensitive
// SUBSTRING match unless `exact: true` is passed. An un-exact 'Close' matches
// any button whose accessible name merely contains that word - including a
// real page action like GitHub's "Close pull request" or "Close issue"
// button, which this helper would then click for real. `exact: true` closes
// that gap. As a second layer, isOverlayLike() additionally requires the
// candidate button to actually sit inside something that looks like an
// overlay (a dialog role, a fixed/sticky-positioned banner, or a
// modal/cookie/consent/toast-named container) before clicking it - a plain
// in-page button, however it's labeled, is never a first-run overlay and is
// left alone.

async function isOverlayLike(locator) {
  return locator.evaluate(el => {
    let node = el;
    for (let depth = 0; node && depth < 6; depth++, node = node.parentElement) {
      const role = node.getAttribute && node.getAttribute('role');
      if (role === 'dialog' || role === 'alertdialog') return true;

      const style = window.getComputedStyle(node);
      const isFixedOrSticky = style.position === 'fixed' || style.position === 'sticky';
      const hasStackingZIndex = (parseInt(style.zIndex, 10) || 0) > 0;
      if (isFixedOrSticky && hasStackingZIndex) return true;

      const idAndClass = `${node.id || ''} ${node.className || ''}`;
      if (/overlay|modal|consent|cookie|banner|toast|snackbar|dialog/i.test(idAndClass)) return true;
    }
    return false;
  }).catch(() => false);
}

async function dismissOverlays(page) {
  const overlayButtons = [
    page.getByRole('button', { name: 'Got it', exact: true }),
    page.getByRole('button', { name: 'Dismiss', exact: true }),
    page.getByRole('button', { name: 'Close', exact: true }),
    page.getByRole('button', { name: /Not now/i }),
  ];
  for (const btn of overlayButtons) {
    const count = await btn.count();
    for (let i = 0; i < count; i++) {
      const candidate = btn.nth(i);
      if (!(await isOverlayLike(candidate))) continue;
      console.log('  Dismissing overlay...');
      await candidate.click();
      // Wait for the button to actually disappear (state-based wait)
      // instead of a fixed delay - follows Browser Chauffeur best practices
      await candidate.waitFor({ state: 'hidden', timeout: 2000 }).catch(() => {});
      break;
    }
  }
}

module.exports = { dismissOverlays };
