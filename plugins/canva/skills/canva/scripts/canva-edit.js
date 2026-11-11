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
// "match" is the layer's current box (within 1.5 px). With "file", the layer's picture becomes that file, sized by
// "to" with the ratio locked, keeping the layer's front-to-back slot. Without "file", the layer is moved (and sized,
// if "to" has h or w). Each finished item and page is logged in <out>/progress.txt, so a rerun resumes where it stopped.

const fs = require('fs');
const path = require('path');
const E = require('./canva-editor');

const arg = (k, d) => E.argValue(k, d);
const has = (k) => process.argv.includes(`--${k}`);
const errors = [];
const log = (out) => (...a) => { const s = a.join(' '); console.log(s); if (out) fs.appendFileSync(path.join(out, 'run.log'), new Date().toISOString() + ' ' + s + '\n'); };

function pageList(spec, total) {
  if (spec === 'all') return Array.from({ length: total }, (_, i) => i + 1);
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
    for (const n of pageList(arg('pages'), await E.pageCount(tab))) {
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
    for (const n of pageList(arg('pages'), await E.pageCount(tab))) {
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
    const before = await E.replaceWord(tab, word, nth, repl);
    await E.reloadToPage(tab, n);
    const after = (await E.findWord(tab, word)).length;
    const lines = (await E.textRuns(tab)).map(t => t.t);
    console.log('page text after reload:', lines.join(' / '));
    if (after !== before - 1 || !lines.some(t => t.includes(repl))) errors.push(`after reload "${word}" appears ${after} times (was ${before}) and "${repl}" ${lines.some(t => t.includes(repl)) ? 'is' : 'is not'} on the page`);
    if (arg('out')) { fs.mkdirSync(arg('out'), { recursive: true }); await E.presentShot(tab, path.join(arg('out'), `p${String(n).padStart(2, '0')}-text.png`)); }
  });
}

async function applyItem(tab, n, item, L) {
  const rows = await E.layers(tab);
  const hits = rows.filter(r => E.sameBox(r.box, item.match));
  if (hits.length !== 1) throw new Error(`p${n}: ${hits.length} layers match ${JSON.stringify(item.match)}`);
  const old = hits[0];
  if (!item.file) {
    await E.selectLayer(tab, old);
    const got = item.to.h != null || item.to.w != null ? await E.placeSelected(tab, item.to) : await E.setArrange(tab, { x: item.to.x, y: item.to.y });
    L(`p${n} moved ${old.label}`, JSON.stringify(old.box), '->', JSON.stringify(got));
    return;
  }
  if (!fs.existsSync(item.file)) throw new Error('missing file ' + item.file);
  const [fw, fh] = E.pngDims(item.file);
  await E.selectLayer(tab, old);
  if (old.img && old.label === 'Image' && E.near(old.box.w / old.box.h, fw / fh, 0.005 * fw / fh)) {
    await E.replaceSelectedImage(tab, item.file);
    const same = (await E.layers(tab)).filter(r => r.img && E.sameBox(r.box, old.box));
    if (same.length !== 1) throw new Error(`p${n}: replaced image not found in its old box`);
    await E.selectLayer(tab, same[0]);
    const got = await E.placeSelected(tab, item.to);
    L(`p${n} replaced in place ${path.basename(item.file)}`, JSON.stringify(got));
    return;
  }
  await E.addImage(tab, item.file);
  const placed = await E.placeSelected(tab, item.to);
  const after = await E.layers(tab);
  const olds = after.filter(r => E.sameBox(r.box, old.box, 1) && !E.sameBox(r.box, placed, 3));
  if (olds.length !== 1) throw new Error(`p${n}: old layer not uniquely found after adding the new image (${olds.length})`);
  await E.deleteLayer(tab, olds[0]);
  for (let t = 0; ; t++) {
    const me = (await E.layers(tab)).find(r => E.sameBox(r.box, placed, 1.5));
    if (!me) throw new Error(`p${n}: new image lost after deleting the old one`);
    if (me.i === old.i) break;
    if (t === 3) throw new Error(`p${n}: new image is layer ${me.i}, could not move it to the old slot ${old.i}`);
    await E.selectLayer(tab, me);
    await E.moveSelected(tab, me.i < old.i ? 'Backward' : 'Forward', Math.abs(old.i - me.i));
  }
  L(`p${n} swapped ${old.label} for ${path.basename(item.file)}`, JSON.stringify(placed));
}

