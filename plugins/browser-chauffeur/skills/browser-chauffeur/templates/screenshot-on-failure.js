// Screenshot-on-failure - call from catch blocks so each failed attempt
// produces a screenshot for debugging. The screenshots help diagnose whether the failure
// was a login page, an overlay, a CAPTCHA, or something else.
//
// RECOMMENDED USAGE: Import this module instead of copying:
//
//   const { screenshotOnFailure } = require('browser-chauffeur-helpers');
//
// This ensures all scripts automatically get improvements when this module
// is updated.

const fs = require('fs');

async function screenshotOnFailure(context, label) {
  const diagPage = context.pages()[0];
  if (!diagPage) return;
  fs.mkdirSync('.tmp', { recursive: true });
  const screenshotPath = `.tmp/diag-${label}-${Date.now()}.png`;
  await diagPage.screenshot({ path: screenshotPath }).catch(() => {});
  console.log(`  Diagnostic screenshot: ${screenshotPath}`);
}

module.exports = { screenshotOnFailure };
