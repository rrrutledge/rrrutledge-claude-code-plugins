// Render a --body-file Markdown body to HTML, warning when pipe-delimited rows were meant as a table but did not render as one.
// Run the tests: node --test markdown-body.test.js

const PIPE_ROW = /^\s*[^|\n]*\|[^|\n]*\|/;

// True when the Markdown has two or more consecutive pipe-delimited lines yet the rendered HTML has no table.
function looksLikeBrokenTable(markdown, html) {
  if (/<table[\s>]/.test(html)) return false;
  const lines = markdown.split('\n');
  return lines.some((line, i) => PIPE_ROW.test(line) && PIPE_ROW.test(lines[i + 1] || ''));
}

// Returns the HTML for `markdown` using the `marked` parser passed in; writes a stderr warning for a broken table.
function renderBody(markdown, marked, warn = m => console.error(m)) {
  const html = marked.parse(markdown);
  if (looksLikeBrokenTable(markdown, html)) {
    warn('WARNING: the body has consecutive pipe-delimited lines but no table rendered. A Markdown table needs a header row, a separator row (|---|---|), and pipes at both ends of every row; otherwise the rows collapse into run-on text.');
  }
  return html;
}

module.exports = { looksLikeBrokenTable, renderBody };
