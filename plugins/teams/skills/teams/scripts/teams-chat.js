#!/usr/bin/env node
// teams-chat.js - fast Microsoft Teams chat operations for the drainer teams provider.
//
// Replaces slow browser automation (20–30 s hydrate + per-conversation open) with direct REST
// calls to the Teams internal services that Teams web itself uses, hosted on the Teams web origin
// (https://teams.cloud.microsoft). Listing unread conversations and reading a chat's messages
// become single ~1 s HTTP calls.
//
// WHY NOT MICROSOFT GRAPH: the current Teams web build (new Teams 2.0, teams.cloud.microsoft)
// never calls graph.microsoft.com for chat - a live sniff presented no graph token. Chat runs on
// the Skype-spaces / IC3 services. We target those.
//
// TWO TOKENS (both sniffed from the live Teams web session over CDP, cached together in
// ~/.claude/drainer/teams-ic3-token.json with their real JWT expiries; auto-re-sniffed on expiry or
// a 401):
//   - aud=https://chatsvcagg.teams.microsoft.com  -> the chat AGGREGATOR ("chatsvcagg"). Its
//     /chats feed carries the AUTHORITATIVE per-chat `isRead` flag (the same unread state the
//     Teams UI shows), plus member names and the last-message preview. This is the source of
//     truth for DMs / group chats / meeting chats.
//   - aud=https://ic3.teams.office.com            -> the IC3 chat-service ("chatsvc"). Used to
//     list CHANNELS (team threads), to read a conversation's messages, and to mark read.
//
// IMPORTANT - isRead is NOT the same as IC3 consumptionhorizon. A chat can have its
// consumptionhorizon caught up to the last message yet still be unread (isRead=false). So unread
// detection for chats MUST use the aggregator's isRead, never the horizon.
//
// SEND: the `send` subcommand POSTs to the IC3 messages endpoint (same token as `messages`), so
// sending is a one-liner: node teams-chat.js send <convId> "text". No mark-read: the aggregator's
// isRead is not driven by any replayable HTTP call, so marking read stays browser-driven (the worker
// opens the conversation via browser-chauffeur). See teams-provider.md § CLEAR.
//
// CONFIG (env vars; sensible defaults for Russell's setup):
//   DRAINER_TEAMS_WATCHED_TEAM_ID - the watched team's space id (threadProperties.spaceId / General id)
//   DRAINER_TEAMS_WATCHED_TEAM_NAME - display name for that team
//   DRAINER_SELF_NAME - your display name (labels 1:1/group chats by the other member[s])
//
// REQUIREMENT: signed in to Teams web in the CDP browser (port 9222). The one-time sniff needs
// `playwright` (the drainer adapter exports NODE_PATH=<repo>/node_modules so it resolves).
//
// Usage:
//   node teams-chat.js enumerate [--top 40] [--unread]  -> JSON array of conversations, newest-first
//   node teams-chat.js messages <convId> [--top 20]     -> recent messages (newest-first)
//   node teams-chat.js send <convId> <message> [--html] -> send a message to a conversation
//   node teams-chat.js mark-unread --conversation-id <id> --message-id <id>  -> mark message (and after) unread
//   node teams-chat.js token [--force]                  -> ensure/refresh cached tokens, print status

const fs = require('fs');
const path = require('path');
const os = require('os');
const https = require('https');

const CDP = 'http://localhost:9222';
const REGION = 'amer'; // Russell's geo; the Teams services are region-routed.
const ORIGIN = 'https://teams.cloud.microsoft';
const IC3_BASE = `${ORIGIN}/api/chatsvc/${REGION}`;
const AGG_BASE = `${ORIGIN}/api/csa/${REGION}`;
const TOKEN_DIR = path.join(os.homedir(), '.claude', 'drainer');
const TOKEN_FILE = path.join(TOKEN_DIR, 'teams-ic3-token.json');
const RECON_FILE = path.join(TOKEN_DIR, 'teams-token-recon.json');
const SNIFF_WAIT_MS = 45000;
const EXPIRY_SKEW_S = 120;
const SELF_NAME = process.env.DRAINER_SELF_NAME || 'Russell Rutledge';

