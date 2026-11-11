#!/usr/bin/env node
// Browser-side Canva editing commands, built on canva-editor.js. Run through browser-chauffeur, which passes --cdp-port.
// Every command prints "Verification passed" or "Verification FAILED - N errors" last.
//
//   --nudge                                   wake a frozen Canva renderer (run before every batch)
//   --layers  --design=<url> --page=N         print the page's layers, front to back, as JSON
//   --shots   --design=<url> --pages=1,2|all --out=<dir>
//                                             full-resolution Present-mode PNG per page (pNN.png)
//   --measure --design=<url> --pages=1,2|all --out=<dir>
//                                             per page: pNN.json (layers with boxes, visible text runs, design px) + pNN.png
//   --replace-word --design=<url> --page=N --word=<word> --with=<text> [--nth=K] [--out=<dir>]
//                                             retype the Kth (0-based, top to bottom) occurrence of one word, keeping its style
//   --apply   --design=<url> --plan=<json> --pages=1,2|all --out=<dir> [--force]
//                                             place images and move layers per a plan (format below)
//   --expand  --file=<png> --name=<slug> --out=<dir> [--top=px] [--bottom=px] [--left=px] [--right=px]
//                                             Magic Expand in a fresh scratch design; stops at the results
//   --expand  --name=<slug> --out=<dir> --pick=K   pick result K (0-based), download it to <out>/<slug>.png
//   --expand  --name=<slug> --out=<dir> --more     ask for another set of results
//   --expand  --name=<slug> --out=<dir> --close    close the scratch tab (the design itself stays; see browser-editing.md)
//
// Plan format for --apply, all boxes in design px:
//   { "pages": { "5": [
//       { "match": {"x":120,"y":300,"w":540,"h":780}, "file": "C:/.../cutout.png", "to": {"x":96,"y":250,"h":830} },
//       { "match": {"x":800,"y":400,"w":700,"h":260}, "to": {"x":780,"y":410} } ] } }
// "match" is the layer's current box (within 1.5 px). With "file", that file is added as a new image sized by "to" with
// the ratio locked, the old layer is deleted, and the new image takes the old layer's front-to-back slot. Without "file", the layer is moved (and sized,
// if "to" has h or w). Each item checks the page first and skips itself when a layer already sits at "to", so a rerun
// resumes where the page really is. Pages that pass their check are logged in <out>/progress.txt and skipped unless --force.

const fs = require('fs');
const path = require('path');
const E = require('./canva-editor');

const arg = (k, d) => E.argValue(k, d);
const has = (k) => process.argv.includes(`--${k}`);
const errors = [];
const log = (out) => (...a) => { const s = a.join(' '); console.log(s); if (out) fs.appendFileSync(path.join(out, 'run.log'), new Date().toISOString() + ' ' + s + '\n'); };

async function pageList(spec, tab) {
  if (spec === 'all') return Array.from({ length: await E.pageCount(tab) }, (_, i) => i + 1);
  return spec.split(',').map(Number).filter(Boolean);
}

function finish() {
  if (errors.length) { console.error(`Verification FAILED - ${errors.length} errors`); errors.forEach(e => console.error('  - ' + e)); process.exit(1); }
  console.log('Verification passed');
  process.exit(0);
}

async function cmdLayers() {
  await E.withDesign(arg('design'), 'cvlayers', async (tab) => {
    await E.gotoPage(tab, +arg('page'));
    console.log(JSON.stringify(await E.layers(tab), null, 1));
  });
}

async function cmdShots() {
  const out = arg('out');
  fs.mkdirSync(out, { recursive: true });
  await E.withDesign(arg('design'), 'cvshots', async (tab) => {
    for (const n of await pageList(arg('pages'), tab)) {
      await E.gotoPage(tab, n);
      const f = path.join(out, `p${String(n).padStart(2, '0')}.png`);
      await E.presentShot(tab, f);
      console.log('shot', f);
    }
  });
}

async function cmdMeasure() {
  const out = arg('out');
  fs.mkdirSync(out, { recursive: true });
  await E.withDesign(arg('design'), 'cvmeasure', async (tab) => {
    for (const n of await pageList(arg('pages'), tab)) {
      await E.gotoPage(tab, n);
      const stem = path.join(out, `p${String(n).padStart(2, '0')}`);
      const rec = { page: n, layers: await E.layers(tab), texts: await E.textRuns(tab) };
      fs.writeFileSync(stem + '.json', JSON.stringify(rec, null, 1));
      await E.presentShot(tab, stem + '.png');
      console.log('measured', stem + '.json', rec.layers.length, 'layers', rec.texts.length, 'text runs');
    }
  });
}

