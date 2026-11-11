// Shared Playwright helpers for editing a Canva design in the browser.
// references/browser-editing.md covers the browser setup and the calls these leave to the caller.
//
// Units: the Arrange panel's px inputs are the design's own units (a 1920x1080 slide reads 1920x1080),
// so a placement plan written in design px needs no conversion.
//
// Waits: every wait is for a condition (a panel showing, a value reading back, a layer count changing),
// polled at 250 ms by until() or waitStable(). The few bounded waits for a change that may not come (maybe())
// say why next to them.

const path = require('path');
const fs = require('fs');
const http = require('http');

const NM = path.join(require('os').homedir(), '.claude', 'browser-chauffeur', 'node_modules');
const req = (name) => { try { return require(name); } catch { return require(path.join(NM, name)); } };
const { chromium } = req('playwright-core');
const { openTab, closeTab } = req('browser-chauffeur-helpers');

function argValue(flag, dflt) {
  const prefix = `--${flag}=`;
  const arg = process.argv.find(a => a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : dflt;
}

const cdpPort = () => argValue('cdp-port', '9222');
const near = (a, b, t) => Math.abs(a - b) <= t;
const sameBox = (p, q, t = 1.5) => !!(p && q && near(p.w, q.w, t) && near(p.h, q.h, t) && near(p.x, q.x, t) && near(p.y, q.y, t));
function pngDims(f) {
  const b = fs.readFileSync(f);
  if (b.toString('latin1', 1, 4) !== 'PNG') throw new Error(`${f} is not a PNG; convert it first`);
  return [b.readUInt32BE(16), b.readUInt32BE(20)];
}
const POLL_MS = 250;
const tick = () => new Promise(r => setTimeout(r, POLL_MS));

// Polls cond() until it returns a truthy value and returns that value; throws what on timeout.
async function until(cond, { timeout = 10000, what = 'condition' } = {}) {
  const end = Date.now() + timeout;
  for (;;) {
    const v = await cond();
    if (v) return v;
    if (Date.now() > end) throw new Error(`timed out after ${timeout} ms waiting for ${what}`);
    await tick();
  }
}

// Like until(), but returns null instead of throwing: for a change that may legitimately not happen.
const maybe = (cond, opts) => until(cond, opts).catch(() => null);

// Polls read() until it returns the same serialized value for quietMs, then returns it.
async function waitStable(read, { quietMs = 1000, timeout = 20000 } = {}) {
  const end = Date.now() + timeout;
  let last = JSON.stringify(await read());
  let since = Date.now();
  while (Date.now() < end) {
    await tick();
    const cur = JSON.stringify(await read());
    if (cur !== last) { last = cur; since = Date.now(); } else if (Date.now() - since >= quietMs) return JSON.parse(cur);
  }
  return JSON.parse(last);
}

const CONNECT_TIMEOUT_MS = 30000;

async function connectBrowser(port = cdpPort()) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(
      `connectOverCDP did not complete within ${CONNECT_TIMEOUT_MS}ms on port ${port}. ` +
      `An idle Canva renderer freezes and hangs the connect: run canva-edit.js --nudge --cdp-port=${port}, then retry.`)), CONNECT_TIMEOUT_MS);
  });
  try { return await Promise.race([chromium.connectOverCDP(`http://127.0.0.1:${port}`), timeout]); }
  finally { clearTimeout(timer); }
}

const getJson = (u) => new Promise((res, rej) => http.get(u, r => { let s = ''; r.on('data', d => s += d); r.on('end', () => { try { res(JSON.parse(s)); } catch (e) { rej(e); } }); }).on('error', rej));

