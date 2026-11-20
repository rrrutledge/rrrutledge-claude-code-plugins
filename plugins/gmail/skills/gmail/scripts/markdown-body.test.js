const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const os = require('os');
module.paths.push(path.join(os.homedir(), '.claude', 'gmail', 'node_modules'));
const { marked } = require('marked');
const { looksLikeBrokenTable, styleTables, renderBody } = require('./markdown-body');

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

test('renderBody warns once for a broken table and not for a good one', () => {
  const seen = [];
  renderBody(loose, marked, m => seen.push(m));
  renderBody(good, marked, m => seen.push(m));
  assert.strictEqual(seen.length, 1);
});