async function cmdReplaceWord() {
  const n = +arg('page');
  const word = arg('word');
  const repl = arg('with');
  const nth = +arg('nth', 0);
  await E.withDesign(arg('design'), 'cvword', async (tab) => {
    await E.reloadToPage(tab, n);
    const expected = await E.replaceWord(tab, word, nth, repl);
    await E.reloadToPage(tab, n);
    const ok = await tab.evaluate((e) => document.body.innerText.includes(e), expected);
    console.log('page text after reload:', (await E.textRuns(tab)).map(t => t.t).join(' / '));
    if (!ok) errors.push(`after reload the page does not read "${expected}"`);
    if (arg('out')) { fs.mkdirSync(arg('out'), { recursive: true }); await E.presentShot(tab, path.join(arg('out'), `p${String(n).padStart(2, '0')}-text.png`)); }
  });
}

const atTo = (rows, to) => rows.filter(r => r.box && E.near(r.box.x, to.x, 2.5) && E.near(r.box.y, to.y, 2.5) && (to.h == null || E.near(r.box.h, to.h, 2.5)) && (to.w == null || to.h != null || E.near(r.box.w, to.w, 2.5)));

// Each item reads the page first, so a rerun after a crash or a failed check picks up where the page really is:
// a layer at "to" and none at "match" means done; both present means a swap stopped before the old layer was deleted.
async function applyItem(tab, n, item, L) {
  const rows = await E.layers(tab);
  const hits = rows.filter(r => E.sameBox(r.box, item.match));
  const done = atTo(rows, item.to);
  if (done.length === 1 && !hits.length) { L(`p${n} item already at ${JSON.stringify(item.to)}`); return; }
  if (hits.length !== 1) throw new Error(`p${n}: ${hits.length} layers match ${JSON.stringify(item.match)} and ${done.length} sit at ${JSON.stringify(item.to)}`);
  const old = hits[0];
  if (item.file && done.length === 1 && !E.sameBox(done[0].box, old.box, 3)) {
    await finishSwap(tab, n, old.i, old.box, done[0].box, old.i - (done[0].i < old.i ? 1 : 0));
    L(`p${n} finished an interrupted swap for ${path.basename(item.file)}`);
    return;
  }
  if (!item.file) {
    await E.selectLayer(tab, old);
    const got = item.to.h != null || item.to.w != null ? await E.placeSelected(tab, item.to) : await E.setArrange(tab, { x: item.to.x, y: item.to.y });
    L(`p${n} moved ${old.label}`, JSON.stringify(old.box), '->', JSON.stringify(got));
    return;
  }
  if (!fs.existsSync(item.file)) throw new Error('missing file ' + item.file);
  await E.addImage(tab, item.file);
  const placed = await E.placeSelected(tab, item.to);
  // addImage puts the new image in front, at row 0, which shifts the old layer down one row.
  await finishSwap(tab, n, old.i + 1, old.box, placed, old.i);
  L(`p${n} swapped ${old.label} for ${path.basename(item.file)}`, JSON.stringify(placed));
}

// Deletes the old layer (row oldIndex, expected at oldBox) and steps the new image (at box placed) to layer index
// slot, the old layer's place in the stack. The old layer is found by index, not box, so a recut placed in the old
// layer's exact box still works; deleteLayer confirms the Layers list lost a row.
async function finishSwap(tab, n, oldIndex, oldBox, placed, slot) {
  const target = (await E.layers(tab))[oldIndex];
  if (!target || !E.sameBox(target.box, oldBox, 1)) throw new Error(`p${n}: layer ${oldIndex} is not the old layer at ${JSON.stringify(oldBox)}`);
  await E.deleteLayer(tab, target);
  for (let t = 0; t < 8; t++) {
    const mine = (await E.layers(tab)).filter(r => E.sameBox(r.box, placed, 1.5));
    if (mine.length !== 1) throw new Error(`p${n}: ${mine.length} layers at the new image's box after deleting the old one`);
    const me = mine[0];
    if (me.i === slot) return;
    await E.selectLayer(tab, me);
    if (!(await E.moveSelected(tab, me.i < slot ? 'Backward' : 'Forward'))) return;
  }
  throw new Error(`p${n}: could not move the new image to the old slot ${slot}`);
}

async function verifyPage(tab, items) {
  const rows = await E.layers(tab);
  const bad = [];
  items.forEach((item, k) => {
    const hits = atTo(rows, item.to);
    if (hits.length !== 1) bad.push(`item ${k}: ${hits.length} layers at ${JSON.stringify(item.to)}`);
  });
  return bad;
}

