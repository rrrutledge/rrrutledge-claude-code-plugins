const { test } = require('node:test');
const assert = require('node:assert');
const { excludeAddresses } = require('./recipients');

test('removes a bare address case-insensitively', () => {
  assert.strictEqual(excludeAddresses('a@x.com, Daniel@Y.com, c@z.com', 'daniel@y.com'), 'a@x.com, c@z.com');
});

test('matches the address inside a Name <addr> form', () => {
  assert.strictEqual(excludeAddresses('"Smith, Dan" <dan@y.com>, a@x.com', 'DAN@y.com'), 'a@x.com');
});

test('accepts an exclude entry written as Name <addr> and several entries', () => {
  assert.strictEqual(excludeAddresses('a@x.com, b@x.com, c@x.com', 'Bee <b@x.com>, a@x.com'), 'c@x.com');
});

test('empty exclude leaves the list unchanged', () => {
  assert.strictEqual(excludeAddresses('a@x.com, b@x.com', ''), 'a@x.com, b@x.com');
});

test('excluding everyone yields an empty string', () => {
  assert.strictEqual(excludeAddresses('a@x.com', 'a@x.com'), '');
});
