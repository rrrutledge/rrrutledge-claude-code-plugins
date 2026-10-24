const test = require('node:test');
const assert = require('node:assert');
const { isReaction, pickLatest } = require('./threading');

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
