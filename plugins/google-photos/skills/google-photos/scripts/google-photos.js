// Fetch photos from a Google Photos library through the Photos Picker API. The account owner picks the
// photos in Google Photos itself; this script creates the picking session, waits for the pick, downloads
// exactly the picked items, and deletes the session.
//
//   node google-photos.js [--account=<name>] --whoami
//   node google-photos.js [--account=<name>] --pick --out=<dir> [--max=<n>] [--since=YYYY-MM-DD] [--session=<id>] [--width=<px>]
//
// --pick prints a PICKER_URL line and SESSION line, then polls until the pick is done (or the session's own
// timeout passes). --session reuses an existing session instead of creating one, so an interrupted run can
// pick up the same selection. --since drops picked items created before that date. --width downloads a
// resized copy (longest side bounded by the value) instead of the original file.

const fs = require('fs');
const path = require('path');
const { getAuthedClient, assertAccountEmail, fetchAccountEmail } = require('./google-photos-oauth');

const API = 'https://photospicker.googleapis.com/v1';

function arg(name) {
  for (const a of process.argv.slice(2)) {
    if (a === `--${name}`) return true;
    const m = a.match(new RegExp(`^--${name}=(.*)$`));
    if (m) return m[1];
  }
  return null;
}

function seconds(duration, fallback) {
  const m = String(duration || '').match(/^([\d.]+)s$/);
  return m ? parseFloat(m[1]) : fallback;
}

const sleep = (s) => new Promise(r => setTimeout(r, s * 1000));

async function createSession(client, max) {
  const data = max ? { pickingConfig: { maxItemCount: String(max) } } : {};
  const res = await client.request({ url: `${API}/sessions`, method: 'POST', data });
  return res.data;
}

async function getSession(client, id) {
  const res = await client.request({ url: `${API}/sessions/${id}`, method: 'GET' });
  return res.data;
}

async function waitForPick(client, session) {
  const started = Date.now();
  let s = session;
  while (!s.mediaItemsSet) {
    const interval = seconds(s.pollingConfig && s.pollingConfig.pollInterval, 5);
    const timeout = seconds(s.pollingConfig && s.pollingConfig.timeoutIn, 1800);
    if (timeout <= 0 || (Date.now() - started) / 1000 > 3600) {
      throw new Error(`Picking session ${s.id} timed out before any photos were picked.`);
    }
    await sleep(interval);
    // A network blip (DNS, reset) mid-wait is retried on the next poll; an HTTP error from the API is not.
    try {
      s = await getSession(client, s.id);
    } catch (e) {
      if (e.response) throw e;
      console.error(`Poll failed (${e.message}) - retrying.`);
    }
  }
  return s;
}

async function listItems(client, sessionId) {
  const items = [];
  let pageToken;
  do {
    const params = { sessionId, pageSize: 100 };
    if (pageToken) params.pageToken = pageToken;
    const res = await client.request({ url: `${API}/mediaItems`, method: 'GET', params });
    items.push(...(res.data.mediaItems || []));
    pageToken = res.data.nextPageToken;
  } while (pageToken);
  return items;
}

function localName(item, used) {
  const stamp = (item.createTime || '').replace(/(\.\d+)?Z$/, '').replace(/[-:]/g, '').replace('T', '-');
  const base = (item.mediaFile.filename || `${item.id}`).replace(/[^\w.\-]+/g, '_');
  let name = stamp ? `${stamp}_${base}` : base;
  let n = 1;
  while (used.has(name)) name = name.replace(/(\.[^.]*)?$/, (ext) => `_${++n}${ext || ''}`);
  used.add(name);
  return name;
}

async function download(client, item, dest, width) {
  const isVideo = item.type === 'VIDEO';
  const suffix = isVideo ? '=dv' : (width ? `=w${width}-h${width}` : '=d');
  // Picked-item base URLs need the bearer token on every fetch, and stay valid for 60 minutes.
  const res = await client.request({ url: item.mediaFile.baseUrl + suffix, method: 'GET', responseType: 'arraybuffer' });
  fs.writeFileSync(dest, Buffer.from(res.data));
}

async function pick(client) {
  const out = arg('out');
  if (!out || out === true) throw new Error('--pick needs --out=<dir>');
  const since = arg('since');
  const width = arg('width');
  const max = arg('max');
  const sessionArg = arg('session');

  let session = sessionArg && sessionArg !== true ? await getSession(client, sessionArg) : await createSession(client, max);
  console.log(`SESSION: ${session.id}`);
  if (!session.mediaItemsSet) {
    console.log(`PICKER_URL: ${session.pickerUri}`);
    console.log('Waiting for the pick to finish in Google Photos...');
    session = await waitForPick(client, session);
  }

  let items = await listItems(client, session.id);
  if (since) {
    const cutoff = new Date(since).getTime();
    items = items.filter(i => !i.createTime || new Date(i.createTime).getTime() >= cutoff);
  }

  fs.mkdirSync(out, { recursive: true });
  const used = new Set(fs.readdirSync(out));
  const manifest = [];
  for (const item of items) {
    const name = localName(item, used);
    const dest = path.join(out, name);
    await download(client, item, dest, width);
    manifest.push({ id: item.id, createTime: item.createTime, type: item.type, filename: item.mediaFile.filename, path: dest });
    console.log(`DOWNLOADED: ${dest}`);
  }
  fs.writeFileSync(path.join(out, `manifest-${session.id}.json`), JSON.stringify(manifest, null, 2));

  await client.request({ url: `${API}/sessions/${session.id}`, method: 'DELETE' });
  console.log(`Done - ${manifest.length} item(s) in ${out}; session deleted.`);
}

(async () => {
  const client = getAuthedClient();
  await assertAccountEmail(client);
  if (arg('whoami')) {
    console.log(await fetchAccountEmail(client));
  } else if (arg('pick')) {
    await pick(client);
  } else {
    throw new Error('Pass --whoami or --pick --out=<dir>.');
  }
})().catch(e => {
  const detail = e.response && e.response.data ? ' ' + JSON.stringify(e.response.data) : '';
  console.error('Error:', e.message + detail);
  process.exit(1);
});
