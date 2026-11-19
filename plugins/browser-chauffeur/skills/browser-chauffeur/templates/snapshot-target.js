// Snapshot the target - navigate to a URL, wait for the page to settle, and
// save a screenshot of whatever's on screen. Generic utility: the caller
// decides what to do with the screenshot (verify login state, confirm an
// app is loaded, capture before/after a mutation, etc.).
//
// Usage: node snapshot-target.js --cdp-port=9222 --url=https://example.com [--target-anchor=<selector>]
//
// Behavior: navigates to --url, waits for URL stability (no redirects for
// 2.5s, max 15s), network idle, and DOM render stability, then saves a
// screenshot. Prints the screenshot path + the URL the browser actually
// landed on. Makes no claims about page contents - the caller inspects the
// screenshot with the Read tool.
//
// --target-anchor: optional CSS selector. When provided, the script returns
// as soon as the anchor element becomes visible instead of running the
// screenshot-stability loop. Useful for slow-hydrating SPAs that animate
// indefinitely (so screenshot diffing never settles) but expose a stable
// app-shell element (e.g. '[data-app-section="CalendarModuleSurface"]' for
// Outlook calendar).
//
// When the connect times out on a hung tab (connect-browser.js), the snapshot
// is taken through a fresh direct tab instead (direct-tab.js), with the same
// waits, and the output adds the connect error naming the hung tab(s). The
// direct tab is left open and registered, like the Playwright one.

const { connectBrowser, openDirectTab, registerTab, touchTab, exitAfterFlush } = (() => {
  try { return require('browser-chauffeur-helpers'); }
  catch { return require(require('path').join(require('os').homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'browser-chauffeur-helpers')); }
})();

// Splits only on the FIRST "=" - a bare .split('=')[1] truncates any value that
// itself contains "=" (a URL query string like ?jl=123, a CSS attribute selector
// like [role="dialog"]), silently handing the script a mangled argument.
function argValue(flag) {
  const prefix = `--${flag}=`;
  const arg = process.argv.find(a => a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : undefined;
}

const cdpPort = argValue('cdp-port') || '9222';
const targetUrl = argValue('url');
const targetAnchor = argValue('target-anchor') || null;

if (!targetUrl) {
  console.error('Usage: node snapshot-target.js --cdp-port=<port> --url=<url> [--target-anchor=<css-selector>]');
  process.exit(1);
}

// Resolves once the URL stops changing for `settleMs` consecutive ms, or
// after `maxMs` total, to the last URL actually read. Captures the destination
// URL after any client-side redirect chain - SSO handshakes, region/locale
// routing, AB-test redirects - instead of screenshotting an in-flight redirect.
// getUrl is async so the direct-tab path can read location.href through
// evaluate; a null from it (a read that failed mid-redirect) counts as a change.
async function waitForUrlStable(getUrl, settleMs = 2500, maxMs = 15000) {
  const start = Date.now();
  let lastUrl = await getUrl();
  let lastRead = lastUrl;
  let lastChange = Date.now();
  while (Date.now() - start < maxMs) {
    await new Promise(r => setTimeout(r, 200));
    const url = await getUrl();
    if (url != null) lastRead = url;
    if (url !== lastUrl || url == null) {
      lastUrl = url;
      lastChange = Date.now();
    } else if (Date.now() - lastChange >= settleMs) {
      break;
    }
  }
  return lastRead;
}

// With an anchor, waits for it to show (waitForAnchor); otherwise takes
// screenshots until two consecutive captures are byte-identical (the page has
// stopped rendering) or attempts run out. shoot returns a PNG buffer.
async function waitForRender(waitForAnchor, shoot) {
  if (targetAnchor) {
    await waitForAnchor().catch(() => {});
    return;
  }
  let previous = null;
  for (let attempt = 0; attempt < 5; attempt++) {
    const current = await shoot();
    if (previous && Buffer.compare(current, previous) === 0) return;
    previous = current;
    if (attempt < 4) await new Promise(r => setTimeout(r, 1000));
  }
}

function report(screenshotPath, finalUrl, elapsedMs) {
  console.log('SNAPSHOT_READY');
  console.log(`SCREENSHOT: ${screenshotPath}`);
  console.log(`FINAL_URL: ${finalUrl}`);
  console.log(`ELAPSED_MS: ${elapsedMs}`);
  console.log('');
  console.log('Read the screenshot with the Read tool to see what is on the page.');
}

async function snapshotDirect(connectError) {
  console.log(connectError.message);
  const navStart = Date.now();
  const tab = await openDirectTab(cdpPort, targetUrl);
  try {
    // An evaluate that lands while a redirect commits fails; that reads as the
    // URL still changing.
    const finalUrl = await waitForUrlStable(() => tab.evaluate('location.href').catch(() => null));
    await waitForRender(async () => {
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline) {
        const shown = await tab.evaluate((sel) => {
          const el = document.querySelector(sel);
          return !!el && el.getClientRects().length > 0;
        }, targetAnchor).catch(() => false);
        if (shown) return;
        await new Promise(r => setTimeout(r, 250));
      }
    }, () => tab.screenshot());
    const screenshotPath = `.tmp/snapshot-target-${Date.now()}.png`;
    await tab.screenshot({ path: screenshotPath });
    report(screenshotPath, finalUrl, Date.now() - navStart);
  } finally {
    tab.session.close();
  }
}

async function snapshot() {
  let browser;
  try {
    browser = await connectBrowser(cdpPort);
  } catch (e) {
    if (e.code !== 'CDP_CONNECT_TIMEOUT') throw e;
    return snapshotDirect(e);
  }
  // Prefer contexts that already have a real http page - Edge sometimes launches
  // a welcome popup window as a separate context and it may sort before the main window.
  const contexts = browser.contexts();
  const context = contexts.length > 1
    ? (contexts.find(ctx => ctx.pages().some(p => p.url().startsWith('http'))) ?? contexts[0])
    : (contexts[0] ?? await browser.newContext());
  const page = await context.newPage();
  // Register the tab under this session before anything else can throw, so a
  // caller that later needs to re-find this exact tab (findTab) - a gate hit
  // right here at orientation, before any task-specific openTab call - gets a
  // tab that's actually owned and re-findable, not an orphan only the age/count
  // sweep backstop will ever touch.
  await registerTab(context, page);

  try {
    const navStart = Date.now();
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });

    await waitForUrlStable(async () => page.url());
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    await waitForRender(
      () => page.locator(targetAnchor).first().waitFor({ state: 'visible', timeout: 8000 }),
      () => page.screenshot(),
    );

    const screenshotPath = `.tmp/snapshot-target-${Date.now()}.png`;
    await page.screenshot({ path: screenshotPath, fullPage: false });

    const finalUrl = page.url();
    const elapsedMs = Date.now() - navStart;
    // Refresh the recorded URL now that navigation has settled - it was
    // registered above against the pre-navigation about:blank.
    await touchTab(context, page);

    report(screenshotPath, finalUrl, elapsedMs);
  } finally {
    // Leave the tab open - it's registered above, so the caller can find it
    // again later via findTab even after this script's own process has exited.
    // The caller may want to interact with the page next, hand it to the user
    // (e.g. to complete a sign-in), or close it.
    await browser.close().catch(() => {});
  }
}

snapshot().then(() => exitAfterFlush(0), e => { console.error(e.message); exitAfterFlush(1); });
