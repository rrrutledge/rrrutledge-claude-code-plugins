const { test } = require('node:test');
const assert = require('node:assert');
const { richTextBlocks, rawTokensIn, mentionIds, userIdsIn, outsideSelfDm } = require('./draft-body');

const section = (text) => richTextBlocks(text)[0].elements[0].elements;

test('a public channel mention becomes a channel element', () => {
  assert.deepStrictEqual(section('in <#C0123ABCD>'), [
    { type: 'text', text: 'in ' }, { type: 'channel', channel_id: 'C0123ABCD' },
  ]);
});

test('a private channel mention becomes a channel element', () => {
  assert.deepStrictEqual(section('in the <#G0134RBUXHP> channel'), [
    { type: 'text', text: 'in the ' }, { type: 'channel', channel_id: 'G0134RBUXHP' }, { type: 'text', text: ' channel' },
  ]);
});

test('a parsed body has no raw tokens', () => {
  assert.deepStrictEqual(rawTokensIn(richTextBlocks('hi <@U0123ABCD> see <#G0134RBUXHP>')), []);
});

test('literal tokens left in text are reported', () => {
  const blocks = [{ type: 'rich_text', elements: [{ type: 'rich_text_section', elements: [
    { type: 'text', text: 'in the <#G0134RBUXHP> channel' }, { type: 'text', text: ' and U0123ABCDE' },
  ] }] }];
  assert.deepStrictEqual(rawTokensIn(blocks), ['<#G0134RBUXHP>', 'U0123ABCDE']);
});

test('ordinary words are not reported', () => {
  assert.deepStrictEqual(rawTokensIn(richTextBlocks('HELLO WORLD, see CONFERENCES soon')), []);
});

test('mentionIds lists each mentioned user once, skipping channels and links', () => {
  assert.deepStrictEqual(
    mentionIds('hi <@U0123ABCD> and <@W0999ZZZZ|pat>, see <#C0123ABCD> and <https://x.y|z>, thanks <@U0123ABCD>'),
    ['U0123ABCD', 'W0999ZZZZ']);
});

test('outsideSelfDm is true everywhere but the user\'s own self-DM', () => {
  const me = 'U0ME00000';
  assert.strictEqual(outsideSelfDm(me, 'C0123ABCD', {}), true);
  assert.strictEqual(outsideSelfDm(me, 'D0123ABCD', { is_im: true, user: 'U0OTHER00' }), true);
  assert.strictEqual(outsideSelfDm(me, 'D0123ABCD', { is_im: true, user: me }), false);
  assert.strictEqual(outsideSelfDm(me, me, {}), false);
});

test('userIdsIn counts user elements and skips raw tokens left in text', () => {
  const blocks = richTextBlocks('hi <@U0123ABCD> and <@U0123ABCD>');
  blocks[0].elements[0].elements.push({ type: 'text', text: ' <@U0999ZZZZ>' });
  assert.deepStrictEqual(userIdsIn(blocks), ['U0123ABCD']);
});
