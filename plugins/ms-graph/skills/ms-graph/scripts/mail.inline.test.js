// Tests for inline body images in mail.js's createDraft, against a fake Graph client - no network calls.
// Run: node --test mail.inline.test.js
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { createDraft } = require('./mail');

const fakeClient = { api: () => ({ post: async (message) => ({ id: 'draft1', message }) }) };
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mail-inline-'));
fs.writeFileSync(path.join(dir, 'my photo.png'), Buffer.from('png-bytes'));

const draft = (body) => createDraft(fakeClient, { to: 'a@x.test', subject: 's', body, baseDir: dir });

test('a local Markdown image becomes one inline attachment referenced by cid at its spot', async () => {
  const d = await draft('Before\n\n![Us](<my photo.png>)\n\nAfter\n\n<img src="my photo.png" width="120">');
  const html = d.message.body.content;
  const [att] = d.message.attachments;
  assert.strictEqual(d.message.attachments.length, 1);
  assert.deepStrictEqual([att.isInline, att.name, att.contentType], [true, 'my photo.png', 'image/png']);
  assert.strictEqual(html.split(`src="cid:${att.contentId}"`).length, 3);
  assert.ok(html.indexOf('Before') < html.indexOf('cid:') && html.indexOf('cid:') < html.indexOf('After'));
  assert.deepStrictEqual(d.inline.map(i => i.file), [path.join(dir, 'my photo.png')]);
});

test('web, cid, and data images are left alone', async () => {
  const d = await draft('![w](https://x.test/a.png) <img src="cid:abc"> <img src="data:image/png;base64,AA">');
  assert.strictEqual(d.message.attachments, undefined);
  assert.match(d.message.body.content, /src="https:\/\/x\.test\/a\.png"/);
});

test('a missing local image fails the draft instead of staging a broken picture', async () => {
  await assert.rejects(draft('![x](nope.png)'), /inline image not found/);
});
