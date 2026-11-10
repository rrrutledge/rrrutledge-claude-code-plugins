// Read personal OneNote via Microsoft Graph (requires the Notes.Read scope).
//
// Notebooks: node onenote.js notebooks
// Sections:  node onenote.js sections "<notebook name or id>"
// Pages:     node onenote.js pages "<section name or id>"
// Find:      node onenote.js find "<text in a page title>"
// One page:  node onenote.js page "<page id>"   (plain text; links print as "text (url)")
//
// Names match case-insensitively on a substring; ids match exactly.

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };

// An entity naming a code point outside Unicode stays as written.
function codePointOr(original, cp) {
  return cp <= 0x10ffff ? String.fromCodePoint(cp) : original;
}

// OneNote page content is HTML; keep paragraph, line, list, and table-row boundaries as
// line breaks and keep each link's target next to its text.
function htmlToText(html) {
  return String(html || '')
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, '')
    .replace(/<head[\s\S]*?<\/head>/gi, '')
    .replace(/<a\b[^>]*?href=(?:"([^"]*)"|'([^']*)')[^>]*>([\s\S]*?)<\/a>/gi, (_, dq, sq, text) => {
      const href = dq ?? sq;
      const label = text.replace(/<[^>]+>/g, '').trim();
      return !label || label === href ? href : `${label} (${href})`;
    })
    .replace(/<li\b[^>]*>/gi, '\n- ')
    .replace(/<br\s*\/?>|<\/(p|div|h[1-6]|tr|ul|ol|table)>/gi, '\n')
    .replace(/<\/t[dh]>/gi, '\t')
    .replace(/<[^>]+>/g, '')
    .replace(/&#(\d+);/g, (m, n) => codePointOr(m, Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (m, n) => codePointOr(m, parseInt(n, 16)))
    .replace(/&([a-z]+);/gi, (m, name) => ENTITIES[name.toLowerCase()] ?? m)
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

async function getAll(client, api, select) {
  let data = await client.api(api).select(select).top(100).get();
  const items = [...(data.value || [])];
  while (data['@odata.nextLink']) {
    data = await client.api(data['@odata.nextLink']).get();
    items.push(...(data.value || []));
  }
  return items;
}

// The one item whose id equals the query, else whose name equals it, else whose name contains it.
function resolve(items, kind, query) {
  const q = query.toLowerCase();
  const name = i => String(i.displayName || '').toLowerCase();
  const matches = [i => i.id === query, i => name(i) === q, i => name(i).includes(q)]
    .map(test => items.filter(test)).find(m => m.length) || [];
  if (!matches.length) throw new Error(`No ${kind} matches "${query}"`);
  if (matches.length > 1) {
    throw new Error(`"${query}" matches ${matches.length} ${kind}s; be more specific:\n` +
      matches.map(m => `  ${m.displayName}  ${m.id}`).join('\n'));
  }
  return matches[0];
}

async function notebooks(client) {
  const items = await getAll(client, '/me/onenote/notebooks', 'id,displayName');
  items.forEach(n => console.log(`${n.displayName}\n      ${n.id}`));
}

async function sections(client, query) {
  if (!query) throw new Error('sections requires a notebook name or id');
  const nb = resolve(await getAll(client, '/me/onenote/notebooks', 'id,displayName'), 'notebook', query);
  const items = await getAll(client, `/me/onenote/notebooks/${nb.id}/sections`, 'id,displayName');
  items.forEach(s => console.log(`${s.displayName}\n      ${s.id}`));
}

function printPages(pages) {
  if (!pages.length) { console.log('(none)'); return; }
  pages.forEach(p => console.log(`${p.title || '(untitled)'}\n      ${p.id}`));
}

async function pages(client, query) {
  if (!query) throw new Error('pages requires a section name or id');
  const sec = resolve(await getAll(client, '/me/onenote/sections', 'id,displayName'), 'section', query);
  printPages(await getAll(client, `/me/onenote/sections/${sec.id}/pages`, 'id,title'));
}

async function find(client, query) {
  if (!query) throw new Error('find requires text to look for in page titles');
  const all = await getAll(client, '/me/onenote/pages', 'id,title');
  printPages(all.filter(p => String(p.title || '').toLowerCase().includes(query.toLowerCase())));
}

async function page(client, id) {
  if (!id) throw new Error('page requires a page id');
  const html = await client.api(`/me/onenote/pages/${id}/content`).getStream();
  const chunks = [];
  for await (const c of html) chunks.push(c);
  console.log(htmlToText(Buffer.concat(chunks).toString('utf8')));
}

if (require.main === module) {
  const [command, arg] = process.argv.slice(2);
  // Loaded here, not at the top, so requiring this file for htmlToText needs no Graph dependencies.
  const { getGraphClient } = require('./graph-client');
  (async () => {
    const client = await getGraphClient();
    if (command === 'notebooks') return notebooks(client);
    if (command === 'sections') return sections(client, arg);
    if (command === 'pages') return pages(client, arg);
    if (command === 'find') return find(client, arg);
    if (command === 'page') return page(client, arg);
    throw new Error('Specify a command: notebooks | sections <notebook> | pages <section> | find <title text> | page <id>');
  })().catch(e => { console.error('Error:', e.message); process.exit(1); });
}

module.exports = { htmlToText };