// Wakes a frozen renderer without Playwright: shrink each window holding a Canva design 20 px and restore it
// over the browser-level CDP socket. Restoring to the read width can leave the window ~1 px wider per run.
async function nudge(port = cdpPort()) {
  const { ws: WebSocket } = require(path.join(NM, 'playwright-core', 'lib', 'utilsBundle'));
  const v = await getJson(`http://127.0.0.1:${port}/json/version`);
  const pages = (await getJson(`http://127.0.0.1:${port}/json/list`)).filter(t => t.type === 'page' && /canva\.com/.test(t.url));
  const sock = new WebSocket(v.webSocketDebuggerUrl);
  let n = 0; const pend = {};
  // A CDP error reply rejects, so a window that closes mid-nudge fails loudly instead of being left resized.
  const send = (method, params = {}) => new Promise((r, j) => { pend[++n] = (m) => (m.error ? j(new Error(`${method}: ${m.error.message}`)) : r(m.result)); sock.send(JSON.stringify({ id: n, method, params })); });
  sock.on('message', d => { const m = JSON.parse(d.toString()); if (m.id && pend[m.id]) pend[m.id](m); });
  await new Promise((r, j) => { sock.on('open', r); sock.on('error', j); });
  const seen = new Set();
  try {
    for (const p of pages) {
      const w = await send('Browser.getWindowForTarget', { targetId: p.id });
      if (seen.has(w.windowId)) continue;
      const id = w.windowId;
      seen.add(id);
      const state = w.bounds.windowState;
      const width = async () => (await send('Browser.getWindowBounds', { windowId: id })).bounds.width;
      if (state !== 'normal') await send('Browser.setWindowBounds', { windowId: id, bounds: { windowState: 'normal' } });
      const w0 = await width();
      await send('Browser.setWindowBounds', { windowId: id, bounds: { width: w0 - 20 } });
      // The resize is what wakes the renderer; wait until the window reports it, at most 3 s.
      await maybe(async () => (await width()) === w0 - 20, { timeout: 3000 });
      await send('Browser.setWindowBounds', { windowId: id, bounds: { width: w0 } });
      if (state !== 'normal') await send('Browser.setWindowBounds', { windowId: id, bounds: { windowState: state } });
    }
    for (const p of pages.filter(p => /canva\.com\/design/.test(p.url))) await send('Target.activateTarget', { targetId: p.id });
  } finally {
    sock.close();
  }
  return seen.size;
}

// The design id is the path segment after /design/. The tag query param marks this script's own tab,
// so a rerun finds it again and the login tab is never touched.
const designId = (url) => (url.match(/\/design\/([A-Za-z0-9_-]+)/) || [])[1];

function taggedUrl(url, tag) {
  const u = new URL(url);
  u.searchParams.set(tag, '1');
  return u.toString();
}

// The editing canvas of a 1920x1080 design: the most common on-screen 16:9 div wider than 600 px.
// s converts screen px to design px. Returns null while the canvas isn't on screen.
async function findPageRect(tab) {
  return tab.evaluate(() => {
    const c = [...document.querySelectorAll('div')].map(e => e.getBoundingClientRect()).filter(r => r.width > 600 && Math.abs(r.width / r.height - 16 / 9) < 0.02 && r.x > 70 && r.y > 60);
    const key = (r) => [Math.round(r.x), Math.round(r.y), Math.round(r.width)].join(',');
    const cnt = {}; c.forEach(r => { cnt[key(r)] = (cnt[key(r)] || 0) + 1; });
    c.sort((a, b) => cnt[key(b)] - cnt[key(a)]);
    const r = c[0];
    return r ? { x: r.x, y: r.y, w: r.width, h: r.height, s: r.width / 1920 } : null;
  });
}

async function pageRect(tab) {
  const r = await findPageRect(tab);
  if (!r) throw new Error('no 16:9 page canvas on screen: the design is still loading, zoomed out of view, or not a 1920x1080 design');
  return r;
}

// Loaded means the canvas is on screen and stays put for a second.
async function ready(tab) {
  await tab.bringToFront().catch(() => {});
  await until(() => findPageRect(tab), { timeout: 120000, what: 'the design canvas' });
  await waitStable(() => findPageRect(tab), { quietMs: 1000 });
}

// Connects, finds or opens this script's tagged tab on the design, keeps the renderer awake, runs fn.
// The tab closes afterward (closeTab never closes the last tab).
async function withDesign(url, tag, fn) {
  const browser = await connectBrowser();
  const context = browser.contexts().find(c => c.pages().some(p => /^https?:/.test(p.url()))) || browser.contexts()[0];
  const mine = taggedUrl(url, tag);
  let tab = context.pages().find(p => p.url().includes(designId(url)) && p.url().includes(`${tag}=1`));
  try {
    if (!tab) { tab = await openTab(context); await tab.goto(mine, { waitUntil: 'domcontentloaded', timeout: 90000 }); }
    await tab.setViewportSize({ width: 1528, height: 1400 });
    const cdp = await context.newCDPSession(tab);
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
    await cdp.send('Page.setWebLifecycleState', { state: 'active' }).catch(() => {});
    await ready(tab);
    return await fn(tab);
  } finally {
    if (tab) await closeTab(tab).catch(() => {});
    await browser.close().catch(() => {});
  }
}

