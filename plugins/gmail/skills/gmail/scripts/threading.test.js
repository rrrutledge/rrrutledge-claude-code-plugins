const test = require('node:test');
const assert = require('node:assert');
const { isReaction, pickLatest, unfoldThreadHeaders, isSingleEmoji } = require('./threading');

test('pulls a folded In-Reply-To and References id back onto the header line', () => {
  const raw = Buffer.from('From: a@x\r\nIn-Reply-To:\r\n <long@id>\r\nReferences: \r\n <a@id>\r\n\t<b@id>\r\n\r\nIn-Reply-To:\r\n body stays');
  assert.strictEqual(unfoldThreadHeaders(raw).toString(),
    'From: a@x\r\nIn-Reply-To: <long@id>\r\nReferences: <a@id>\r\n\t<b@id>\r\n\r\nIn-Reply-To:\r\n body stays');
});

test('accepts exactly one emoji', () => {
  for (const ok of ['👍', '❤️', '👍🏽', '👨‍👩‍👧', '🇺🇸', '1️⃣']) assert.ok(isSingleEmoji(ok), ok);
  for (const bad of ['', 'ok', '👍👍', 'a👍', '©']) assert.ok(!isSingleEmoji(bad), bad);
});
const msg = (id, internalDate, extra = {}) => ({ id, internalDate: String(internalDate), labelIds: [], payload: { mimeType: 'text/html' }, ...extra });

test('picks the newest message by send time', () => {
  assert.strictEqual(pickLatest([msg('a', 1), msg('c', 3), msg('b', 2)]).id, 'c');
});

test('skips drafts', () => {
  assert.strictEqual(pickLatest([msg('a', 1), msg('d', 9, { labelIds: ['DRAFT'] })]).id, 'a');
});

test('skips emoji reactions, including a nested reaction part', () => {
  const reaction = msg('r', 9, { payload: { mimeType: 'multipart/alternative', parts: [{ mimeType: 'text/vnd.google.email-reaction+json' }] } });
  assert.ok(isReaction(reaction.payload));
  assert.strictEqual(pickLatest([msg('a', 1), reaction]).id, 'a');
});

test('returns null when nothing qualifies', () => {
  assert.strictEqual(pickLatest([msg('d', 1, { labelIds: ['DRAFT'] })]), null);
  assert.strictEqual(pickLatest([]), null);
});