// Channel watching is scoped to ONE team - the only team whose channels the user cares about.
// Every other team's channels are intentionally ignored (the rest are noise). Channels within this
// team are matched by their parent-team space id (threadProperties.spaceId), or the General channel
// which IS the team space id itself. Overridable via env for other setups.
const WATCHED_TEAM_SPACE = process.env.DRAINER_TEAMS_WATCHED_TEAM_ID || '';
const WATCHED_TEAM_NAME = process.env.DRAINER_TEAMS_WATCHED_TEAM_NAME || 'R&D Community';

function decodeJwt(tok) {
  try { return JSON.parse(Buffer.from(tok.split('.')[1], 'base64').toString('utf8')); }
  catch { return null; }
}

// Cached tokens are valid only if BOTH audiences are present and unexpired.
function readCachedTokens() {
  try {
    const meta = JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'));
    const now = Date.now() / 1000;
    const ok = (t) => t && t.token && t.exp && t.exp - EXPIRY_SKEW_S > now;
    if (ok(meta.ic3) && ok(meta.agg)) return meta;
  } catch {}
  return null;
}

// --- Token sniff via CDP (grabs both ic3 + agg) ------------------------------
async function sniffTokens() {
  const { chromium } = require('playwright');
  const browser = await chromium.connectOverCDP(CDP);
  const context = browser.contexts()[0];
  if (!context) { await browser.close().catch(() => {}); throw new Error('No CDP browser context (is Edge running on 9222?)'); }

  let page = context.pages().find(p => /teams\.(microsoft\.com|cloud\.microsoft)/.test(p.url()));
  let openedPage = false;
  if (!page) {
    page = await context.newPage();
    openedPage = true;
    await page.goto(`${ORIGIN}/`, { waitUntil: 'domcontentloaded' }).catch(() => {});
  }

  const reconByHost = {};
  let ic3 = null, agg = null;

  const onReq = (req) => {
    const headers = req.headers();
    const auth = headers['authorization'] || headers['Authorization'];
    if (!auth || !auth.toLowerCase().startsWith('bearer ')) return;
    const tok = auth.slice(7);
    const payload = decodeJwt(tok);
    const aud = String((payload && payload.aud) || '');
    let host = ''; try { host = new URL(req.url()).host; } catch {}
    if (host && !reconByHost[host]) reconByHost[host] = { aud, scp: (payload && payload.scp) || '' };
    if (aud.includes('ic3.teams.office.com') && !ic3) ic3 = { token: tok, payload };
    if (aud.includes('chatsvcagg') && !agg) agg = { token: tok, payload };
  };
  context.on('request', onReq);
  page.on('request', onReq);

  try { await page.bringToFront(); } catch {}
  try { await page.goto(`${ORIGIN}/v2/?ctx=chat`, { waitUntil: 'domcontentloaded', timeout: 20000 }); } catch {}

  const start = Date.now();
  while (Date.now() - start < SNIFF_WAIT_MS && !(ic3 && agg)) await new Promise(r => setTimeout(r, 800));

  try { fs.mkdirSync(TOKEN_DIR, { recursive: true }); fs.writeFileSync(RECON_FILE, JSON.stringify(reconByHost, null, 2)); } catch {}
  if (openedPage) await page.close().catch(() => {});
  await browser.close().catch(() => {});

  const missing = [!ic3 && 'ic3.teams.office.com', !agg && 'chatsvcagg.teams.microsoft.com'].filter(Boolean);
  if (missing.length) {
    const hosts = Object.entries(reconByHost).map(([h, i]) => `${h} (aud=${i.aud})`).join(', ');
    throw new Error(`Missing token(s): ${missing.join(', ')}. Ensure Teams web is open & signed in. Hosts seen: ${hosts}`);
  }
  const pack = (x) => ({
    token: x.token, aud: x.payload.aud, exp: x.payload.exp,
    expISO: x.payload.exp ? new Date(x.payload.exp * 1000).toISOString() : null,
  });
  const meta = { ic3: pack(ic3), agg: pack(agg), capturedISO: new Date().toISOString() };
  fs.mkdirSync(path.dirname(TOKEN_FILE), { recursive: true });
  fs.writeFileSync(TOKEN_FILE, JSON.stringify(meta, null, 2));
  return meta;
}

