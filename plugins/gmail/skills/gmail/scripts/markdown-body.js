// Render a --body-file Markdown body to HTML with inline-bordered tables, warning when pipe-delimited rows were meant as a table but did not render as one.
// Also embeds the body's local images inline at the spot they appear (slashImagePaths, embedLocalImages).
// Run the tests: node --test markdown-body.test.js

const fs = require('fs');
const path = require('path');

const PIPE_ROW = /^\s*[^|\n]*\|[^|\n]*\|/;

// True when the Markdown has two or more consecutive pipe-delimited lines yet the rendered HTML has no table.
function looksLikeBrokenTable(markdown, html) {
  if (/<table[\s>]/.test(html)) return false;
  const lines = markdown.split('\n');
  return lines.some((line, i) => PIPE_ROW.test(line) && PIPE_ROW.test(lines[i + 1] || ''));
}

const BORDER = 'border:1px solid #000;';
const CELL_PADDING = 'padding:4px 8px;';

// Gmail ignores stylesheets, so give every table black grid lines as inline styles, keeping any style a tag already carries.
function styleTables(html) {
  const addStyle = (tag, css) => html => html.replace(new RegExp(`<${tag}(\\s[^>]*)?>`, 'g'), (m, attrs = '') => {
    const existing = attrs.match(/\sstyle="([^"]*)"/);
    if (existing) return `<${tag}${attrs.replace(existing[0], ` style="${css}${existing[1]}"`)}>`;
    return `<${tag}${attrs} style="${css}">`;
  });
  return [
    addStyle('table', `${BORDER}border-collapse:collapse;`),
    addStyle('th', `${BORDER}${CELL_PADDING}`),
    addStyle('td', `${BORDER}${CELL_PADDING}`),
  ].reduce((out, step) => step(out), html);
}

// Returns the HTML for `markdown` using the `marked` parser passed in, tables with inline grid lines;
// writes a stderr warning for pipe rows that did not render as a table.
function renderBody(markdown, marked, warn = m => console.error(m)) {
  const html = marked.parse(markdown);
  if (looksLikeBrokenTable(markdown, html)) {
    warn('WARNING: the body has consecutive pipe-delimited lines but no table rendered. A Markdown table needs a header row, a separator row (|---|---|), and pipes at both ends of every row; otherwise the rows collapse into run-on text.');
  }
  return styleTables(html);
}

// A Markdown image path's backslashes become forward slashes before rendering: Markdown reads a backslash
// before punctuation as an escape and drops it, so a Windows path like `C:\repo\.tmp\a.jpg` would otherwise
// lose the separator before `.tmp`.
function slashImagePaths(markdown) {
  return markdown.replace(/(!\[[^\]\n]*\]\(\s*<?)([^)>\n]+)/g, (m, open, dest) => open + dest.replace(/\\/g, '/'));
}

// The image types mail clients show inline; any other file type arrives as a broken picture.
const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'];

// An image in the body whose source is a local file - Markdown `![alt](photo.jpg)` or raw
// `<img src="photo.jpg" width="400">` - becomes an inline image shown at that spot in the message.
// Its src is rewritten to `cid:<id>`, and the returned `inline` list names each file and its cid for the
// MIME builder to attach. The cid follows Gmail's own `ii_` shape so its compose editor keeps the image
// inline through send (see inlineList in gmail.js). A relative path resolves against baseDir. A source
// with a scheme (https:, cid:, data:) or a protocol-relative `//host` is left alone. An image with no
// style of its own gets max-width:100% and height:auto so a full-size phone photo fits the reading pane
// without stretching. marked percent-encodes the src (a space becomes %20), so it is decoded before use.
function embedLocalImages(html, baseDir) {
  const byPath = new Map();
  const out = html.replace(/<img\b[^>]*>/gi, (tag) => {
    const m = tag.match(/\ssrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
    const raw = m && (m[1] ?? m[2] ?? m[3]);
    if (!m || /^([a-z][a-z0-9+.-]+:|\/\/)/i.test(raw)) return tag;
    let src = raw.replace(/&amp;/g, '&');
    try { src = decodeURI(src); } catch { /* keep as written */ }
    const file = path.resolve(baseDir, src);
    if (!fs.existsSync(file)) throw new Error(`inline image not found: ${file} (from src="${raw}" in the body file)`);
    if (!IMAGE_EXTENSIONS.includes(path.extname(file).toLowerCase())) {
      throw new Error(`inline image must be ${IMAGE_EXTENSIONS.join(' ')}: ${file}`);
    }
    if (!byPath.has(file)) byPath.set(file, { path: file, filename: path.basename(file), cid: `ii_gmailbody${byPath.size}` });
    const rewritten = tag.replace(m[0], ` src="cid:${byPath.get(file).cid}"`);
    return /\sstyle\s*=/i.test(rewritten) ? rewritten : rewritten.replace(/^<img\b/i, '<img style="max-width:100%; height:auto"');
  });
  return { html: out, inline: [...byPath.values()] };
}

module.exports = { looksLikeBrokenTable, styleTables, renderBody, slashImagePaths, embedLocalImages };