async function cmdApply() {
  const out = arg('out');
  fs.mkdirSync(out, { recursive: true });
  const L = log(out);
  const plan = JSON.parse(fs.readFileSync(arg('plan'), 'utf8')).pages;
  const prog = E.progress(path.join(out, 'progress.txt'));
  await E.withDesign(arg('design'), 'cvapply', async (tab) => {
    const pages = arg('pages') === 'all' ? Object.keys(plan).map(Number).sort((a, b) => a - b) : await pageList(arg('pages'), tab);
    for (const n of pages) {
      const items = plan[String(n)];
      if (!items) { errors.push(`p${n}: not in the plan`); continue; }
      // A page is done for this design and these exact items; a changed plan or another design redoes it.
      const key = `done ${E.designId(arg('design'))} ${n} ${require('crypto').createHash('md5').update(JSON.stringify(items)).digest('hex').slice(0, 10)}`;
      if (!has('force') && prog.has(key)) { L(`p${n} already done`); continue; }
      try {
        await E.reloadToPage(tab, n);
        for (const item of items) await applyItem(tab, n, item, L);
        await E.reloadToPage(tab, n);
        const bad = await verifyPage(tab, items);
        await E.presentShot(tab, path.join(out, `p${String(n).padStart(2, '0')}.png`));
        if (bad.length) { errors.push(`p${n} after reload: ${bad.join('; ')}`); L(`p${n} CHECK FAILED`, bad.join('; ')); continue; }
        prog.add(key);
        L(`p${n} done, verified after reload`);
      } catch (e) {
        errors.push(e.message);
        L(`p${n} FAILED: ${e.message}`);
        await tab.screenshot({ path: path.join(out, `diag-p${n}.png`) }).catch(() => {});
      }
    }
  });
}

// The Magic Expand result tiles in the side panel, top to bottom then left to right, with their image sources.
const resultTiles = (tab) => tab.evaluate(() => {
  const ims = [...document.querySelectorAll('img')].filter(i => { const r = i.getBoundingClientRect(); return r.x > 80 && r.x < 520 && r.y > 150 && r.y < 900 && r.width > 90 && r.width < 220 && r.height > 90; })
    .map(i => { const r = i.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), src: i.src.slice(0, 160), done: i.complete && i.naturalWidth > 0 }; });
  const seen = new Set();
  return ims.filter(p => { const key = Math.round(p.x / 40) + ',' + Math.round(p.y / 40); if (seen.has(key)) return false; seen.add(key); return true; }).sort((a, b) => a.y - b.y || a.x - b.x);
});

// Four result tiles, all loaded and holding steady.
async function resultsReady(tab, before = '') {
  await E.until(async () => { const t = await resultTiles(tab); return t.length >= 4 && t.every(x => x.done) && t.map(x => x.src).join('|') !== before; }, { timeout: 150000, what: 'the Magic Expand results' });
  return E.waitStable(() => resultTiles(tab), { quietMs: 1500 });
}

