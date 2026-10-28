const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const os = require('os');
module.paths.push(path.join(os.homedir(), '.claude', 'gmail', 'node_modules'));
const { simpleParser } = require('mailparser');
const { pickTextPart, partAsMime } = require('./body-part');

const leaf = (part, type, extra = {}) => ({ part, type, encoding: 'quoted-printable', parameters: { charset: 'UTF-8' }, ...extra });

test('picks the plain-text alternative nested under attachments', () => {
  const structure = { type: 'multipart/mixed', childNodes: [
    { part: '1', type: 'multipart/related', childNodes: [
      { part: '1.1', type: 'multipart/alternative', childNodes: [leaf('1.1.1', 'text/plain'), leaf('1.1.2', 'text/html')] },
      leaf('1.2', 'image/png', { encoding: 'base64', disposition: 'attachment' }),
    ] },
    leaf('2', 'application/pdf', { encoding: 'base64', disposition: 'attachment' }),
  ] };
  assert.deepStrictEqual(pickTextPart(structure), { part: '1.1.1', type: 'text/plain', charset: 'UTF-8', encoding: 'quoted-printable' });
});

test('falls back to html when there is no plain text', () => {
  const structure = { type: 'multipart/mixed', childNodes: [leaf('1', 'text/html'), leaf('2', 'application/pdf', { disposition: 'attachment' })] };
  assert.strictEqual(pickTextPart(structure).part, '1');
  assert.strictEqual(pickTextPart(structure).type, 'text/html');
});

test('skips a text file sent as an attachment', () => {
  const structure = { type: 'multipart/mixed', childNodes: [leaf('1', 'text/plain', { disposition: 'attachment' }), leaf('2', 'text/html')] };
  assert.strictEqual(pickTextPart(structure).part, '2');
});

test('reads a single-part message as part 1', () => {
  assert.strictEqual(pickTextPart({ type: 'text/plain', encoding: '7bit' }).part, '1');
});

test('returns null when the message has no text part', () => {
  assert.strictEqual(pickTextPart({ type: 'multipart/mixed', childNodes: [leaf('1', 'image/png', { disposition: 'attachment' })] }), null);
});

test('the wrapped part decodes its transfer encoding and charset', async () => {
  const picked = { part: '1', type: 'text/plain', charset: 'UTF-8', encoding: 'quoted-printable' };
  const parsed = await simpleParser(partAsMime(picked, Buffer.from('caf=C3=A9 =E2=80=94 done=\r\n')));
  assert.strictEqual(parsed.text.trim(), 'café — done');
});
