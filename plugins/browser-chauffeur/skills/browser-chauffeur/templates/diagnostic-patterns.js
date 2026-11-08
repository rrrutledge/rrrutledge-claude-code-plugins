// Diagnostic patterns - used during Phase 4 autonomous recovery. When a
// step fails, these snippets gather visual evidence (screenshots, button
// inventories, timing comparisons) so you can diagnose the root cause from
// what the page actually shows, not from the error text.

// Pattern 1: Element visibility test
async function checkVisibility(page, selector) {
  const element = page.locator(selector);
  const isVisible = await element.isVisible({ timeout: 3000 }).catch(() => false);
  console.log('Element visible:', isVisible);
  await page.screenshot({ path: '.tmp/diag-element.png' });
}

// Pattern 2: Button enumeration
async function enumerateButtons(page) {
  const buttons = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('button')).map(btn => ({
      text: btn.textContent?.trim(),
      ariaLabel: btn.getAttribute('aria-label'),
      visible: btn.offsetParent !== null
    })).filter(b => b.visible && (b.text || b.ariaLabel));
  });
  console.log('Buttons:', JSON.stringify(buttons, null, 2));
}

// Pattern 3: Timing test
async function timingTest(page) {
  console.log('Immediately after click:');
  await page.screenshot({ path: '.tmp/diag-1-immediate.png' });

  await page.waitForLoadState('networkidle', { timeout: 3000 }).catch(() => {});
  console.log('After networkidle:');
  await page.screenshot({ path: '.tmp/diag-2-after-wait.png' });
}

module.exports = { checkVisibility, enumerateButtons, timingTest };
