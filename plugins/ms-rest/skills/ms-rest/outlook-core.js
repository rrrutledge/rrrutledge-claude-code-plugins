// outlook-core.js — shared token + REST plumbing for the `ms-rest` skill.
//
// Owns the one thing every ms-rest command needs: a valid bearer token for the Outlook REST API
// v2.0 (https://outlook.office.com/api/v2.0) and a helper that calls it with one automatic 401
// refresh and a loud 410-Gone guard. Both `outlook-mail.js` (mail) and `outlook-calendar.js`
// (calendar) require this module so auth lives in exactly one place.
//
// TOKEN: Outlook web (outlook.cloud.microsoft) holds a bearer token whose audience is
// https://outlook.office.com with Mail.ReadWrite/Mail.Send (and Calendars.ReadWrite) scopes. We
// sniff that token off the live browser session via CDP and cache it at .tmp/outlook-token.json
// with its real JWT expiry (typically 1-2 days). While the cached token is valid, commands run with
// no browser at all; the browser is opened only to (re)sniff when the cache is missing/expired or a
// call returns 401. See SKILL.md for the full token + endpoint rationale.

const fs = require('fs');
const path = require('path');
const https = require('https');

const CDP = 'http://localhost:9222';
const API = 'https://outlook.office.com/api/v2.0';
// Token cache lives at .tmp/ relative to the repo root (process.cwd() when Claude Code runs this).
const TOKEN_FILE = path.join(process.cwd(), '.tmp', 'outlook-token.json');
const RECON_FILE = path.join(process.cwd(), '.tmp', 'outlook-token-recon.json');
const SNIFF_WAIT_MS = 45000;
const EXPIRY_SKEW_S = 120; // treat token as expired this many seconds early

function decodeJwt(tok) {
  try { return JSON.parse(Buffer.from(tok.split('.')[1], 'base64').toString('utf8')); }
  catch { return null; }
}

function readCachedToken() {
  try {
    const meta = JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'));
    if (meta.token && meta.exp && meta.exp - EXPIRY_SKEW_S > Date.now() / 1000) return meta;
  } catch {}
  return null;
}

// --- Token sniff via CDP -----------------------------------------------------
async function sniffToken() {
  const { chromium } = (() => {
    try { return require('playwright-core'); }
    catch { return require(require('path').join(require('os').homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'playwright-core')); }
  })();
  const browser = await chromium.connectOverCDP(CDP);
  const context = browser.contexts()[0];
  if (!context) { await browser.close().catch(() => {}); throw new Error('No CDP browser context (is Edge running on 9222?)'); }

  let page = context.pages().find(p => /outlook\.(cloud\.microsoft|office\.com)\/mail/.test(p.url()));
  let openedPage = false;
  if (!page) {
    page = await context.newPage();
    openedPage = true;
    await page.goto('https://outlook.cloud.microsoft/mail/', { waitUntil: 'domcontentloaded' }).catch(() => {});
  }

  const reconByHost = {};
  let mailToken = null, mailPayload = null;

  const onReq = (req) => {
    const headers = req.headers();
    const auth = headers['authorization'] || headers['Authorization'];
    if (!auth || !auth.toLowerCase().startsWith('bearer ')) return;
    const tok = auth.slice(7);
    const payload = decodeJwt(tok);
    const aud = (payload && payload.aud) || '';
    const scp = (payload && payload.scp) || '';
    let host = ''; try { host = new URL(req.url()).host; } catch {}
    if (host && !reconByHost[host]) reconByHost[host] = { aud, scp };
    const audStr = typeof aud === 'string' ? aud : '';
    if (audStr.includes('outlook.office.com') && /Mail\.ReadWrite/.test(scp) && !mailToken) {
      mailToken = tok; mailPayload = payload;
    }
  };
  context.on('request', onReq);
  page.on('request', onReq);

  try { await page.bringToFront(); } catch {}
  try { await page.reload({ waitUntil: 'domcontentloaded', timeout: 20000 }); } catch {}

  const start = Date.now();
  while (Date.now() - start < SNIFF_WAIT_MS && !mailToken) await new Promise(r => setTimeout(r, 800));

  try { fs.writeFileSync(RECON_FILE, JSON.stringify(reconByHost, null, 2)); } catch {}
  if (openedPage) await page.close().catch(() => {});
  await browser.close().catch(() => {});

  if (!mailToken) {
    const hosts = Object.entries(reconByHost).map(([h, i]) => `${h} (aud=${i.aud})`).join(', ');
    throw new Error('No Mail.ReadWrite token sniffed. Ensure Outlook web is open & signed in. Hosts seen: ' + hosts);
  }
  const meta = {
    token: mailToken,
    aud: mailPayload.aud,
    exp: mailPayload.exp,
    expISO: mailPayload.exp ? new Date(mailPayload.exp * 1000).toISOString() : null,
    capturedISO: new Date().toISOString(),
  };
  fs.mkdirSync(path.dirname(TOKEN_FILE), { recursive: true });
  fs.writeFileSync(TOKEN_FILE, JSON.stringify(meta, null, 2));
  return meta;
}

async function getToken(force) {
  if (!force) { const c = readCachedToken(); if (c) return c; }
  return sniffToken();
}

// --- REST helper -------------------------------------------------------------
// extraHeaders lets calendar calls add `Prefer: outlook.timezone="America/Chicago"` so times come
// back in wall-clock; mail calls omit it.
function apiRequest(method, urlPath, token, body, extraHeaders) {
  return new Promise((resolve, reject) => {
    const u = new URL(urlPath.startsWith('http') ? urlPath : API + urlPath);
    const data = body ? Buffer.from(JSON.stringify(body)) : null;
    const req = https.request({
      method,
      hostname: u.hostname,
      path: u.pathname + u.search,
      headers: {
        Authorization: 'Bearer ' + token,
        Accept: 'application/json',
        ...(extraHeaders || {}),
        ...(data ? { 'Content-Type': 'application/json', 'Content-Length': data.length } : {}),
      },
    }, (res) => {
      let chunks = '';
      res.on('data', d => chunks += d);
      res.on('end', () => resolve({ status: res.statusCode, body: chunks }));
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

// Call with one automatic token refresh on 401.
async function apiCall(method, urlPath, body, extraHeaders) {
  let meta = await getToken(false);
  let res = await apiRequest(method, urlPath, meta.token, body, extraHeaders);
  if (res.status === 401) {
    meta = await getToken(true);
    res = await apiRequest(method, urlPath, meta.token, body, extraHeaders);
  }
  // The Outlook REST API v2.0 is officially deprecated (responses carry Deprecation/Sunset
  // headers) but stays live because Outlook on the web itself uses it with first-party tokens.
  // If it ever truly goes away it returns 410 — surface that loudly so we know to revisit.
  if (res.status === 410) {
    throw new Error('Outlook REST API returned 410 Gone — the deprecated endpoint may finally be '
      + 'retired. See SKILL.md "Endpoint status"; migrating to Microsoft Graph requires a '
      + 'Graph-audience token with Mail scopes (not available via session sniff).');
  }
  return res;
}

function enc(id) { return encodeURIComponent(id); }

module.exports = { API, TOKEN_FILE, getToken, apiRequest, apiCall, enc };
