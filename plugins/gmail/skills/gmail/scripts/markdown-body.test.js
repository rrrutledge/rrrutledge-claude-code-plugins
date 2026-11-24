const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const os = require('os');
module.paths.push(path.join(os.homedir(), '.claude', 'gmail', 'node_modules'));
const { marked } = require('marked');
const fs = require('fs');
const { looksLikeBrokenTable, styleTables, renderBody, slashImagePaths, embedLocalImages } = require('./markdown-body');

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gmail-inline-'));
fs.mkdirSync(path.join(dir, '.pics'));
fs.writeFileSync(path.join(dir, 'my photo.png'), 'png');
fs.writeFileSync(path.join(dir, '.pics', 'a.jpg'), 'jpg');
fs.writeFileSync(path.join(dir, 'IMG_1.heic'), 'heic');
const embed = md => embedLocalImages(renderBody(slashImagePaths(md), marked, () => {}), dir);

const loose = 'Date | Category | Amount\n10/1 | Hotel | $100\n10/2 | Meals | $50\n';
const good = '| Date | Category | Amount |\n|---|---|--:|\n| 10/1 | Hotel | $100 |\n| 10/2 | Meals | $50 |\n';

test('flags loose pipe rows that render no table', () => {
  assert.ok(looksLikeBrokenTable(loose, marked.parse(loose)));
});

test('accepts a real GFM table', () => {
  assert.ok(!looksLikeBrokenTable(good, marked.parse(good)));
});

test('ignores prose with a single pipe', () => {
  const md = 'Use a | b to pipe.\n';
  assert.ok(!looksLikeBrokenTable(md, marked.parse(md)));
});

test('renderBody gives every table, th and td an inline black border', () => {
  const html = renderBody(good, marked, () => {});
  assert.match(html, /<table style="border:1px solid #000;border-collapse:collapse;">/);
  const cells = html.match(/<t[hd](?:\s[^>]*)?>/g);
  assert.ok(cells.length > 0 && cells.every(c => c.includes('border:1px solid #000;') && c.includes('padding:')));
});

test('styleTables keeps a style the tag already has, including the alignment marked adds', () => {
  const out = styleTables('<table><tr><td align="right" style="color:red">1</td></tr></table>');
  assert.match(out, /<td align="right" style="border:1px solid #000;padding:4px 8px;color:red">/);
});

test('renderBody leaves HTML without a table unchanged', () => {
  assert.strictEqual(renderBody('Hello **there**\n', marked, () => {}), marked.parse('Hello **there**\n'));
});

test('a local image becomes one inline cid image at the spot the body places it', () => {
  const { html, inline } = embed("Before\n\n![Us](<my photo.png>)\n\nAfter\n\n<img src='my photo.png' width=\"120\">");
  assert.deepStrictEqual(inline, [{ path: path.join(dir, 'my photo.png'), filename: 'my photo.png', cid: 'ii_gmailbody0' }]);
  assert.strictEqual(html.split('src="cid:ii_gmailbody0"').length, 3);
  assert.ok(html.indexOf('Before') < html.indexOf('cid:') && html.indexOf('cid:') < html.indexOf('After'));
});

test('a Windows backslash path survives Markdown escaping before a dot folder', () => {
  assert.deepStrictEqual(embed(`![a](${path.join(dir, '.pics', 'a.jpg')})`).inline.map(i => i.filename), ['a.jpg']);
});

test('web, protocol-relative, cid, and data images are left alone', () => {
  const { html, inline } = embed('![w](https://x.test/a.png) <img src="//cdn.test/b.png"> <img src="cid:abc"> <img src="data:image/png;base64,AA">');
  assert.deepStrictEqual(inline, []);
  assert.match(html, /src="https:\/\/x\.test\/a\.png"/);
});

test('a missing or non-displayable image fails the render', () => {
  assert.throws(() => embed('![x](nope.png)'), /inline image not found/);
  assert.throws(() => embed('![x](IMG_1.heic)'), /inline image must be/);
});

test('renderBody warns once for a broken table and not for a good one', () => {
  const seen = [];
  renderBody(loose, marked, m => seen.push(m));
  renderBody(good, marked, m => seen.push(m));
  assert.strictEqual(seen.length, 1);
});
