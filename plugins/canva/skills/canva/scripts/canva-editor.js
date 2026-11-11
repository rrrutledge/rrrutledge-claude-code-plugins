// Shared Playwright helpers for editing a Canva design in the browser.
// Every selector and wait here is one that held up across the ISC Summit deck rounds
// (40-page "Up next" speaker deck, Nov 2026); references/browser-editing.md explains why each exists.
//
// Units: the Arrange panel's px inputs are the design's own units (a 1920x1080 slide reads 1920x1080),
// so a placement plan written in design px needs no conversion.

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

const cdpPort = () => argValue('cdp-port', '9230');
const wait = (tab, ms) => tab.waitForTimeout(ms);
const near = (a, b, t) => Math.abs(a - b) <= t;
const sameBox = (p, q, t = 1.5) => !!(p && q && near(p.w, q.w, t) && near(p.h, q.h, t) && near(p.x, q.x, t) && near(p.y, q.y, t));
const pngDims = (f) => { const b = fs.readFileSync(f); return [b.readUInt32BE(16), b.readUInt32BE(20)]; };

const CONNECT_TIMEOUT_MS = 30000;

async function connectBrowser(port = cdpPort()) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(
      `connectOverCDP did not complete within ${CONNECT_TIMEOUT_MS}ms on port ${port}. ` +
      `An idle Canva renderer freezes and hangs the connect: run canva-nudge.js --cdp-port=${port}, then retry.`)), CONNECT_TIMEOUT_MS);
  });
  try { return await Promise.race([chromium.connectOverCDP(`http://127.0.0.1:${port}`), timeout]); }
  finally { clearTimeout(timer); }
}

const getJson = (u) => new Promise((res, rej) => http.get(u, r => { let s = ''; r.on('data', d => s += d); r.on('end', () => { try { res(JSON.parse(s)); } catch (e) { rej(e); } }); }).on('error', rej));