async function getTokens(force) {
  if (!force) { const c = readCachedTokens(); if (c) return c; }
  return sniffTokens();
}

// --- REST helper -------------------------------------------------------------
function request(method, url, token, body) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const data = body ? Buffer.from(JSON.stringify(body)) : null;
    const req = https.request({
      method, hostname: u.hostname, path: u.pathname + u.search,
      headers: {
        Authorization: 'Bearer ' + token, Accept: 'application/json',
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

// Call against a given service ('ic3'|'agg'), with one automatic token refresh on 401.
async function call(which, method, fullPath, body) {
  const base = which === 'agg' ? AGG_BASE : IC3_BASE;
  let toks = await getTokens(false);
  let res = await request(method, base + fullPath, toks[which].token, body);
  if (res.status === 401) {
    toks = await getTokens(true);
    res = await request(method, base + fullPath, toks[which].token, body);
  }
  return res;
}

function enc(id) { return encodeURIComponent(id); }

function htmlToText(html) {
  return String(html || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/\s+/g, ' ').trim();
}

function messageDeepLink(convId, messageId, kind) {
  const ctx = encodeURIComponent(JSON.stringify({ contextType: kind === 'channel' ? 'channel' : 'chat' }));
  return `https://teams.microsoft.com/l/message/${convId}/${messageId}?context=${ctx}`;
}
function convDeepLink(convId, kind) {
  if (kind === 'channel') return `https://teams.microsoft.com/l/channel/${convId}/conversations`;
  return `https://teams.microsoft.com/l/chat/${convId}/conversations`;
}

// --- Enumerate: aggregator chats (authoritative isRead) + IC3 channels -------
async function fetchChats(top) {
  const res = await call('agg', 'GET', `/api/v2/teams/users/me/chats?pageSize=${top}`);
  if (res.status !== 200) throw new Error(`chats HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  const items = JSON.parse(res.body).items || [];
  return items.map(it => {
    const kind = (it.threadType === 'meeting' || it.chatType === 'meeting') ? 'meeting' : (it.isOneOnOne ? 'dm' : 'group');
    const others = (it.members || []).map(m => m.displayName).filter(n => n && n !== SELF_NAME);
    const lm = it.lastMessage || {};
    const label = it.title || others.join(', ') || (kind === 'dm' ? '(1:1 chat)' : '(group chat)');
    return {
      id: it.id,
      type: kind,
      label,
      unread: it.isRead === false,
      muted: it.isMuted === true,
      fromMe: !!it.isLastMessageFromMe,
      lastMessage: {
        id: lm.id || null,
        from: lm.imDisplayName || lm.fromDisplayNameInToken || (it.isLastMessageFromMe ? SELF_NAME : (others[0] || null)),
        time: lm.composeTime || lm.originalArrivalTime || null,
        preview: htmlToText(lm.content || ''),
      },
      lastMessageId: lm.id || null,
      deepLink: convDeepLink(it.id, kind),
    };
  });
}

// Recently-active channels of the single watched team (WATCHED_TEAM_SPACE). We deliberately do NOT
// list every team's channels: there is no clean REST signal for which arbitrary channel is unread
// (that lives only in the fragile delta-sync /updates feed), and the user only cares about this one
// team. Within it, unread = last message past the IC3 read horizon. Only channels with recent
// activity appear (the IC3 conversation list is recency-bounded) - exactly what a drainer wants.
async function fetchWatchedChannels(top) {
  const res = await call('ic3', 'GET', `/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=${top}&startTime=1`);
  if (res.status !== 200) throw new Error(`channels HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  const all = JSON.parse(res.body).conversations || [];
  const inWatchedTeam = (c) => c.id === WATCHED_TEAM_SPACE || (c.threadProperties || {}).spaceId === WATCHED_TEAM_SPACE;
  return all.filter(inWatchedTeam).map(c => {
    const lm = c.lastMessage || {};
    const horizon = (c.properties || {}).consumptionhorizon;
    const lastRead = horizon ? horizon.split(';')[0] : null;
    const unread = lm.id ? (!lastRead || Number(lm.id) > Number(lastRead)) : false;
    const isGeneral = c.id === WATCHED_TEAM_SPACE;
    const topic = (c.threadProperties || {}).topic;
    const label = isGeneral ? `${WATCHED_TEAM_NAME} (General)` : `${WATCHED_TEAM_NAME} / ${topic || c.id}`;
    return {
      id: c.id,
      type: 'channel',
      label,
      unread,
      muted: false,
      fromMe: false,
      lastMessage: {
        id: lm.id || null,
        from: lm.fromDisplayNameInToken || lm.imdisplayname || null,
        time: lm.composetime || lm.originalarrivaltime || null,
        preview: htmlToText(lm.content || ''),
      },
      lastMessageId: lm.id || null,
      deepLink: convDeepLink(c.id, 'channel'),
    };
  });
}

async function cmdEnumerate(top, unreadOnly) {
  // Chats (DMs / group chats / meeting chats) come from the aggregator with authoritative isRead +
  // isMuted. Channels are limited to the single watched team (see fetchWatchedChannels); all other
  // teams' channels are ignored as noise.
  const [chats, channels] = await Promise.all([fetchChats(top), fetchWatchedChannels(top)]);
  let out = [...chats, ...channels];
  out = out.filter(c => !c.muted);
  if (unreadOnly) out = out.filter(c => c.unread);
  out.sort((a, b) => String(b.lastMessage.time || '').localeCompare(String(a.lastMessage.time || '')));
  process.stdout.write(JSON.stringify(out, null, 2) + '\n');
}

async function cmdMessages(convId, top) {
  // Fetch messages and the conversation's IC3 consumptionhorizon in parallel so we can tag each
  // message as unread (id > lastReadId) or context-only (id <= lastReadId).
  const [msgRes, convRes] = await Promise.all([
    call('ic3', 'GET', `/v1/users/ME/conversations/${enc(convId)}/messages?view=msnp24Equivalent&pageSize=${top}`),
    call('ic3', 'GET', `/v1/users/ME/conversations/${enc(convId)}?view=msnp24Equivalent`),
  ]);
  if (msgRes.status !== 200) throw new Error(`messages HTTP ${msgRes.status}: ${msgRes.body.slice(0, 400)}`);
  const value = JSON.parse(msgRes.body).messages || [];
  const kind = (convId.includes('@thread.skype') || convId.includes('@thread.tacv2')) ? 'channel' : 'chat';

  // Parse the read watermark: consumptionhorizon is "<messageId>;<timestamp>;..." - take the first segment.
  let lastReadId = null;
  if (convRes.status === 200) {
    try {
      const props = JSON.parse(convRes.body).properties || {};
      const horizon = props.consumptionhorizon || props.consumptionHorizon || '';
      if (horizon) lastReadId = horizon.split(';')[0];
    } catch {}
  }

  const out = value
    .filter(m => (m.messagetype && m.messagetype.startsWith('RichText')) || m.messagetype === 'Text')
    .map(m => ({
      id: m.id,
      from: m.imdisplayname || m.fromDisplayNameInToken || '(unknown)',
      time: m.composetime || m.originalarrivaltime,
      messageType: m.messagetype,
      text: htmlToText(m.content || ''),
      html: m.content || '',
      deepLink: messageDeepLink(convId, m.id, kind),
      // unread=true when this message's id is numerically after the last-read watermark.
      unread: lastReadId ? Number(m.id) > Number(lastReadId) : null,
    }));
  process.stdout.write(JSON.stringify(out, null, 2) + '\n');
}

// NOTE: there is no REST `clear`/mark-read here. The aggregator's authoritative isRead is NOT driven
// by any replayable HTTP call - the consumptionHorizon/consumptionhorizon PUTs return 200 but do not
// flip isRead (verified by round-trip), and opening the conversation in the UI flips it within ~5s
// via a trouter/websocket signal we can't cleanly replay. So mark-read is browser-driven: the worker
// opens the conversation in Teams web via browser-chauffeur (see teams-provider.md § CLEAR), which
// marks it read exactly as a human would. Verify by re-running `enumerate` - the item leaves unread.

// Mark a specific message (and implicitly all subsequent messages) as unread by setting the IC3
// lastMarkedUnreadMessageId property on the conversation. This is the same call Teams web makes when
// the user right-clicks → Mark as unread. The aggregator's isRead flips to false within ~5s via the
// same trouter/websocket signal that mark-read uses - so this is the REST-accessible inverse.
async function cmdMarkUnread(convId, messageId) {
  if (!convId || !messageId) throw new Error('mark-unread requires --conversation-id and --message-id');
  const res = await call(
    'ic3', 'PUT',
    `/v1/users/ME/conversations/${enc(convId)}/properties/lastmarkedunreadmessageid`,
    { lastmarkedunreadmessageid: messageId },
  );
  if (res.status !== 200 && res.status !== 204) {
    throw new Error(`mark-unread HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  }
  process.stdout.write(`Marked unread from message ${messageId} in ${convId}\n`);
}

async function cmdSend(convId, text, isHtml) {
  if (!convId) throw new Error('send requires a convId');
  if (!text) throw new Error('send requires message text');
  const content = isHtml ? text : `<p>${text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</p>`;
  const body = { content, messagetype: 'RichText/Html', contenttype: 'html' };
  let res = await call('ic3', 'POST', `/v1/users/ME/conversations/${enc(convId)}/messages`, body);
  if (res.status !== 200 && res.status !== 201) {
    res = await call('agg', 'POST', `/api/v2/teams/users/me/chats/${enc(convId)}/messages`, body);
    if (res.status !== 200 && res.status !== 201)
      throw new Error(`send HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  }
  let parsed; try { parsed = JSON.parse(res.body); } catch { throw new Error(`send: non-JSON response (HTTP ${res.status}): ${res.body.slice(0, 200)}`); }
  const id = parsed.id || parsed.OriginalArrivalTime || null;
  const time = parsed.composetime || parsed.OriginalArrivalTime || null;
  process.stdout.write(JSON.stringify({ id, time }, null, 2) + '\n');
}

async function cmdToken(force) {
  const meta = await getTokens(force);
  process.stdout.write(`Tokens OK ✅  ic3 exp=${meta.ic3.expISO}  agg exp=${meta.agg.expISO}\n`);
}

// --- Main --------------------------------------------------------------------
(async () => {
  const [cmd, ...rest] = process.argv.slice(2);
  const flag = (name) => { const i = rest.indexOf(name); return i >= 0 ? rest[i + 1] : null; };
  const has = (name) => rest.includes(name);
  const positional = rest.filter((a, i) => !a.startsWith('--') && rest[i - 1] !== '--top');
  try {
    switch (cmd) {
      case 'enumerate': await cmdEnumerate(parseInt(flag('--top') || '40', 10), has('--unread')); break;
      case 'messages': await cmdMessages(positional[0], parseInt(flag('--top') || '20', 10)); break;
      case 'send': {
        const convId = positional[0];
        const convIdIdx = rest.findIndex(a => a === convId);
        const msgText = rest.slice(convIdIdx + 1).filter(a => a !== '--html').join(' ');
        await cmdSend(convId, msgText, has('--html')); break;
      }
      case 'mark-unread': await cmdMarkUnread(flag('--conversation-id'), flag('--message-id')); break;
      case 'token': await cmdToken(has('--force')); break;
      default:
        process.stderr.write('Usage: teams-chat.js <enumerate [--unread]|messages <convId>|send <convId> <message> [--html]|mark-unread --conversation-id <id> --message-id <id>|token> [--top N] [--force]\n');
        process.exit(1);
    }
  } catch (e) {
    process.stderr.write('ERROR: ' + (e && e.message || e) + '\n');
    process.exit(2);
  }
})();
