// Build the rich_text blocks of a Slack draft from a mrkdwn body, and check the result for leftovers.
// Run the tests: node --test draft-body.test.js
const { styledTextElements } = require('./rich-text-style');

// A public channel id starts with C, a private channel or group with G.
const MENTION_RE = /<(https?:[^|>]+)(?:\|([^>]+))?>|<@([UW][A-Z0-9]+)(?:\|[^>]*)?>|<#([CG][A-Z0-9]+)(?:\|[^>]*)?>|<!(here|channel)>/g;

// A draft's body is rich_text blocks, not mrkdwn: split each mrkdwn token into its own element so it
// survives as a link, user chip, channel chip, or @here/@channel broadcast instead of literal `<...>`
// text. The plain text between tokens carries its *bold*, _italic_, and ~strike~ spans as styled text.
function richTextBlocks(text) {
  const elements = [];
  let last = 0;
  for (const m of text.matchAll(MENTION_RE)) {
    if (m.index > last) elements.push(...styledTextElements(text.slice(last, m.index)));
    if (m[3]) elements.push({ type: 'user', user_id: m[3] });
    else if (m[4]) elements.push({ type: 'channel', channel_id: m[4] });
    else if (m[5]) elements.push({ type: 'broadcast', range: m[5] });
    else elements.push(m[2] ? { type: 'link', url: m[1], text: m[2] } : { type: 'link', url: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) elements.push(...styledTextElements(text.slice(last)));
  return [{ type: 'rich_text', elements: [{ type: 'rich_text_section', elements }] }];
}

// Raw mention tokens or bare Slack ids left in the text elements of saved blocks: a staged draft that
// still shows `<#G0134RBUXHP>` or `U012ABCDE` is broken, not saved.
const RAW_RE = /<[#@!][^>]*>|\b[CGUW](?=[A-Z]*\d)[A-Z0-9]{8,}\b/g;
function rawTokensIn(blocks) {
  const found = [];
  const walk = (els) => {
    for (const e of els || []) {
      if (e.type === 'text') found.push(...(e.text || '').match(RAW_RE) || []);
      else if (e.elements) walk(e.elements);
    }
  };
  for (const b of blocks || []) walk(b.elements);
  return found;
}

// The distinct user ids a mrkdwn body mentions, in order of first appearance.
function mentionIds(text) {
  return [...new Set([...text.matchAll(MENTION_RE)].map(m => m[3]).filter(Boolean))];
}

module.exports = { richTextBlocks, rawTokensIn, mentionIds };