const pageLoc = (tab, n) => tab.locator(`[data-role="timeline-scene"][aria-label="Page ${n}"]`);
const shownPages = (tab) => tab.locator('[data-role="timeline-scene"]').evaluateAll(els => els.map(e => +(e.getAttribute('aria-label') || '').slice(5)).filter(Boolean));
// The editor footer's "N / M" page counter: a leaf element in the bottom 80 px of the window, so slide text such
// as "24/7" never matches.
const currentPage = (tab) => tab.evaluate(() => {
  const e = [...document.querySelectorAll('body *')].find(e => e.children.length === 0 && /^\s*\d+\s*\/\s*\d+\s*$/.test(e.textContent) && e.getBoundingClientRect().top > innerHeight - 80);
  const m = e && e.textContent.match(/(\d+)\s*\/\s*(\d+)/);
  return m ? { n: +m[1], of: +m[2] } : null;
});

// The page strip only shows in the Pages view; turn it on when it's off.
async function showPageStrip(tab) {
  if ((await shownPages(tab)).length) return;
  await tab.getByRole('button', { name: /^Pages/ }).first().click();
  await until(async () => (await shownPages(tab)).length, { what: 'the page strip' });
}

// The footer's "N / M" counter gives the page count without scrolling the strip.
async function pageCount(tab) {
  const c = await until(() => currentPage(tab), { what: 'the page counter' });
  return c.of;
}

// The strip renders a window of thumbnails; step toward the target until its thumb exists, then click it
// and wait for the footer counter to read it.
async function gotoPage(tab, n) {
  await showPageStrip(tab);
  for (let k = 0; k < 60 && !(await pageLoc(tab, n).count()); k++) {
    const nums = await shownPages(tab);
    const pick = n > Math.max(...nums) ? Math.max(...nums) : Math.min(...nums);
    await pageLoc(tab, pick).scrollIntoViewIfNeeded();
    await pageLoc(tab, pick).click();
    await maybe(async () => JSON.stringify(await shownPages(tab)) !== JSON.stringify(nums), { timeout: 3000 });
  }
  await pageLoc(tab, n).scrollIntoViewIfNeeded();
  await pageLoc(tab, n).click();
  await until(async () => (await currentPage(tab) || {}).n === n, { what: `page ${n} to be current` });
  await waitStable(() => findPageRect(tab), { quietMs: 700 });
}

const positionButton = (tab) => tab.getByRole('button', { name: 'Position', exact: true });

// (1490, 165) is empty grey canvas at withDesign's 1528x1400 viewport; a click there clears any selection,
// which the toolbar's Position button disappearing confirms.
async function deselect(tab) {
  await tab.keyboard.press('Escape');
  await tab.keyboard.press('Escape');
  await tab.mouse.click(1490, 165);
  await maybe(async () => !(await positionButton(tab).count()), { timeout: 3000 });
}

// Each side-panel tab shows when its own content does.
const TAB_SHOWN = {
  Arrange: (tab) => tab.getByText('Advanced', { exact: true }).first().isVisible(),
  Layers: async (tab) => (await layerRows(tab)).length > 0,
};

async function showTab(tab, name) {
  const loc = tab.getByText(name, { exact: true }).first();
  try { await loc.click({ timeout: 8000 }); } catch { await loc.click({ force: true, timeout: 8000 }); }
  await until(() => TAB_SHOWN[name](tab).catch(() => false), { what: `the ${name} tab` });
}

// The Position panel (Arrange + Layers tabs) only opens with something selected: select the top-left corner
// of the page, falling back to a point on the right, then press Position.
async function openPositionPanel(tab) {
  if (await tab.getByText('Arrange', { exact: true }).first().isVisible().catch(() => false)) return;
  for (const [fx, fy] of [[0.12, 0.06], [0.83, 0.31]]) {
    if (await positionButton(tab).count()) break;
    await deselect(tab);
    const p = await pageRect(tab);
    await tab.mouse.click(p.x + p.w * fx, p.y + p.h * fy);
    await maybe(async () => (await positionButton(tab).count()) > 0, { timeout: 3000 });
  }
  await positionButton(tab).first().click();
  await tab.getByText('Arrange', { exact: true }).first().waitFor({ timeout: 10000 });
}