// Magic Expand runs only in a scratch design: it is a generative edit, and the scratch design keeps it out of the real one.
async function cmdExpand() {
  const out = arg('out');
  const name = arg('name');
  fs.mkdirSync(out, { recursive: true });
  const L = log(out);
  const stateFile = path.join(out, 'expand-state.json');
  const st = fs.existsSync(stateFile) ? JSON.parse(fs.readFileSync(stateFile, 'utf8')) : {};
  const browser = await E.connectBrowser();
  const ctx = browser.contexts().find(c => c.pages().some(p => /^https?:/.test(p.url()))) || browser.contexts()[0];
  const shot = (tab, n) => tab.screenshot({ path: path.join(out, `${name}-${n}.png`) });
  let tab = st[name] ? ctx.pages().find(p => p.url().includes(st[name])) : null;
  try {
    if (arg('file')) {
      if (tab) throw new Error(`a scratch design for ${name} is already open; --pick, --more or --close it first`);
      const file = arg('file');
      const [w, h] = E.pngDims(file);
      const top = +arg('top', 0), bottom = +arg('bottom', 0), left = +arg('left', 0), right = +arg('right', 0);
      tab = await E.openTab(ctx);
      await tab.setViewportSize({ width: 1600, height: 1000 });
      await tab.goto(`https://www.canva.com/design?create=true&width=${w + left + right}&height=${h + top + bottom}&units=px`, { timeout: 90000 });
      await tab.waitForURL(/\/design\/[A-Za-z0-9_-]+\//, { timeout: 90000 });
      st[name] = E.designId(tab.url());
      fs.writeFileSync(stateFile, JSON.stringify(st, null, 1));
      L(name, 'scratch design', tab.url());
      await tab.getByText('Uploads', { exact: true }).first().click({ timeout: 60000 });
      const pick = await E.uploadFile(tab, file);
      await tab.mouse.click(pick.x, pick.y);
      await E.until(() => E.isImageSelected(tab), { what: 'the photo to be added and selected' });
      let pos = tab.getByRole('button', { name: 'Position', exact: true });
      if (!(await pos.count())) {
        await tab.getByRole('button', { name: /More/ }).last().click();
        pos = tab.getByText('Position', { exact: true });
      }
      await pos.first().click({ timeout: 10000 });
      await E.placeSelected(tab, { w, x: left, y: top });
      await shot(tab, 'placed');
      await tab.getByRole('button', { name: 'Edit', exact: true }).first().click({ timeout: 10000 });
      await tab.getByText('See all', { exact: true }).first().click({ timeout: 10000 });
      await tab.getByText('Magic Expand', { exact: true }).first().click({ timeout: 15000 });
      await tab.getByText('Whole Page', { exact: true }).first().click({ timeout: 10000 });
      await tab.getByRole('button', { name: 'Expand', exact: true }).last().click({ timeout: 10000 });
      L(name, 'expanding');
      await tab.getByText('Generate new results', { exact: true }).first().waitFor({ timeout: 150000 });
      await resultsReady(tab);
      await shot(tab, 'results');
      L(name, 'results ready:', path.join(out, `${name}-results.png`));
      return;
    }
    if (!tab) throw new Error(`no open scratch design for ${name}`);
    await tab.bringToFront();
    if (arg('pick') != null) {
      const k = +arg('pick');
      const tiles = await resultTiles(tab);
      if (!tiles[k]) throw new Error(`no result tile ${k} (found ${tiles.length})`);
      await tab.mouse.click(tiles[k].x, tiles[k].y);
      const done = tab.getByRole('button', { name: 'Done', exact: true }).last();
      await done.click({ timeout: 10000 });
      await done.waitFor({ state: 'hidden', timeout: 30000 });
      await shot(tab, 'picked');
      await tab.keyboard.press('Escape');
      await tab.getByText('File', { exact: true }).first().click({ timeout: 10000 });
      await tab.getByText('Download', { exact: true }).first().click({ timeout: 10000 });
      const [dl] = await Promise.all([
        tab.waitForEvent('download', { timeout: 120000 }),
        tab.getByRole('button', { name: 'Download', exact: true }).last().click({ timeout: 10000 }),
      ]);
      const f = path.join(out, name + '.png');
      await dl.saveAs(f);
      L(name, 'downloaded', f);
    } else if (has('more')) {
      const before = (await resultTiles(tab)).map(x => x.src).join('|');
      await tab.getByText('Generate new results', { exact: true }).first().click({ timeout: 10000 });
      await resultsReady(tab, before);
      await shot(tab, 'results2');
      L(name, 'more results:', path.join(out, `${name}-results2.png`));
    } else if (has('close')) {
      L(name, 'closing scratch tab; design', st[name], 'stays in Projects as "Untitled design"');
      await E.closeTab(tab);
      delete st[name];
      fs.writeFileSync(stateFile, JSON.stringify(st, null, 1));
    } else {
      errors.push('--expand needs --file=<png>, --pick=K, --more or --close');
    }
  } catch (e) {
    errors.push(e.message);
    if (tab) await shot(tab, 'error').catch(() => {});
  } finally {
    await browser.close().catch(() => {});
  }
}

const REQUIRED = { layers: ['design', 'page'], shots: ['design', 'pages', 'out'], measure: ['design', 'pages', 'out'], 'replace-word': ['design', 'page', 'word', 'with'], apply: ['design', 'plan', 'pages', 'out'], expand: ['name', 'out'] };

(async () => {
  try {
    const cmd = Object.keys(REQUIRED).find(has);
    const missing = cmd ? REQUIRED[cmd].filter(k => arg(k) == null) : [];
    if (missing.length) errors.push(`--${cmd} needs ${missing.map(k => '--' + k + '=').join(' ')}`);
    else if (has('nudge')) console.log('nudged', await E.nudge(), 'window(s)');
    else if (has('layers')) await cmdLayers();
    else if (has('shots')) await cmdShots();
    else if (has('measure')) await cmdMeasure();
    else if (has('replace-word')) await cmdReplaceWord();
    else if (has('apply')) await cmdApply();
    else if (has('expand')) await cmdExpand();
    else errors.push('no command: pass --nudge, --layers, --shots, --measure, --replace-word, --apply or --expand');
  } catch (e) { errors.push(e.message); }
  finish();
})();