// Wakes a frozen renderer without Playwright: shrink each page's window 20 px and restore it over
// the browser-level CDP socket. Restoring to the read width can leave the window ~1 px wider per run.
async function nudge(port = cdpPort()) {
  const { ws: WebSocket } = require(path.join(NM, 'playwright-core', 'lib', 'utilsBundle'));
  const v = await getJson(`http://127.0.0.1:${port}/json/version`);
  const pages = (await getJson(`http://127.0.0.1:${port}/json/list`)).filter(t => t.type === 'page');
  const sock = new WebSocket(v.webSocketDebuggerUrl);
  let n = 0; const pend = {};
  const send = (method, params = {}) => new Promise(r => { pend[++n] = r; sock.send(JSON.stringify({ id: n, method, params })); });
  sock.on('message', d => { const m = JSON.parse(d.toString()); if (m.id && pend[m.id]) pend[m.id](m); });
  await new Promise((r, j) => { sock.on('open', r); sock.on('error', j); });
  const seen = new Set();
  for (const p of pages) {
    const w = await send('Browser.getWindowForTarget', { targetId: p.id });
    if (!w.result || seen.has(w.result.windowId)) continue;
    seen.add(w.result.windowId);
    const b = w.result.bounds;
    if (b.windowState !== 'normal') await send('Browser.setWindowBounds', { windowId: w.result.windowId, bounds: { windowState: 'normal' } });
    await send('Browser.setWindowBounds', { windowId: w.result.windowId, bounds: { width: b.width - 20 } });
    await new Promise(r => setTimeout(r, 800));
    await send('Browser.setWindowBounds', { windowId: w.result.windowId, bounds: { width: b.width } });
  }
  for (const p of pages.filter(p => /canva\.com\/design/.test(p.url))) await send('Target.activateTarget', { targetId: p.id });
  sock.close();
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

async function ready(tab) {
  await tab.bringToFront().catch(() => {});
  await tab.locator('[data-role="timeline-scene"]').first().waitFor({ state: 'attached', timeout: 120000 });
  await wait(tab, 1500);
}

// Connects, finds or opens this script's tagged tab on the design, keeps the renderer awake, runs fn.
// The tab closes afterward unless keep is set (closeTab never closes the last tab).
async function withDesign(url, tag, fn, { keep = false, viewport = { width: 1528, height: 1400 } } = {}) {
  const browser = await connectBrowser();
  const context = browser.contexts().find(c => c.pages().some(p => /^https?:/.test(p.url()))) || browser.contexts()[0];
  const mine = taggedUrl(url, tag);
  let tab = context.pages().find(p => p.url().includes(designId(url)) && p.url().includes(`${tag}=1`));
  try {
    if (!tab) { tab = await openTab(context); await tab.goto(mine, { waitUntil: 'domcontentloaded', timeout: 90000 }); }
    await tab.setViewportSize(viewport);
    const cdp = await context.newCDPSession(tab);
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
    await cdp.send('Page.setWebLifecycleState', { state: 'active' }).catch(() => {});
    await ready(tab);
    return await fn(tab, context);
  } finally {
    if (tab && !keep) await closeTab(tab).catch(() => {});
    await browser.close().catch(() => {});
  }
}

const pageLoc = (tab, n) => tab.locator(`[data-role="timeline-scene"][aria-label="Page ${n}"]`);

async function pageCount(tab) {
  const nums = await tab.locator('[data-role="timeline-scene"]').evaluateAll(els => els.map(e => +(e.getAttribute('aria-label') || '').slice(5)).filter(Boolean));
  return nums.length ? Math.max(...nums) : 0;
}

// The page strip renders a window of thumbnails; step toward the target until its thumb exists.
async function gotoPage(tab, n) {
  for (let k = 0; k < 60 && !(await pageLoc(tab, n).count()); k++) {
    const nums = await tab.locator('[data-role="timeline-scene"]').evaluateAll(els => els.map(e => +(e.getAttribute('aria-label') || '').slice(5)).filter(Boolean));
    const pick = n > Math.max(...nums) ? Math.max(...nums) : Math.min(...nums);
    await pageLoc(tab, pick).scrollIntoViewIfNeeded();
    await pageLoc(tab, pick).click();
    await wait(tab, 400);
  }
  await pageLoc(tab, n).scrollIntoViewIfNeeded();
  await pageLoc(tab, n).click();
  await tab.waitForFunction((n) => document.querySelector(`[aria-roledescription="Presentation"][aria-label="Page ${n}"]`), n, { timeout: 15000 }).catch(() => {});
  await wait(tab, 1200);
}

// The editing canvas: the most common on-screen 16:9 div wider than 600 px. s converts screen px to design px.
async function pageRect(tab) {
  return tab.evaluate(() => {
    const c = [...document.querySelectorAll('div')].map(e => e.getBoundingClientRect()).filter(r => r.width > 600 && Math.abs(r.width / r.height - 16 / 9) < 0.02 && r.x > 70 && r.y > 60);
    const key = (r) => [Math.round(r.x), Math.round(r.y), Math.round(r.width)].join(',');
    const cnt = {}; c.forEach(r => { cnt[key(r)] = (cnt[key(r)] || 0) + 1; });
    c.sort((a, b) => cnt[key(b)] - cnt[key(a)]);
    const r = c[0];
    return r ? { x: r.x, y: r.y, w: r.width, h: r.height, s: r.width / 1920 } : null;
  });
}

// (1490, 165) is empty grey canvas at withDesign's 1528x1400 viewport; a click there clears any selection.
async function deselect(tab) {
  await tab.keyboard.press('Escape');
  await tab.keyboard.press('Escape');
  await tab.mouse.click(1490, 165);
  await wait(tab, 400);
}

async function showTab(tab, name) {
  const loc = tab.getByText(name, { exact: true }).first();
  try { await loc.click({ timeout: 8000 }); } catch { await loc.click({ force: true, timeout: 8000 }); }
  await wait(tab, 350);
}

// The Position panel (Arrange + Layers tabs) only opens with something selected: select the top-left corner
// of the page, falling back to a point on the right, then press Position.
async function openPositionPanel(tab) {
  if (await tab.getByText('Arrange', { exact: true }).first().isVisible().catch(() => false)) return;
  let pos = tab.getByRole('button', { name: 'Position', exact: true });
  for (const [fx, fy] of [[0.12, 0.06], [0.83, 0.31]]) {
    if (await pos.count()) break;
    await deselect(tab);
    const p = await pageRect(tab);
    await tab.mouse.click(p.x + p.w * fx, p.y + p.h * fy);
    await wait(tab, 600);
  }
  await pos.first().click();
  await tab.getByText('Arrange', { exact: true }).first().waitFor({ timeout: 10000 });
}

// Layer rows are the 280-360 px wide, 40-70 px tall buttons whose left edge sits at x 80-100 in the side panel.
// Each read tags them data-cvrow=<index> so a click targets exactly the row that was read.
async function rowButtons(tab) {
  const read = () => tab.evaluate(() => {
    const bs = [...document.querySelectorAll('button')].filter(b => {
      const r = b.getBoundingClientRect();
      return r.x > 80 && r.x < 100 && r.width > 280 && r.width < 360 && r.height > 40 && r.height < 70 && r.y > 200 && r.bottom < window.innerHeight - 30;
    });
    document.querySelectorAll('[data-cvrow]').forEach(e => e.removeAttribute('data-cvrow'));
    bs.forEach((b, i) => b.setAttribute('data-cvrow', String(i)));
    return bs.map(b => ({ label: (b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 70) }));
  });
  for (let k = 0; k < 25; k++) { const r = await read(); if (r.some(x => x.label === 'Background')) return r; await wait(tab, 250); }
  return read();
}

async function scrollLayersTop(tab) {
  await tab.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find(b => { const r = b.getBoundingClientRect(); return r.x > 80 && r.x < 100 && r.width > 280 && r.width < 360 && r.height > 40 && r.height < 70; });
    let p = b && b.parentElement; while (p && p.scrollHeight <= p.clientHeight + 2) p = p.parentElement;
    if (p) p.scrollTop = 0;
  });
  await wait(tab, 400);
}