// Layer rows are the 280-360 px wide, 40-70 px tall buttons whose left edge sits at x 80-100 in the side panel.
// Each read tags them data-cvrow=<index> so a click targets exactly the row that was read.
function layerRows(tab) {
  return tab.evaluate(() => {
    const bs = [...document.querySelectorAll('button')].filter(b => {
      const r = b.getBoundingClientRect();
      return r.x > 80 && r.x < 100 && r.width > 280 && r.width < 360 && r.height > 40 && r.height < 70 && r.y > 200 && r.bottom < window.innerHeight - 30;
    });
    document.querySelectorAll('[data-cvrow]').forEach(e => e.removeAttribute('data-cvrow'));
    bs.forEach((b, i) => b.setAttribute('data-cvrow', String(i)));
    return bs.map(b => ({ label: (b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 70) }));
  });
}

// The full list ends with the Background row.
const rowButtons = (tab) => until(async () => { const r = await layerRows(tab); return r.some(x => x.label === 'Background') && r; }, { what: 'the Layers list' });

async function scrollLayersTop(tab) {
  await tab.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find(b => { const r = b.getBoundingClientRect(); return r.x > 80 && r.x < 100 && r.width > 280 && r.width < 360 && r.height > 40 && r.height < 70; });
    let p = b && b.parentElement; while (p && p.scrollHeight <= p.clientHeight + 2) p = p.parentElement;
    if (p) p.scrollTop = 0;
  });
}

const pxInputs = (tab) => tab.locator('input').evaluateAll(els => els.map((e, i) => [i, e.value]).filter(([, v]) => /px$|°$/.test(v)));

async function readArrange(tab) {
  await showTab(tab, 'Arrange');
  const v = (await until(async () => { const p = await pxInputs(tab); return p.length >= 4 && p; }, { what: 'the Arrange values' })).map(([, s]) => parseFloat(s));
  return { w: v[0], h: v[1], x: v[2], y: v[3] };
}

// An image (not a frame or text) is selected when the toolbar offers the Eraser.
const isImageSelected = async (tab) => (await tab.getByRole('button', { name: 'Eraser', exact: true }).count()) > 0;

// Clicking a layer row selects that layer. The row carries no selected state, so each caller waits on what the
// selection changes: the Arrange box (layers, selectLayer) or the row order (moveSelected).
const clickRow = (tab, i) => tab.locator(`[data-cvrow="${i}"]`).click();

// Every layer on the current page, front to back, with its box in design px.
// Rows are clicked one at a time because the Arrange values only show for the selected layer.
// Only rows visible in the panel are read: a page with more layers than fit fails on the missing Background row.
async function layers(tab) {
  await openPositionPanel(tab);
  await showTab(tab, 'Layers');
  await scrollLayersTop(tab);
  const rows = await rowButtons(tab);
  const out = [];
  let prev = null;
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].label === 'Background') { out.push({ i, label: 'Background', img: false, box: null }); continue; }
    await showTab(tab, 'Layers');
    const now = await rowButtons(tab);
    if (!now[i] || now[i].label !== rows[i].label) throw new Error('Layers list changed while reading it');
    await clickRow(tab, i);
    const box = await readSelectedBox(tab, prev);
    out.push({ i, label: rows[i].label, img: await isImageSelected(tab), box });
    prev = box;
  }
  return out;
}

// Reads the selected layer's box once the Arrange values have moved off the previous layer's box and settled.
async function readSelectedBox(tab, prev) {
  if (prev) await maybe(async () => !sameBox(await readArrange(tab), prev, 0.01), { timeout: 2000 });
  return waitStable(() => readArrange(tab), { quietMs: 300 }).catch(() => null);
}

async function selectLayer(tab, row) {
  await openPositionPanel(tab);
  await showTab(tab, 'Layers');
  const rws = await rowButtons(tab);
  if (!rws[row.i] || rws[row.i].label !== row.label) throw new Error(`layer ${row.i} is no longer "${row.label}"`);
  await clickRow(tab, row.i);
  const box = row.box ? await until(async () => { const b = await readArrange(tab); return sameBox(b, row.box, 0.5) && b; }, { timeout: 4000, what: `layer ${row.i}'s box` }).catch(() => { throw new Error(`layer ${row.i} moved since it was read`); }) : await waitStable(() => readArrange(tab), { quietMs: 300 });
  return box;
}

