// Turn Slack mrkdwn style spans (*bold*, _italic_, ~strike~) in plain text into rich_text `text` elements
// carrying a `style`. Run the tests: node --test rich-text-style.test.js

// Alternatives are tried left to right at each position, so a code span or bare URL is consumed whole
// (its asterisks and underscores stay literal) before a style marker can start inside it. A style span
// opens and closes on a non-space character and is not flanked by a word character or the same marker,
// which leaves a lone `*`, `snake_case_words`, and `2 * 3 * 4` as literal text.
const STYLE_RE = new RegExp([
  '(`[^`\\n]*`)',
  '(https?:\\/\\/\\S+)',
  '(?<![\\w*])\\*(?=\\S)([^*\\n]*[^*\\s])\\*(?![\\w*])',
  '(?<![\\w_])_(?=\\S)([^_\\n]*[^_\\s])_(?![\\w_])',
  '(?<![\\w~])~(?=\\S)([^~\\n]*[^~\\s])~(?![\\w~])',
].join('|'), 'g');

function styledTextElements(text) {
  const elements = [];
  let last = 0;
  for (const m of text.matchAll(STYLE_RE)) {
    const style = m[3] !== undefined ? 'bold' : m[4] !== undefined ? 'italic' : m[5] !== undefined ? 'strike' : null;
    if (!style) continue; // a code span or bare URL: leave it inside the surrounding plain text
    if (m.index > last) elements.push({ type: 'text', text: text.slice(last, m.index) });
    elements.push({ type: 'text', text: m[3] ?? m[4] ?? m[5], style: { [style]: true } });
    last = m.index + m[0].length;
  }
  if (last < text.length) elements.push({ type: 'text', text: text.slice(last) });
  return elements;
}

module.exports = { styledTextElements };
