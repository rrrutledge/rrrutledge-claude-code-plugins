// openDirectTab - a fresh tab driven over its own CDP WebSocket, for when
// connectBrowser() times out on a hung tab (connect-browser.js).
//
// connectOverCDP has to enumerate every tab, so one wedged renderer blocks it.
// This opens a new tab through the browser's HTTP endpoint (PUT /json/new) and
// talks only to that tab's own webSocketDebuggerUrl, so no other tab is ever
// touched. The tab is recorded in the tab registry under this session, so the
// sweep reclaims it like any openTab tab.
//
// The DirectTab's calls are listed in HELPERS.md (openDirectTab). evaluate and
// screenshot keep Playwright's signatures so captureEvidence
// (evidence-screenshot.js) works on either kind of tab.
//
//   const tab = await openDirectTab(cdpPort, 'https://example.com');
//   try { console.log(await tab.evaluate(() => document.title)); }
//   finally { await tab.close(); }

const fs = require('fs');
const { CDPSession, httpText, httpJson } = require('./cdp-session');
const { recordTarget, unregisterTab } = require('./tab-registry');

const CALL_TIMEOUT_MS = 30000;

class DirectTab {
  constructor(cdpPort, target) {
    this.cdpPort = cdpPort;
    this.targetId = target.id;
    this.currentUrl = target.url;
    this.session = new CDPSession(target.webSocketDebuggerUrl);
  }

  send(method, params) {
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${method} did not answer within ${CALL_TIMEOUT_MS}ms`)), CALL_TIMEOUT_MS);
    });
    return Promise.race([this.session.send(method, params), timeout]).finally(() => clearTimeout(timer));
  }

  url() { return this.currentUrl; }

  async evaluate(fn, arg) {
    const expression = typeof fn === 'function' ? `(${fn})(${JSON.stringify(arg)})` : fn;
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) {
      throw new Error('evaluate failed: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    }
    return r.result.value;
  }

  // Waits on the load event rather than polling readyState, which reads the old
  // document's 'complete' until the navigation commits.
  async goto(url) {
    await this.send('Page.enable');
    let timer;
    let onLoad;
    const loaded = new Promise((resolve, reject) => {
      onLoad = resolve;
      timer = setTimeout(() => reject(new Error(`${url} did not load within ${CALL_TIMEOUT_MS}ms`)), CALL_TIMEOUT_MS);
    });
    this.session.on(m => { if (m.method === 'Page.loadEventFired') onLoad(); });
    try {
      const { errorText } = await this.send('Page.navigate', { url });
      if (errorText) throw new Error(`navigation to ${url} failed: ${errorText}`);
      await loaded;
    } finally {
      clearTimeout(timer);
    }
    this.currentUrl = await this.evaluate('location.href');
  }

  // clip is in viewport CSS pixels, as in Playwright; CDP's clip is in document
  // coordinates, so the scroll offset is added.
  async screenshot({ path, clip } = {}) {
    const params = { format: 'png' };
    if (clip) {
      const { x, y } = await this.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));
      params.clip = { x: clip.x + x, y: clip.y + y, width: clip.width, height: clip.height, scale: 1 };
    }
    const { data } = await this.send('Page.captureScreenshot', params);
    const buffer = Buffer.from(data, 'base64');
    if (path) fs.writeFileSync(path, buffer);
    return buffer;
  }

  async click(selector) {
    const box = await this.evaluate((sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      el.scrollIntoView({ block: 'center', inline: 'center' });
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    }, selector);
    if (!box) throw new Error(`click: no element matches ${selector}`);
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
      await this.send('Input.dispatchMouseEvent', { type, x: box.x, y: box.y, button: 'left', clickCount: 1 });
    }
  }

  async type(text) {
    await this.send('Input.insertText', { text });
  }

  // Closes the tab, or parks it on about:blank when it is the browser's last
  // page, so the persistent browser never exits.
  async close() {
    try {
      const pages = (await httpJson(`http://localhost:${this.cdpPort}/json`)).filter(t => t.type === 'page');
      if (pages.length > 1) await httpText(`http://localhost:${this.cdpPort}/json/close/${this.targetId}`);
      else await this.send('Page.navigate', { url: 'about:blank' });
    } catch {
      // best-effort: the sweep reclaims a tab this misses
    } finally {
      try { this.session.close(); } catch { /* already closed */ }
      unregisterTab(this.targetId);
    }
  }
}

// The tab opens blank and then navigates, so its load event arrives on a socket
// that is already listening.
async function openDirectTab(cdpPort, url) {
  const target = await httpJson(`http://localhost:${cdpPort}/json/new`, { method: 'PUT' });
  recordTarget(target.id, url || target.url);
  const tab = new DirectTab(cdpPort, target);
  try {
    await tab.session.ready;
    if (url) await tab.goto(url);
  } catch (e) {
    await tab.close();
    throw e;
  }
  return tab;
}

module.exports = { openDirectTab, DirectTab };
