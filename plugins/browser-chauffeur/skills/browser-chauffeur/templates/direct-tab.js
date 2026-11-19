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
const { CDPSession, httpText, httpJson, withTimeout } = require('./cdp-session');
const { recordTarget, unregisterTab } = require('./tab-registry');

const CALL_TIMEOUT_MS = 30000;

class DirectTab {
  constructor(cdpPort, target) {
    this.cdpPort = cdpPort;
    this.targetId = target.id;
    this.session = new CDPSession(target.webSocketDebuggerUrl);
  }

  send(method, params) {
    return withTimeout(this.session.send(method, params), CALL_TIMEOUT_MS, `${method} did not answer within ${CALL_TIMEOUT_MS}ms`);
  }

  async evaluate(fn, arg) {
    const expression = typeof fn === 'function' ? `(${fn})(${JSON.stringify(arg)})` : fn;
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) {
      throw new Error('evaluate failed: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    }
    return r.result.value;
  }

  // Waits for DOMContentLoaded of this navigation's own document, matched by its
  // loaderId, so a late event from an earlier navigation can't end the wait. The
  // event can arrive before Page.navigate answers, so every one is collected
  // from the start. A same-document (hash) navigation has no loaderId and no new
  // document to wait for.
  async goto(url) {
    await this.send('Page.enable');
    await this.send('Page.setLifecycleEventsEnabled', { enabled: true });
    const seen = new Set();
    let notify = () => {};
    const listener = m => {
      if (m.method === 'Page.lifecycleEvent' && m.params.name === 'DOMContentLoaded') {
        seen.add(m.params.loaderId);
        notify();
      }
    };
    this.session.on(listener);
    try {
      const { errorText, loaderId } = await this.send('Page.navigate', { url });
      if (errorText) throw new Error(`navigation to ${url} failed: ${errorText}`);
      if (!loaderId) return;
      const ready = new Promise(resolve => {
        notify = () => { if (seen.has(loaderId)) resolve(); };
        notify();
      });
      await withTimeout(ready, CALL_TIMEOUT_MS, `${url} did not load within ${CALL_TIMEOUT_MS}ms`);
    } finally {
      this.session.off(listener);
    }
  }

  // clip is in viewport CSS pixels, as in Playwright; CDP's clip is in document
  // coordinates, so the scroll offset is added. The tab comes to the front
  // first, since a background tab may never produce the frame a capture waits on.
  async screenshot({ path, clip } = {}) {
    await this.send('Page.bringToFront');
    const params = { format: 'png' };
    if (clip) {
      const { x, y } = await this.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));
      params.clip = { x: clip.x + x, y: clip.y + y, width: clip.width, height: clip.height, scale: 1 };
    }
    const { data } = await this.send('Page.captureScreenshot', params);
    const buffer = Buffer.from(data, 'base64');
    if (path) {
      fs.mkdirSync(require('path').dirname(path), { recursive: true });
      fs.writeFileSync(path, buffer);
    }
    return buffer;
  }

  // Clicks the center of the first visible match. The scroll is instant, so the
  // position read right after it is where the element ends up, and the click is
  // refused when something else (a modal, an overlay) sits on top of that point.
  async click(selector) {
    const box = await this.evaluate((sel) => {
      const visible = (e) => {
        const r = e.getBoundingClientRect();
        const s = getComputedStyle(e);
        return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && Number(s.opacity) > 0;
      };
      const el = [...document.querySelectorAll(sel)].find(visible);
      if (!el) return { error: 'no visible element matches' };
      el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
      const r = el.getBoundingClientRect();
      const x = r.x + r.width / 2;
      const y = r.y + r.height / 2;
      const top = document.elementFromPoint(x, y);
      if (!top || !(el === top || el.contains(top))) return { error: 'another element covers' };
      return { x, y };
    }, selector);
    if (box.error) throw new Error(`click: ${box.error} ${selector}`);
    await this.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: box.x, y: box.y, button: 'none' });
    for (const type of ['mousePressed', 'mouseReleased']) {
      await this.send('Input.dispatchMouseEvent', { type, x: box.x, y: box.y, button: 'left', clickCount: 1 });
    }
  }

  // Inserts text into the focused element: input events fire, key events don't.
  async type(text) {
    await this.send('Input.insertText', { text });
  }

  // Closes the tab, or parks it on about:blank when it is the browser's last
  // page, so the persistent browser never exits.
  // The tab stays in the registry when the close fails, so the sweep reclaims it.
  async close() {
    try {
      const pages = (await httpJson(`http://localhost:${this.cdpPort}/json`)).filter(t => t.type === 'page');
      if (pages.length > 1) await httpText(`http://localhost:${this.cdpPort}/json/close/${this.targetId}`);
      else await this.send('Page.navigate', { url: 'about:blank' });
      unregisterTab(this.targetId);
    } catch {
      // left registered for the sweep
    } finally {
      try { this.session.close(); } catch { /* already closed */ }
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

module.exports = { openDirectTab };