async function setRatioLock(tab, want) {
  await showTab(tab, 'Arrange');
  const st = await tab.evaluate(() => {
    const lab = [...document.querySelectorAll('label, span, p')].find(l => l.innerText && l.innerText.trim() === 'Ratio');
    if (!lab) return 'nolabel';
    const lr = lab.getBoundingClientRect();
    const b = [...document.querySelectorAll('button[aria-pressed]')].find(b => { const r = b.getBoundingClientRect(); return Math.abs(r.x - lr.x) < 12 && r.y > lr.y && r.y < lr.y + 60; });
    if (!b) return 'nobutton';
    b.setAttribute('data-cvratio', '1');
    return b.getAttribute('aria-pressed');
  });
  if (st !== 'true' && st !== 'false') throw new Error('Ratio lock control not found: ' + st);
  if (st !== String(want)) {
    await tab.locator('[data-cvratio="1"]').click();
    await until(async () => (await tab.locator('[data-cvratio="1"]').getAttribute('aria-pressed')) === String(want), { what: 'the ratio lock to switch' });
  }
}

// Types into the selected layer's W/H/X/Y inputs, waiting for each value to read back.
// With the ratio locked, set h alone first, then x and y.
async function setArrange(tab, vals) {
  await showTab(tab, 'Arrange');
  const order = ['w', 'h', 'x', 'y'];
  for (const k of order) {
    if (vals[k] == null) continue;
    const want = Math.round(vals[k]);
    const idx = (await pxInputs(tab)).map(([i]) => i);
    const el = tab.locator('input').nth(idx[order.indexOf(k)]);
    await el.click();
    await tab.keyboard.press('Control+A');
    await tab.keyboard.type(String(want));
    await tab.keyboard.press('Enter');
    await maybe(async () => near(parseFloat((await pxInputs(tab))[order.indexOf(k)][1]), want, 1), { timeout: 3000 });
  }
  return readArrange(tab);
}

// Height-driven sizing with the ratio locked keeps the image's aspect; one retry covers a dropped keystroke.
async function placeSelected(tab, to) {
  await setRatioLock(tab, true);
  if (to.h != null) await setArrange(tab, { h: to.h });
  else if (to.w != null) await setArrange(tab, { w: to.w });
  let got = await setArrange(tab, { x: to.x, y: to.y });
  const ok = (g) => (to.h == null || near(g.h, to.h, 1.5)) && (to.w == null || to.h != null || near(g.w, to.w, 1.5)) && near(g.x, to.x, 1.5) && near(g.y, to.y, 1.5);
  if (!ok(got)) got = await setArrange(tab, to.h != null ? { h: to.h, x: to.x, y: to.y } : { w: to.w, x: to.x, y: to.y });
  if (!ok(got)) throw new Error(`position did not stick: ${JSON.stringify(got)} wanted ${JSON.stringify(to)}`);
  return got;
}

// Upload thumbnails in the side panel, newest first.
async function uploadThumbs(tab) {
  return tab.evaluate(() => [...document.querySelectorAll('img')].filter(e => {
    const r = e.getBoundingClientRect();
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return hit && (hit === e || (e.parentElement && e.parentElement.parentElement && e.parentElement.parentElement.contains(hit)));
  }).map(e => { const r = e.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2, left: r.left, top: r.top, w: r.width, h: r.height, nw: e.naturalWidth, nh: e.naturalHeight, src: e.src.slice(0, 160) }; })
    .filter(r => r.left > 60 && r.left < 440 && r.top > 250 && r.w > 40 && r.h > 40)
    .sort((a, b) => Math.round(a.top / 30) - Math.round(b.top / 30) || a.left - b.left));
}

