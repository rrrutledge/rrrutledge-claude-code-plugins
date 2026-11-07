const { test } = require('node:test');
const assert = require('node:assert');
const { unanswered } = require('./unanswered');

const ME = 'UME';
const msg = (ts, user, extra = {}) => ({ ts, user, text: `m${ts}`, ...extra });
const tsOf = (list) => list.map(m => m.ts);

test('messages after my last reply are unanswered, oldest first', () => {
  const history = [msg('5.0', 'UA'), msg('4.0', 'UA'), msg('3.0', ME), msg('2.0', 'UA')];
  assert.deepStrictEqual(tsOf(unanswered(history, ME)), ['4.0', '5.0']);
});

test('nothing is unanswered when my message is the newest', () => {
  assert.deepStrictEqual(unanswered([msg('2.0', ME), msg('1.0', 'UA')], ME), []);
});

test('with no reply from me, every message from the other person is unanswered', () => {
  assert.deepStrictEqual(tsOf(unanswered([msg('2.0', 'UA'), msg('1.0', 'UA')], ME)), ['1.0', '2.0']);
});

test('my reaction answers that message and everything before it', () => {
  const history = [
    msg('3.0', 'UA'),
    msg('2.0', 'UA', { reactions: [{ name: '+1', users: [ME] }] }),
    msg('1.0', 'UA'),
  ];
  assert.deepStrictEqual(tsOf(unanswered(history, ME)), ['3.0']);
});

test('a reaction from someone else answers nothing', () => {
  const history = [msg('1.0', 'UA', { reactions: [{ name: '+1', users: ['UB'] }] })];
  assert.deepStrictEqual(tsOf(unanswered(history, ME)), ['1.0']);
});

test('bot posts and system messages are never unanswered', () => {
  const history = [
    msg('3.0', 'UBOT', { bot_id: 'B1' }),
    msg('2.0', 'UA', { subtype: 'channel_join' }),
    msg('1.0', ME),
  ];
  assert.deepStrictEqual(unanswered(history, ME), []);
});

test('in a group DM, every other member counts', () => {
  const history = [msg('3.0', 'UB'), msg('2.0', 'UA'), msg('1.0', ME)];
  assert.deepStrictEqual(tsOf(unanswered(history, ME)), ['2.0', '3.0']);
});