const pxInputs = (tab) => tab.locator('input').evaluateAll(els => els.map((e, i) => [i, e.value]).filter(([, v]) => /px$|°$/.test(v)));

async function readArrange(tab) {
  await showTab(tab, 'Arrange');
  await tab.getByText('Advanced', { exact: true }).first().waitFor({ timeout: 8000 });
  await wait(tab, 200);
  const v = (await pxInputs(tab)).map(([, s]) => parseFloat(s));
  return { w: v[0], h: v[1], x: v[2], y: v[3] };
}

// An image (not a frame or text) is selected when the toolbar offers the Eraser.
const isImageSelected = async (tab) => (await tab.getByRole('button', { name: 'Eraser', exact: true }).count()) > 0;

// Every layer on the current page, front to back, with its box in design px.
// Rows are clicked one at a time because the Arrange values only show for the selected layer.
async function layers(tab) {
  await openPositionPanel(tab);
  await showTab(tab, 'Layers');
  await scrollLayersTop(tab);
  const rows = await rowButtons(tab);
  if (!rows.some(r => r.label === 'Background')) throw new Error('Layers list did not show the Background row: ' + rows.map(r => r.label).join('|'));
  const out = [];
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].label === 'Background') { out.push({ i, label: 'Background', img: false, box: null }); continue; }
    await showTab(tab, 'Layers');
    const now = await rowButtons(tab);
    if (!now[i] || now[i].label !== rows[i].label) throw new Error('Layers list changed while reading it');
    await tab.locator(`[data-cvrow="${i}"]`).click();
    await wait(tab, 700);
    out.push({ i, label: rows[i].label, img: await isImageSelected(tab), box: await readArrange(tab).catch(() => null) });
  }
  return out;
}