// Uploads a PNG through the open Uploads panel and returns its thumbnail once Canva has processed it:
// a new first thumb, served from Canva (not a blob: preview), with the file's aspect ratio, holding steady.
async function uploadFile(tab, file) {
  const [fw, fh] = pngDims(file);
  const ar = fw / fh;
  await tab.locator('input[type=file]').first().waitFor({ state: 'attached', timeout: 15000 });
  const t0 = await waitStable(async () => (await uploadThumbs(tab)).map(t => t.src + t.nw), { quietMs: 1000 });
  const before = t0.length ? t0[0] : '';
  await tab.locator('input[type=file]').first().setInputFiles(file.replace(/\\/g, '/'));
  const isNew = async () => { const f = (await uploadThumbs(tab))[0]; return f && f.src + f.nw !== before && f.nw > 0 && !/^blob:/.test(f.src) && near(f.nw / f.nh, ar, ar * 0.015) && f; };
  await until(isNew, { timeout: 120000, what: 'the uploaded thumbnail' });
  const f = await waitStable(isNew, { quietMs: 1500 });
  if (!f) throw new Error('uploaded thumbnail did not settle for ' + path.basename(file));
  return f;
}

// Adds the file to the current page as a new, selected, front-most image and returns its box.
async function addImage(tab, file) {
  const [fw, fh] = pngDims(file);
  await deselect(tab);
  if (!(await tab.getByText('Upload files', { exact: true }).first().isVisible().catch(() => false))) await tab.getByText('Uploads', { exact: true }).first().click();
  const pick = await uploadFile(tab, file);
  await tab.mouse.click(pick.x, pick.y);
  await until(() => isImageSelected(tab), { what: 'the added image to be selected' });
  await openPositionPanel(tab);
  const p = await waitStable(() => readArrange(tab), { quietMs: 500 });
  if (!near(p.w / p.h, fw / fh, 0.02 * fw / fh)) throw new Error(`placed image aspect ${p.w / p.h} differs from the file's ${fw / fh}`);
  return p;
}

// Deletes a layer and waits for the Layers list to lose a row.
// A Frame element ignores the Delete key, so it goes through its row's context menu; anything else takes Delete.
async function deleteLayer(tab, row) {
  await selectLayer(tab, row);
  await showTab(tab, 'Layers');
  const n0 = (await rowButtons(tab)).length;
  const gone = () => until(async () => { await openPositionPanel(tab); await showTab(tab, 'Layers'); return (await rowButtons(tab)).length === n0 - 1; }, { what: 'the deleted layer to leave the Layers list' });
  if (row.label !== 'Frame element') {
    // Re-clicking the row hands keyboard focus to the selection, which is the state Delete was tested in.
    await clickRow(tab, row.i);
    await tab.keyboard.press('Delete');
    return gone();
  }
  const rb = await tab.locator(`[data-cvrow="${row.i}"]`).boundingBox();
  await tab.mouse.move(rb.x + rb.width / 2, rb.y + rb.height / 2);
  await until(() => tab.evaluate((y) => {
    document.querySelectorAll('[data-cvmenu]').forEach(e => e.removeAttribute('data-cvmenu'));
    const b = [...document.querySelectorAll('button[aria-label="Open context menu for layer element"]')].find(b => { const q = b.getBoundingClientRect(); return q.y >= y - 2 && q.y <= y + 60; });
    if (b) b.setAttribute('data-cvmenu', '1');
    return !!b;
  }, rb.y), { timeout: 5000, what: 'the layer context menu button' });
  await tab.locator('[data-cvmenu="1"]').click({ force: true });
  const del = tab.getByRole('menuitem', { name: /^Delete/ });
  await del.first().waitFor({ timeout: 5000 });
  await del.first().click();
  return gone();
}

// Presses Arrange's Forward or Backward once for the selected layer and waits for Canva to save the change.
// Rows carry only generic labels ("Image"), so the caller re-reads the layers to see where the layer landed.
// Canva only steps past layers the selection overlaps, and disables the button when there are none left that
// way; the stacking then makes no visible difference, so this returns false.
async function moveSelected(tab, direction) {
  await showTab(tab, 'Arrange');
  const btn = tab.getByRole('button', { name: direction, exact: true }).first();
  // The button re-enables a moment after the Arrange tab shows, so a single read can wrongly see it disabled.
  if (!(await maybe(() => btn.isEnabled(), { timeout: 2000 }))) return false;
  await btn.click();
  await waitSaved(tab);
  return true;
}

// Waits out Canva's header save status ("Saving...") so a reload never drops the last edit. Only the header is
// read, so slide text such as "Cost Savings" never matches.
const saving = (tab) => tab.evaluate(() => [...document.querySelectorAll('body *')].some(e => e.children.length === 0 && /^Saving/i.test(e.textContent.trim()) && e.getBoundingClientRect().top < 60));

