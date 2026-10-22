const { test } = require('node:test');
const assert = require('node:assert');
const { styledTextElements } = require('./rich-text-style');

const plain = (text) => ({ type: 'text', text });
const styled = (text, style) => ({ type: 'text', text, style: { [style]: true } });

test('bold, italic, and strike spans become styled elements', () => {
  assert.deepStrictEqual(styledTextElements('a *b* c _d_ e ~f~ g'), [
    plain('a '), styled('b', 'bold'), plain(' c '), styled('d', 'italic'), plain(' e '), styled('f', 'strike'), plain(' g'),
  ]);
});

test('a span at the start and end of the text', () => {
  assert.deepStrictEqual(styledTextElements('*all bold*'), [styled('all bold', 'bold')]);
});

test('a lone asterisk and spaced asterisks stay literal', () => {
  assert.deepStrictEqual(styledTextElements('2 * 3 * 4 and a * b'), [plain('2 * 3 * 4 and a * b')]);
});

test('snake_case words stay literal', () => {
  assert.deepStrictEqual(styledTextElements('use my_var_name and other_thing here'), [plain('use my_var_name and other_thing here')]);
});

test('markers inside a URL stay literal', () => {
  assert.deepStrictEqual(styledTextElements('see https://x.com/a_b_c/*d* now'), [plain('see https://x.com/a_b_c/*d* now')]);
});

test('markers inside a code span stay literal', () => {
  assert.deepStrictEqual(styledTextElements('run `*glob* _x_` then *go*'), [plain('run `*glob* _x_` then '), styled('go', 'bold')]);
});

test('a span does not cross a newline', () => {
  assert.deepStrictEqual(styledTextElements('*a\nb*'), [plain('*a\nb*')]);
});
