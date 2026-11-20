const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const os = require('os');
module.paths.push(path.join(os.homedir(), '.claude', 'gmail', 'node_modules'));
const { marked } = require('marked');
const { looksLikeBrokenTable, renderBody } = require('./markdown-body');

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

test('renderBody warns once for a broken table and not for a good one', () => {
  const seen = [];
  renderBody(loose, marked, m => seen.push(m));
  renderBody(good, marked, m => seen.push(m));
  assert.strictEqual(seen.length, 1);
});