async function waitSaved(tab) {
  await deselect(tab);
  // Canva shows "Saving" a moment after an edit; give it up to 2 s to appear before waiting for it to clear.
  await maybe(() => saving(tab), { timeout: 2000 });
  await until(async () => !(await saving(tab)), { timeout: 60000, what: 'Canva to finish saving' });
}

// Saves, reloads, and returns to page n: the only trustworthy check that an edit stuck.
async function reloadToPage(tab, n) {
  await waitSaved(tab);
  for (let k = 0; k < 2; k++) {
    await tab.reload({ waitUntil: 'domcontentloaded', timeout: 90000 });
    await ready(tab);
    try { await gotoPage(tab, n); return; } catch (e) { if (k) throw e; }
  }
}

// Whether a page counter ("N / M") is showing on screen: on screen, hit-testable, and not faded out.
// In Present mode only the presenter bar's counter can show; the editor's sits under the presentation.
const counterShowing = (tab) => tab.evaluate(() => [...document.querySelectorAll('body *')].filter(e => e.children.length === 0 && /^\s*\d+\s*\/\s*\d+\s*$/.test(e.textContent)).some(e => {
  const r = e.getBoundingClientRect();
  if (!r.height || r.top >= innerHeight || r.bottom <= 0) return false;
  const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
  if (!hit || !(hit === e || e.contains(hit) || hit.contains(e))) return false;
  let o = 1;
  for (let p = e; p; p = p.parentElement) o *= +getComputedStyle(p).opacity;
  return o > 0.05;
}));

// Full-resolution screenshot of the current page through Present full screen at a 1920x1080 viewport, with the
// pointer parked in a corner. It waits for the presented slide to fill the screen, its images to finish loading,
// and the presenter controls to fade.
async function presentShot(tab, file) {
  const vp = tab.viewportSize();
  await deselect(tab);
  const closeBtn = tab.getByRole('button', { name: 'Close', exact: true });
  if (await closeBtn.count()) await closeBtn.first().click().catch(() => {});
  await tab.setViewportSize({ width: 1920, height: 1080 });
  try {
    await tab.locator('button[aria-label="Present full screen"]').first().click();
    await until(() => tab.evaluate(() => {
      const b = document.querySelector('button[aria-label="Present full screen"]');
      const r = b && b.getBoundingClientRect();
      const hit = r && document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return !b || !hit || !(b === hit || b.contains(hit));
    }), { what: 'Present mode to cover the editor' });
    await until(() => counterShowing(tab), { what: 'the presenter controls' });
    await until(() => tab.evaluate(() => [...document.images].every(i => i.complete)), { what: 'the slide images' });
    // The controls hide only after a pointer move inside the presentation followed by stillness.
    await tab.mouse.move(960, 540);
    await tab.mouse.move(1919, 2, { steps: 5 });
    await until(async () => !(await counterShowing(tab)), { timeout: 20000, what: 'the presenter controls to fade' });
    await tab.screenshot({ path: file });
  } finally {
    // Leave the editor as it was even when the shot fails, so the next page's fixed-coordinate clicks still land.
    await tab.keyboard.press('Escape');
    if (vp) await tab.setViewportSize(vp);
    await ready(tab);
  }
}

// Visible text runs on the current page (design px), from the DOM rather than OCR. Button labels floating over
// the canvas (Canva's "Ask Canva") are editor chrome, not page text.
async function textRuns(tab) {
  const pr = await pageRect(tab);
  return tab.evaluate((pr) => {
    const res = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let nd;
    while ((nd = walker.nextNode())) {
      const t = nd.textContent.trim();
      if (!t || nd.parentElement.closest('button')) continue;
      const rg = document.createRange(); rg.selectNodeContents(nd);
      for (const r of rg.getClientRects()) {
        if (r.width < 2 || r.height < 8) continue;
        if (r.x < pr.x || r.y < pr.y || r.right > pr.x + pr.w || r.bottom > pr.y + pr.h) continue;
        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        if (!hit || !(hit.contains(nd.parentElement) || nd.parentElement.contains(hit))) continue;
        res.push({ t: t.slice(0, 80), x: (r.x - pr.x) / pr.s, y: (r.y - pr.y) / pr.s, w: r.width / pr.s, h: r.height / pr.s });
      }
    }
    return res;
  }, pr);
}

