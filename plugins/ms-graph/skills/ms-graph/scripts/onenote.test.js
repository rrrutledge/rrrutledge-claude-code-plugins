// Tests for onenote.js's htmlToText - pure string work, no network calls.
// Run: node --test onenote.test.js
const { test } = require('node:test');
const assert = require('node:assert');

const { htmlToText } = require('./onenote');

test('keeps paragraph and list boundaries as line breaks', () => {
  const html = '<html><head><title>T</title></head><body><p>Kids</p><ul><li>Lego set</li><li>Book</li></ul></body></html>';
  assert.strictEqual(htmlToText(html), 'Kids\n\n- Lego set\n- Book');
});

test('prints a link as "text (url)" and a bare link as the url', () => {
  const html = '<p><a href="https://x.test/a">Telescope</a> and <a href="https://x.test/b">https://x.test/b</a></p>';
  assert.strictEqual(htmlToText(html), 'Telescope (https://x.test/a) and https://x.test/b');
});

test('decodes entities and drops style blocks', () => {
  assert.strictEqual(htmlToText('<style>p{}</style><p>Tom &amp; Jerry&#39;s &lt;3</p>'), "Tom & Jerry's <3");
});