async function verifyPage(tab, n, items) {
  const rows = await E.layers(tab);
  const bad = [];
  items.forEach((item, k) => {
    const hits = rows.filter(r => r.box && E.near(r.box.x, item.to.x, 2.5) && E.near(r.box.y, item.to.y, 2.5) && (item.to.h == null || E.near(r.box.h, item.to.h, 2.5)) && (item.to.w == null || item.to.h != null || E.near(r.box.w, item.to.w, 2.5)));
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
    const pages = arg('pages') === 'all' ? Object.keys(plan).map(Number).sort((a, b) => a - b) : pageList(arg('pages'));
    for (const n of pages) {
      const items = plan[String(n)];
      if (!items) { errors.push(`p${n}: not in the plan`); continue; }
      if (!has('force') && prog.has(`done ${n}`)) { L(`p${n} already done`); continue; }
      try {
        await E.reloadToPage(tab, n);
        for (let k = 0; k < items.length; k++) {
          if (!has('force') && prog.has(`item ${n}:${k}`)) continue;
          await applyItem(tab, n, items[k], L);
          prog.add(`item ${n}:${k}`);
        }
        await E.reloadToPage(tab, n);
        const bad = await verifyPage(tab, n, items);
        await E.presentShot(tab, path.join(out, `p${String(n).padStart(2, '0')}.png`));
        if (bad.length) { errors.push(`p${n} after reload: ${bad.join('; ')}`); L(`p${n} CHECK FAILED`, bad.join('; ')); continue; }
        prog.add(`done ${n}`);
        L(`p${n} done, verified after reload`);
      } catch (e) {
        errors.push(e.message);
        L(`p${n} FAILED: ${e.message}`);
        await tab.screenshot({ path: path.join(out, `diag-p${n}.png`) }).catch(() => {});
      }
    }
  });
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
      await E.wait(tab, 7000);
      st[name] = E.designId(tab.url());
      fs.writeFileSync(stateFile, JSON.stringify(st, null, 1));
      L(name, 'scratch design', tab.url());
      await tab.getByText('Uploads', { exact: true }).first().click({ timeout: 20000 });
      await E.wait(tab, 2500);
      const pick = await E.uploadFile(tab, file);
      await tab.mouse.click(pick.x, pick.y);
      await E.wait(tab, 3000);
      let pos = tab.getByRole('button', { name: 'Position', exact: true });
      if (!(await pos.count())) { await tab.getByRole('button', { name: /More/ }).last().click().catch(() => {}); await E.wait(tab, 800); pos = tab.getByText('Position', { exact: true }); }
      await pos.first().click({ timeout: 10000 }).catch(() => pos.first().click({ force: true, timeout: 10000 }));
      await E.placeSelected(tab, { w, x: left, y: top });
      await shot(tab, 'placed');
      await tab.getByRole('button', { name: 'Edit', exact: true }).first().click({ timeout: 10000 });
      await E.wait(tab, 2500);
      await tab.getByText('See all', { exact: true }).first().click({ timeout: 10000 });
      await E.wait(tab, 2000);
      await tab.getByText('Magic Expand', { exact: true }).first().click({ timeout: 15000 });
      await E.wait(tab, 2500);
      await tab.getByText('Whole Page', { exact: true }).first().click({ timeout: 10000 });
      await E.wait(tab, 2000);
      await tab.getByRole('button', { name: 'Expand', exact: true }).last().click({ timeout: 10000 });
      L(name, 'expanding');
      await tab.getByText('Generate new results', { exact: true }).first().waitFor({ timeout: 150000 });
      await E.wait(tab, 6000);
      await shot(tab, 'results');
      L(name, 'results ready:', path.join(out, `${name}-results.png`));
      return;
    }
    if (!tab) throw new Error(`no open scratch design for ${name}`);
    await tab.bringToFront();
    if (arg('pick') != null) {
      const k = +arg('pick');
      const tiles = await tab.evaluate(() => {
        const ims = [...document.querySelectorAll('img, button')].filter(i => { const r = i.getBoundingClientRect(); return r.x > 80 && r.x < 520 && r.y > 150 && r.y < 700 && r.width > 90 && r.width < 220 && r.height > 90; }).map(i => { const r = i.getBoundingClientRect(); return [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]; });
        const seen = new Set();
        return ims.filter(p => { const key = Math.round(p[0] / 40) + ',' + Math.round(p[1] / 40); if (seen.has(key)) return false; seen.add(key); return true; }).sort((a, b) => a[1] - b[1] || a[0] - b[0]);
      });
      if (!tiles[k]) throw new Error(`no result tile ${k} (found ${tiles.length})`);
      await tab.mouse.click(tiles[k][0], tiles[k][1]);
      await E.wait(tab, 4000);
      await shot(tab, 'picked');
      await tab.getByRole('button', { name: 'Done', exact: true }).last().click({ timeout: 10000 });
      await E.wait(tab, 3000);
      await tab.keyboard.press('Escape');
      await tab.getByText('File', { exact: true }).first().click({ timeout: 10000 });
      await E.wait(tab, 1500);
      await tab.getByText('Download', { exact: true }).first().click({ timeout: 10000 });
      await E.wait(tab, 3000);
      const [dl] = await Promise.all([
        tab.waitForEvent('download', { timeout: 120000 }),
        tab.getByRole('button', { name: 'Download', exact: true }).last().click({ timeout: 10000 }),
      ]);
      const f = path.join(out, name + '.png');
      await dl.saveAs(f);
      L(name, 'downloaded', f);
    } else if (has('more')) {
      await tab.getByText('Generate new results', { exact: true }).first().click({ timeout: 10000 });
      await E.wait(tab, 40000);
      await shot(tab, 'results2');
      L(name, 'more results:', path.join(out, `${name}-results2.png`));
    } else if (has('close')) {
      L(name, 'closing scratch tab; design', st[name], 'stays in Projects as "Untitled design"');
      await E.closeTab(tab);
      delete st[name];
      fs.writeFileSync(stateFile, JSON.stringify(st, null, 1));
    }
  } catch (e) {
    errors.push(e.message);
    if (tab) await shot(tab, 'error').catch(() => {});
  } finally {
    await browser.close().catch(() => {});
  }
}

(async () => {
  try {
    if (has('nudge')) console.log('nudged', await E.nudge(), 'window(s)');
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
