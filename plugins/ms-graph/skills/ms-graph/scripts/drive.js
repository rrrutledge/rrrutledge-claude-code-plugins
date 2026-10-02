// Read personal OneDrive items via Microsoft Graph (requires the Files.Read scope).
//
// Web link:  node drive.js weburl "<path-under-onedrive-root>"
//            prints the item's webUrl — a one-click onedrive.live.com deep link that opens
//            the file or folder in the browser.
// List:      node drive.js list "<folder-path-under-onedrive-root>"
//            prints each child's name and webUrl (path defaults to the drive root).
//
// Paths are relative to the OneDrive root, e.g. "Claude/job-applications/Contoso".

const { getGraphClient } = require('./graph-client');

const [command, rawPath] = process.argv.slice(2);

// Graph addresses a path-based item as /me/drive/root:/<path>: — encode each segment but
// keep the slashes that separate them.
function itemApiPath(p) {
  const clean = String(p || '').replace(/^\/+|\/+$/g, '');
  if (!clean) return '/me/drive/root';
  const encoded = clean.split('/').map(encodeURIComponent).join('/');
  return `/me/drive/root:/${encoded}:`;
}

async function weburl(client) {
  if (!rawPath) throw new Error('weburl requires a path, e.g. weburl "Claude/job-applications"');
  const item = await client.api(itemApiPath(rawPath)).select('name,webUrl').get();
  console.log(item.webUrl);
}

async function list(client) {
  const base = itemApiPath(rawPath);
  const api = base === '/me/drive/root' ? '/me/drive/root/children' : `${base}/children`;
  const data = await client.api(api).select('name,webUrl,folder,file').get();
  const items = data.value || [];
  if (!items.length) { console.log('(empty)'); return; }
  items.forEach(i => {
    const kind = i.folder ? '[dir] ' : '      ';
    console.log(`${kind}${i.name}\n      ${i.webUrl}`);
  });
}

if (require.main === module) {
  (async () => {
    const client = await getGraphClient();
    if (command === 'weburl') return weburl(client);
    if (command === 'list') return list(client);
    throw new Error('Specify a command: weburl <path> | list [folder-path]');
  })().catch(e => { console.error('Error:', e.message); process.exit(1); });
}
