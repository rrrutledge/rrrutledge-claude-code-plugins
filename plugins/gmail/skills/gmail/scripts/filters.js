// Manage Gmail filters via the Gmail settings REST API (OAuth path — see gmail-oauth.js). This is the
// programmatic parallel to browser-driving the Gmail filters UI, and mirrors ms-graph's mail.js rule
// commands (--list-rules/--create-rule/--append-rule/--delete-rule) so the two mailboxes feel alike.
//
// List filters:   node filters.js --list-filters [--json]
//                 (every filter, with its criteria.query and action; --json emits the raw array)
// Create filter:  node filters.js --create-filter --query='subject:("A" OR "B") -from:(gmail.com OR outlook.com)' --archive [--mark-read]
//                 [--name="Corporate Subjects"] [--trash]
//                 (--query is the raw Gmail search — OR-lists and -from: fences work verbatim.
//                  --archive removes the INBOX label (Skip the Inbox); --mark-read also removes UNREAD;
//                  --trash routes to Trash instead. --name is a human label for output only — Gmail
//                  filters are anonymous, identified server-side by id + criteria, not by a name.)
// Append phrase:  node filters.js --append-filter=<id> --add-subject='"C" OR "D"'
//                 node filters.js --append-filter=<id> --add-body='"E"'
//                 node filters.js --append-filter=<id> --add='"F"'
//                 (the "add to the bucket" op. Gmail filters can't be edited in place, so this reads the
//                  filter's query, splices the addition in, DELETES the old filter, and CREATES a new one
//                  with the same action — so the id CHANGES. --add-subject splices inside the subject:(…)
//                  group; --add-body inside the leading (…) group; --add appends ` OR <x>` at top level.)
// Delete filter:  node filters.js --delete-filter=<id>
//                 (removes the filter — reversible: re-create it. Does not touch already-archived mail.)

const { getAuthedClient, assertAccountEmail } = require('./gmail-oauth');

const API = 'https://gmail.googleapis.com/gmail/v1/users/me/settings/filters';

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);

// Splice ` OR <addition>` inside the balanced (…) group that opens at `openMarker` (e.g. "subject:(").
// Depth-counts parens so a nested group doesn't fool it. Returns null if the group isn't found/balanced.
function insertIntoGroup(query, openMarker, addition) {
  const start = query.indexOf(openMarker);
  if (start === -1) return null;
  let depth = 0, i;
  for (i = start + openMarker.length - 1; i < query.length; i++) {
    if (query[i] === '(') depth++;
    else if (query[i] === ')') { depth--; if (depth === 0) break; }
  }
  if (depth !== 0) return null;
  return query.slice(0, i) + ' OR ' + addition + query.slice(i);
}

// Splice into the leading top-level (…) group — the body-bucket shape `("x" OR "y") -from:(…)`.
function insertIntoLeadingGroup(query, addition) {
  const offset = query.length - query.trimStart().length;
  if (query[offset] !== '(') return null;
  let depth = 0, i;
  for (i = offset; i < query.length; i++) {
    if (query[i] === '(') depth++;
    else if (query[i] === ')') { depth--; if (depth === 0) break; }
  }
  if (depth !== 0) return null;
  return query.slice(0, i) + ' OR ' + addition + query.slice(i);
}

// Compact human summary of a filter's criteria (query is the load-bearing field; others are rare).
function criteriaSummary(c = {}) {
  if (c.query) return c.query;
  const parts = [];
  for (const k of ['from', 'to', 'subject', 'negatedQuery', 'hasAttachment', 'excludeChats', 'size', 'sizeComparison']) {
    if (c[k] !== undefined) parts.push(`${k}=${c[k]}`);
  }
  return parts.join(' ') || '(no criteria)';
}

function actionSummary(a = {}) {
  const parts = [];
  if ((a.removeLabelIds || []).includes('INBOX')) parts.push('Skip Inbox');
  if ((a.removeLabelIds || []).includes('UNREAD')) parts.push('Mark read');
  if ((a.addLabelIds || []).includes('TRASH')) parts.push('Trash');
  const otherAdd = (a.addLabelIds || []).filter(l => l !== 'TRASH');
  if (otherAdd.length) parts.push('label ' + otherAdd.join(','));
  if (a.forward) parts.push('forward ' + a.forward);
  return parts.join(' + ') || JSON.stringify(a);
}

function buildAction() {
  const removeLabelIds = [];
  const addLabelIds = [];
  if (args.archive) removeLabelIds.push('INBOX');
  if (args['mark-read']) removeLabelIds.push('UNREAD');
  if (args.trash) addLabelIds.push('TRASH');
  const action = {};
  if (removeLabelIds.length) action.removeLabelIds = removeLabelIds;
  if (addLabelIds.length) action.addLabelIds = addLabelIds;
  return action;
}