// Centres (screen px) of each on-page whole-word occurrence of word, top to bottom.
async function findWord(tab, word) {
  const pr = await pageRect(tab);
  return tab.evaluate(({ pr, word }) => {
    const res = [];
    const re = new RegExp('(?<![\\p{L}\\p{N}])' + word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?![\\p{L}\\p{N}])', 'gu');
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let nd;
    while ((nd = w.nextNode())) {
      for (const m of nd.textContent.matchAll(re)) {
        const i = m.index;
        const rg = document.createRange(); rg.setStart(nd, i); rg.setEnd(nd, i + word.length);
        const r = rg.getBoundingClientRect();
        if (r.width < 5 || r.x < pr.x || r.y < pr.y || r.right > pr.x + pr.w || r.bottom > pr.y + pr.h) continue;
        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        if (!hit || !(hit.contains(nd.parentElement) || nd.parentElement.contains(hit))) continue;
        res.push({ x: r.x + r.width / 2, y: r.y + r.height / 2, text: nd.textContent, at: i });
      }
    }
    res.sort((a, b) => a.y - b.y || a.x - b.x);
    return res.filter((p, k) => !res.slice(0, k).some(q => Math.abs(q.y - p.y) < 8 && Math.abs(q.x - p.x) < 8));
  }, { pr, word });
}

const selection = (tab) => tab.evaluate(() => String(window.getSelection()));

// Retypes one word in place, keeping its text style: click selects the text element, a double-click selects
// the word (plus any trailing space, which Shift+Left trims back off), and typing starts only once the browser
// selection reads exactly that word (typing with nothing selected would insert a stray "Your paragraph text" box).
// The word must be a single word. Returns the line the edited text should read.
async function replaceWord(tab, word, nth, replacement) {
  await deselect(tab);
  const close = tab.getByRole('button', { name: 'Close', exact: true });
  if (await close.count()) await close.first().click().catch(() => {});
  let hits = await findWord(tab, word);
  if (!hits[nth]) throw new Error(`"${word}" occurrence ${nth} not on the page (found ${hits.length})`);
  const { text, at } = hits[nth];
  const expected = (text.slice(0, at) + replacement + text.slice(at + word.length)).trim();
  await tab.mouse.click(hits[nth].x, hits[nth].y);
  await until(async () => (await positionButton(tab).count()) > 0, { what: 'the text element to be selected' });
  let sel = '';
  for (let k = 0; k < 2 && sel !== word; k++) {
    hits = await findWord(tab, word);
    if (!hits[nth]) { await tab.keyboard.press('Escape'); throw new Error(`"${word}" occurrence ${nth} vanished after selecting its text`); }
    await tab.mouse.dblclick(hits[nth].x, hits[nth].y);
    sel = (await maybe(async () => { const s = await selection(tab); return s.trim() === word && s; }, { timeout: 3000 })) || await selection(tab);
    for (let t = sel.length - sel.trimEnd().length; t > 0 && sel.trim() === word; t--) await tab.keyboard.press('Shift+ArrowLeft');
    if (sel.trim() === word) sel = await until(async () => { const s = await selection(tab); return s === word && s; }, { timeout: 3000, what: 'the selection to be exactly the word' }).catch(() => sel);
  }
  if (sel !== word) { await tab.keyboard.press('Escape'); throw new Error(`selection is "${sel}", not "${word}"`); }
  await tab.keyboard.type(replacement);
  await until(() => tab.evaluate((e) => document.body.innerText.includes(e), expected), { what: 'the edited line' });
  await tab.keyboard.press('Escape');
  await deselect(tab);
  return expected;
}

function progress(file) {
  const lines = () => (fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split('\n') : []);
  return {
    has: (entry) => lines().includes(entry),
    add: (entry) => fs.appendFileSync(file, entry + '\n'),
  };
}

module.exports = {
  openTab, closeTab, argValue, cdpPort, until, waitStable, near, sameBox, pngDims, connectBrowser, nudge,
  designId, withDesign, ready, pageCount, gotoPage, pageRect, deselect, showTab, openPositionPanel, readArrange,
  isImageSelected, layers, selectLayer, setRatioLock, setArrange, placeSelected, uploadFile, addImage, deleteLayer,
  moveSelected, waitSaved, reloadToPage, presentShot, textRuns, findWord, replaceWord, progress,
};