async function selectLayer(tab, row) {
  await openPositionPanel(tab);
  await showTab(tab, 'Layers');
  const rws = await rowButtons(tab);
  if (!rws[row.i] || rws[row.i].label !== row.label) throw new Error(`layer ${row.i} is no longer "${row.label}"`);
  await tab.locator(`[data-cvrow="${row.i}"]`).click();
  await wait(tab, 700);
  const box = await readArrange(tab);
  if (row.box && !sameBox(box, row.box, 0.5)) throw new Error(`layer ${row.i} moved since it was read`);
  return box;
}

async function setRatioLock(tab, want) {
  await showTab(tab, 'Arrange');
  await tab.getByText('Advanced', { exact: true }).first().waitFor({ timeout: 8000 });
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
    await wait(tab, 400);
    if ((await tab.locator('[data-cvratio="1"]').getAttribute('aria-pressed')) !== String(want)) throw new Error('Ratio lock did not switch');
  }
}

// Types into the selected layer's W/H/X/Y inputs. With the ratio locked, set h alone first, then x and y.
async function setArrange(tab, vals) {
  await showTab(tab, 'Arrange');
  await tab.getByText('Advanced', { exact: true }).first().waitFor({ timeout: 8000 });
  const order = ['w', 'h', 'x', 'y'];
  for (const k of order) {
    if (vals[k] == null) continue;
    const idx = (await pxInputs(tab)).map(([i]) => i);
    const el = tab.locator('input').nth(idx[order.indexOf(k)]);
    await el.click();
    await tab.keyboard.press('Control+A');
    await tab.keyboard.type(String(Math.round(vals[k])));
    await tab.keyboard.press('Enter');
    await wait(tab, 500);
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
// a new first thumb, served from Canva (not a blob: preview), with the file's aspect ratio, stable for 2 s.
async function uploadFile(tab, file) {
  const [fw, fh] = pngDims(file);
  const ar = fw / fh;
  await tab.locator('input[type=file]').first().waitFor({ state: 'attached', timeout: 15000 });
  let prev = '';
  for (let k = 0; k < 20; k++) { await wait(tab, 700); const cur = (await uploadThumbs(tab)).map(t => t.src + t.nw).join('|'); if (cur && cur === prev) break; prev = cur; }
  const t0 = await uploadThumbs(tab);
  const before = t0.length ? t0[0].src : '';
  await tab.locator('input[type=file]').first().setInputFiles(file.replace(/\\/g, '/'));
  for (let k = 0; k < 120; k++) {
    await wait(tab, 1000);
    const f = (await uploadThumbs(tab))[0];
    if (f && f.src !== before && f.nw > 0 && !/^blob:/.test(f.src) && near(f.nw / f.nh, ar, ar * 0.015)) {
      await wait(tab, 2000);
      const again = (await uploadThumbs(tab))[0];
      if (again && again.src === f.src) return again;
    }
  }
  throw new Error('uploaded thumbnail never appeared for ' + path.basename(file));
}

// Swaps the selected image's picture, keeping its layer slot. Canva keeps the old box, so only use this when the
// new file has the old image's aspect ratio, then size it with placeSelected.
async function replaceSelectedImage(tab, file) {
  await tab.getByRole('button', { name: 'Replace', exact: true }).first().click();
  await wait(tab, 1200);
  await tab.getByText('Uploads', { exact: true }).first().click();
  const pick = await uploadFile(tab, file);
  await tab.mouse.click(pick.x, pick.y);
  await wait(tab, 3000);
  const close = tab.getByRole('button', { name: 'Close', exact: true });
  if (await close.count()) await close.first().click().catch(() => {});
  await wait(tab, 800);
}

// Adds the file to the current page as a new, selected, front-most image and returns its box.
async function addImage(tab, file) {
  const [fw, fh] = pngDims(file);
  await deselect(tab);
  if (!(await tab.getByText('Upload files', { exact: true }).first().isVisible().catch(() => false))) await tab.getByText('Uploads', { exact: true }).first().click();
  const pick = await uploadFile(tab, file);
  await tab.mouse.click(pick.x, pick.y);
  await wait(tab, 2500);
  if (!(await isImageSelected(tab))) throw new Error('clicking the upload did not add a selected image');
  await openPositionPanel(tab);
  const p = await readArrange(tab);
  if (!near(p.w / p.h, fw / fh, 0.02 * fw / fh)) throw new Error(`placed image aspect ${p.w / p.h} differs from the file's ${fw / fh}`);
  return p;
}

// A Frame element ignores the Delete key; delete it through its layer row's context menu. Anything else takes Delete.
async function deleteLayer(tab, row) {
  await selectLayer(tab, row);
  if (row.label !== 'Frame element') { await tab.keyboard.press('Delete'); await wait(tab, 1200); return; }
  await showTab(tab, 'Layers');
  await rowButtons(tab);
  const rb = await tab.locator(`[data-cvrow="${row.i}"]`).boundingBox();
  await tab.mouse.move(rb.x + rb.width / 2, rb.y + rb.height / 2);
  await wait(tab, 400);
  const ok = await tab.evaluate((y) => {
    document.querySelectorAll('[data-cvmenu]').forEach(e => e.removeAttribute('data-cvmenu'));
    const b = [...document.querySelectorAll('button[aria-label="Open context menu for layer element"]')].find(b => { const q = b.getBoundingClientRect(); return q.y >= y - 2 && q.y <= y + 60; });
    if (b) b.setAttribute('data-cvmenu', '1');
    return !!b;
  }, rb.y);
  if (!ok) throw new Error('no layer context menu button');
  await tab.locator('[data-cvmenu="1"]').click({ force: true });
  await wait(tab, 800);
  const del = tab.getByRole('menuitem', { name: /^Delete/ });
  if (!(await del.count())) { await tab.keyboard.press('Escape'); throw new Error('no Delete menu item'); }
  await del.first().click();
  await wait(tab, 1500);
}

async function moveSelected(tab, direction, times = 1) {
  await showTab(tab, 'Arrange');
  for (let k = 0; k < times; k++) { await tab.getByText(direction, { exact: true }).first().click(); await wait(tab, 1000); }
}

// Waits out "Saving..." so a reload never drops the last edit.
async function waitSaved(tab) {
  await deselect(tab);
  await wait(tab, 3000);
  await tab.waitForFunction(() => !/Saving/i.test(document.body.innerText), null, { timeout: 60000 }).catch(() => {});
  await wait(tab, 1500);
}

// Saves, reloads, and returns to page n: the only trustworthy check that an edit stuck.
async function reloadToPage(tab, n) {
  await waitSaved(tab);
  for (let k = 0; k < 2; k++) {
    await tab.reload({ waitUntil: 'domcontentloaded', timeout: 90000 });
    await ready(tab);
    await wait(tab, 3000);
    try { await gotoPage(tab, n); return; } catch (e) { if (k) throw e; }
  }
}

// Full-resolution screenshot of the current page through Present full screen at a 1920x1080 viewport,
// with the pointer parked in a corner so the presenter controls fade.
async function presentShot(tab, file) {
  const vp = tab.viewportSize();
  await deselect(tab);
  const closeBtn = tab.getByRole('button', { name: 'Close', exact: true });
  if (await closeBtn.count()) await closeBtn.first().click().catch(() => {});
  await wait(tab, 600);
  await deselect(tab);
  await tab.setViewportSize({ width: 1920, height: 1080 });
  await wait(tab, 800);
  await tab.locator('button[aria-label="Present full screen"]').first().click();
  await wait(tab, 1000);
  await tab.mouse.move(1919, 2);
  await wait(tab, 5000);
  await tab.screenshot({ path: file });
  await tab.keyboard.press('Escape');
  await wait(tab, 1500);
  if (vp) await tab.setViewportSize(vp);
  await wait(tab, 800);
}

// Visible text runs on the current page inside box (design px), from the DOM rather than OCR.
async function textRuns(tab, box = { x: 0, y: 0, w: 1920, h: 1080 }) {
  const pr = await pageRect(tab);
  return tab.evaluate(({ pr, box }) => {
    const res = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let nd;
    while ((nd = walker.nextNode())) {
      const t = nd.textContent.trim();
      if (!t) continue;
      const rg = document.createRange(); rg.selectNodeContents(nd);
      for (const r of rg.getClientRects()) {
        if (r.width < 2 || r.height < 8) continue;
        if (r.x < pr.x || r.y < pr.y || r.right > pr.x + pr.w || r.bottom > pr.y + pr.h) continue;
        const x = (r.x - pr.x) / pr.s, y = (r.y - pr.y) / pr.s, w = r.width / pr.s, h = r.height / pr.s;
        if (x < box.x - 20 || x > box.x + box.w + 20 || y < box.y - 20 || y > box.y + box.h + 20) continue;
        res.push({ t: t.slice(0, 80), x, y, w, h });
      }
    }
    return res;
  }, { pr, box });
}

// Centres (screen px) of each on-page occurrence of word, top to bottom, one per line.
async function findWord(tab, word) {
  const pr = await pageRect(tab);
  return tab.evaluate(({ pr, word }) => {
    const res = [];
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let nd;
    while ((nd = w.nextNode())) {
      for (let i = nd.textContent.indexOf(word); i >= 0; i = nd.textContent.indexOf(word, i + 1)) {
        const rg = document.createRange(); rg.setStart(nd, i); rg.setEnd(nd, i + word.length);
        const r = rg.getBoundingClientRect();
        if (r.width < 5 || r.x < pr.x || r.y < pr.y || r.right > pr.x + pr.w || r.bottom > pr.y + pr.h) continue;
        res.push({ x: r.x + r.width / 2, y: r.y + r.height / 2 });
      }
    }
    res.sort((a, b) => a.y - b.y || a.x - b.x);
    return res.filter((p, k) => !res.slice(0, k).some(q => Math.abs(q.y - p.y) < 8 && Math.abs(q.x - p.x) < 8));
  }, { pr, word });
}

// Retypes one word in place, keeping its text style: click selects the text element, a double-click selects
// the word, and typing starts only once the browser selection reads exactly that word (typing with nothing
// selected would insert a stray "Your paragraph text" box). The word must be a single word.
async function replaceWord(tab, word, nth, replacement) {
  await deselect(tab);
  const close = tab.getByRole('button', { name: 'Close', exact: true });
  if (await close.count()) await close.first().click().catch(() => {});
  let hits = await findWord(tab, word);
  if (!hits[nth]) throw new Error(`"${word}" occurrence ${nth} not on the page (found ${hits.length})`);
  const count = hits.length;
  await tab.mouse.click(hits[nth].x, hits[nth].y);
  await wait(tab, 700);
  let sel = '';
  for (let k = 0; k < 2 && sel.trim() !== word; k++) {
    hits = await findWord(tab, word);
    await tab.mouse.dblclick(hits[nth].x, hits[nth].y);
    await wait(tab, 700);
    sel = await tab.evaluate(() => String(window.getSelection()));
  }
  if (sel.trim() !== word) { await tab.keyboard.press('Escape'); throw new Error(`selection is "${sel}", not "${word}"`); }
  if ((await findWord(tab, word)).length !== count) throw new Error('text changed before typing');
  await tab.keyboard.type(replacement);
  await wait(tab, 800);
  await tab.keyboard.press('Escape');
  await wait(tab, 800);
  await deselect(tab);
  return count;
}

function progress(file) {
  const lines = () => (fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split('\n') : []);
  return {
    has: (entry) => lines().includes(entry),
    add: (entry) => fs.appendFileSync(file, entry + '\n'),
  };
}

module.exports = {
  openTab, closeTab, argValue, cdpPort, wait, near, sameBox, pngDims, connectBrowser, nudge, designId, withDesign, ready,
  pageCount, gotoPage, pageRect, deselect, showTab, openPositionPanel, readArrange, isImageSelected, layers,
  selectLayer, setRatioLock, setArrange, placeSelected, uploadFile, replaceSelectedImage, addImage, deleteLayer,
  moveSelected, waitSaved, reloadToPage, presentShot, textRuns, findWord, replaceWord, progress,
};