async function listFilters(client) {
  const res = await client.request({ url: API, method: 'GET' });
  const filters = res.data.filter || [];
  if (args.json) { console.log(JSON.stringify(filters, null, 2)); return; }
  if (!filters.length) { console.log('No filters.'); return; }
  console.log(`${filters.length} filter(s):`);
  for (const f of filters) {
    console.log(`\n--- id: ${f.id}`);
    console.log(`    when: ${criteriaSummary(f.criteria)}`);
    console.log(`    do:   ${actionSummary(f.action)}`);
  }
}

// Create a filter from a raw query + action. Reusable; returns the created filter (with its new id).
async function createFilter(client, { query, action }) {
  if (!query) throw new Error('createFilter requires a query');
  if (!action || !Object.keys(action).length) throw new Error('createFilter requires an action');
  const res = await client.request({
    url: API, method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: { criteria: { query }, action },
  });
  return res.data;
}

async function createFilterCli(client) {
  const query = args.query && args.query !== true ? String(args.query) : null;
  if (!query) throw new Error('--create-filter requires --query="<raw Gmail search>"');
  const action = buildAction();
  if (!Object.keys(action).length) {
    throw new Error('need an action: --archive, --mark-read, and/or --trash');
  }
  const created = await createFilter(client, { query, action });
  const label = args.name && args.name !== true ? ` "${args.name}"` : '';
  console.log(`Created filter${label} (id ${created.id}).`);
  console.log(`    when: ${criteriaSummary(created.criteria)}`);
  console.log(`    do:   ${actionSummary(created.action)}`);
}

async function getFilter(client, id) {
  const res = await client.request({ url: `${API}/${encodeURIComponent(id)}`, method: 'GET' });
  return res.data;
}

// Append a phrase to a bucket: read query, splice, delete old, create new (id changes — Gmail has no
// in-place update). --add-subject/--add-body target a group; --add appends at top level.
async function appendFilter(client) {
  const id = args['append-filter'];
  if (id === true) throw new Error('--append-filter requires an id (from --list-filters)');
  const f = await getFilter(client, id);
  const oldQuery = f.criteria && f.criteria.query;
  if (!oldQuery) {
    throw new Error(`filter ${id} has no criteria.query to append to (criteria: ${JSON.stringify(f.criteria)})`);
  }
  let newQuery;
  if (args['add-subject'] && args['add-subject'] !== true) {
    newQuery = insertIntoGroup(oldQuery, 'subject:(', String(args['add-subject']));
    if (!newQuery) throw new Error(`no "subject:(…)" group found to append to in query: ${oldQuery}`);
  } else if (args['add-body'] && args['add-body'] !== true) {
    newQuery = insertIntoLeadingGroup(oldQuery, String(args['add-body']));
    if (!newQuery) throw new Error(`no leading "(…)" group found to append to in query: ${oldQuery}`);
  } else if (args.add && args.add !== true) {
    newQuery = `${oldQuery} OR ${args.add}`;
  } else {
    throw new Error('--append-filter needs --add-subject, --add-body, or --add');
  }
  await client.request({ url: `${API}/${encodeURIComponent(id)}`, method: 'DELETE' });
  const created = await createFilter(client, { query: newQuery, action: f.action });
  console.log(`Appended to bucket. Deleted old id ${id}; created new id ${created.id}.`);
  console.log(`    new query: ${newQuery}`);
}

async function deleteFilter(client) {
  const id = args['delete-filter'];
  if (id === true) throw new Error('--delete-filter requires an id (from --list-filters)');
  await client.request({ url: `${API}/${encodeURIComponent(id)}`, method: 'DELETE' });
  console.log(`Deleted filter ${id}. Reversible — re-create it with --create-filter.`);
}

module.exports = { createFilter, insertIntoGroup, insertIntoLeadingGroup };

// --- CLI (only when run directly, so `require` of this file is side-effect-free) ---
if (require.main === module) {
  (async () => {
    const client = getAuthedClient();
    await assertAccountEmail(client); // wrong-mailbox guard before touching any filter
    if (args['list-filters']) return listFilters(client);
    if (args['create-filter']) return createFilterCli(client);
    if (args['append-filter']) return appendFilter(client);
    if (args['delete-filter']) return deleteFilter(client);
    throw new Error('Specify --list-filters, --create-filter, --append-filter, or --delete-filter');
  })().catch(e => {
    const detail = e.response && e.response.data ? ' ' + JSON.stringify(e.response.data) : '';
    console.error('Error:', e.message + detail);
    process.exit(1);
  });
}
