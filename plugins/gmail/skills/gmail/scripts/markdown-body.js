// Render a --body-file Markdown body to HTML with inline-bordered tables, warning when pipe-delimited rows were meant as a table but did not render as one.
// Run the tests: node --test markdown-body.test.js

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

module.exports = { looksLikeBrokenTable, styleTables, renderBody };
