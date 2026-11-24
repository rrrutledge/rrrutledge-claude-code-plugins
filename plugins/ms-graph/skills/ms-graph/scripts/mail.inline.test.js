// Tests for inline body images in mail.js's createDraft, against a fake Graph client - no network calls.
// Run: node --test mail.inline.test.js
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { createDraft } = require('./mail');

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mail-inline-'));
fs.mkdirSync(path.join(dir, '.pics'));
fs.writeFileSync(path.join(dir, 'my photo.png'), Buffer.from('png-bytes'));
fs.writeFileSync(path.join(dir, '.pics', 'a.jpg'), Buffer.from('jpg-bytes'));
fs.writeFileSync(path.join(dir, 'IMG_1.heic'), Buffer.from('heic'));

// Records each Graph call; the draft POST returns the message it was given, the attachment POSTs are kept.
// failAttachment makes every attachment POST reject.
async function draft(body, opts = {}, calls = [], failAttachment = false) {
  const client = { api: (url) => ({
    post: async (payload) => {
      calls.push({ url, verb: 'post', payload });
      if (failAttachment && url.endsWith('/attachments')) throw new Error('413 too large');
      return url === '/me/messages' ? { id: 'd1', message: payload } : {};
    },
    filter() { return this; }, select() { return this; }, top() { return this; },
    get: async () => ({ value: [{ id: 'old' }] }),
    delete: async () => { calls.push({ url, verb: 'delete' }); },
  }) };
  const d = await createDraft(client, { to: 'a@x.test', subject: 's', body, baseDir: dir, ...opts });
  return { d, html: d.message.body.content, attachments: calls.filter(c => c.url.endsWith('/attachments')).map(c => c.payload), calls };
}

test('a local Markdown image becomes one inline attachment referenced by cid at its spot', async () => {
  const { d, html, attachments } = await draft("Before\n\n![Us](<my photo.png>)\n\nAfter\n\n<img src='my photo.png' width=\"120\">");
  const [att] = attachments;
  assert.strictEqual(attachments.length, 1);
  assert.deepStrictEqual([att.isInline, att.name, att.contentType], [true, 'my photo.png', 'image/png']);
  assert.strictEqual(html.split(`src="cid:${att.contentId}"`).length, 3);
  assert.ok(html.indexOf('Before') < html.indexOf('cid:') && html.indexOf('cid:') < html.indexOf('After'));
  assert.deepStrictEqual(d.inline.map(i => i.file), [path.join(dir, 'my photo.png')]);
});

test('a Windows backslash path survives Markdown escaping before a dot folder', async () => {
  const { attachments } = await draft(`![a](${path.join(dir, '.pics', 'a.jpg')})`);
  assert.deepStrictEqual(attachments.map(a => a.name), ['a.jpg']);
});

test('web, protocol-relative, cid, and data images are left alone', async () => {
  const { html, attachments } = await draft('![w](https://x.test/a.png) <img src="//cdn.test/b.png"> <img src="cid:abc"> <img src="data:image/png;base64,AA">');
  assert.strictEqual(attachments.length, 0);
  assert.match(html, /src="https:\/\/x\.test\/a\.png"/);
});

test('a missing or non-displayable image fails before --replace deletes the old draft', async () => {
  const calls = [];
  await assert.rejects(draft('![x](nope.png)', { replace: true }, calls), /inline image not found/);
  assert.deepStrictEqual(calls, []);
  await assert.rejects(draft('![x](IMG_1.heic)'), /inline image must be/);
});

test('a failed attachment deletes the half-built draft and keeps the old one', async () => {
  const calls = [];
  await assert.rejects(draft('![Us](<my photo.png>)', { replace: true }, calls, true), /413/);
  assert.deepStrictEqual(calls.filter(c => c.verb === 'delete').map(c => c.url), ['/me/messages/d1']);
});

test('--replace deletes the other same-subject drafts after the new one is built', async () => {
  const calls = [];
  await draft('![Us](<my photo.png>)', { replace: true }, calls);
  assert.deepStrictEqual(calls.map(c => `${c.verb} ${c.url}`).slice(-2),
    ['post /me/messages/d1/attachments', 'delete /me/messages/old']);
});
