// Canva Connect API client — asset upload and folder management, via Node's built-in fetch and
// the OAuth helper in canva-oauth.js. Every call goes through canvaRequest(), which attaches a
// fresh (auto-refreshed) bearer token and throws on any non-2xx response.
//
// Create a folder:        node canva.js --create-folder --name="..." [--parent-folder-id=root]
//                          (parent-folder-id defaults to "root"; pass an existing folder's id to
//                           nest one folder inside another.)
//
// Upload an asset:         node canva.js --upload-asset --file=<path> [--name="..."] [--folder-id=<id>]
//                          (name defaults to the file's basename, truncated to Canva's 50-char cap;
//                           when --folder-id is given, the asset is moved into that folder once the
//                           upload job succeeds — a fresh upload always lands in the account's default
//                           Uploads location first, so grouping into a folder is a second call.)
//
// List a folder's items:   node canva.js --list-folder --folder-id=<id>
//
// Move an item to folder:  node canva.js --move-to-folder --item-id=<id> --to-folder-id=<id>
//
// The Asset Upload API is asynchronous: POST /asset-uploads returns a job immediately, and the
// caller polls GET /asset-uploads/{jobId} until status leaves "in_progress" — pollAssetUploadJob
// does that here so every command above returns only once the job has actually settled.

const fs = require('fs');
const path = require('path');
const { getAccessToken } = require('./canva-oauth');

const API_BASE = 'https://api.canva.com/rest/v1';
const MAX_NAME_LEN = 50;
const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 60000;

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);

async function canvaRequest(method, endpoint, { headers = {}, body } = {}) {
  const accessToken = await getAccessToken();
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method,
    headers: { Authorization: `Bearer ${accessToken}`, ...headers },
    body,
  });
  const text = await res.text();
  const parsed = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(`${method} ${endpoint} failed (${res.status}): ${text}`);
  return parsed;
}

async function pollAssetUploadJob(jobId) {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const { job } = await canvaRequest('GET', `/asset-uploads/${jobId}`);
    if (job.status === 'success') return job.asset;
    if (job.status === 'failed') throw new Error(`Asset upload job failed: ${JSON.stringify(job.error)}`);
    await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));
  }
  throw new Error(`Asset upload job ${jobId} did not settle within ${POLL_TIMEOUT_MS}ms`);
}

async function uploadAsset(filePath, name, folderId) {
  const fileName = (name || path.basename(filePath)).slice(0, MAX_NAME_LEN);
  const nameB64 = Buffer.from(fileName).toString('base64');
  const fileContent = fs.readFileSync(filePath);
  const { job } = await canvaRequest('POST', '/asset-uploads', {
    headers: {
      'Content-Type': 'application/octet-stream',
      'Asset-Upload-Metadata': JSON.stringify({ name_base64: nameB64 }),
    },
    body: fileContent,
  });
  const asset = await pollAssetUploadJob(job.id);
  if (folderId) {
    await canvaRequest('POST', '/folders/move', {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_folder_id: folderId, item_id: asset.id }),
    });
  }
  return asset;
}

async function createFolder(name, parentFolderId) {
  const { folder } = await canvaRequest('POST', '/folders', {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, parent_folder_id: parentFolderId || 'root' }),
  });
  return folder;
}

async function listFolder(folderId) {
  const { items } = await canvaRequest('GET', `/folders/${folderId}/items`);
  return items;
}

async function moveToFolder(itemId, toFolderId) {
  await canvaRequest('POST', '/folders/move', {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ to_folder_id: toFolderId, item_id: itemId }),
  });
}

(async () => {
  if (args['create-folder']) {
    if (!args['name']) throw new Error('--name required');
    const folder = await createFolder(args['name'], args['parent-folder-id']);
    console.log(JSON.stringify(folder, null, 2));
  } else if (args['upload-asset']) {
    if (!args['file']) throw new Error('--file required');
    const asset = await uploadAsset(args['file'], args['name'], args['folder-id']);
    console.log(JSON.stringify(asset, null, 2));
  } else if (args['list-folder']) {
    if (!args['folder-id']) throw new Error('--folder-id required');
    const items = await listFolder(args['folder-id']);
    console.log(JSON.stringify(items, null, 2));
  } else if (args['move-to-folder']) {
    if (!args['item-id']) throw new Error('--item-id required');
    if (!args['to-folder-id']) throw new Error('--to-folder-id required');
    await moveToFolder(args['item-id'], args['to-folder-id']);
    console.log(`Moved ${args['item-id']} to folder ${args['to-folder-id']}.`);
  } else {
    throw new Error('Nothing to do — pass --create-folder, --upload-asset, --list-folder, or --move-to-folder');
  }
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
