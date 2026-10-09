// Read / mark-read a Slack workspace via the Slack Web API.
//
// Auth: set SLACK_BOT_TOKEN to a Slack API token, SLACK_COOKIE_D to the companion `d` session cookie
// (required when the token type needs it — e.g. a browser `xoxc` token is invalid_auth without it; a
// bot/app token that doesn't need a cookie can set it to any non-empty value), and SLACK_TEAM_ID to the
// workspace's team id. No npm deps of its own — uses Node's built-in fetch (Node 18+) — though on
// invalid_auth it shells out to the sibling `slack-token-refresh.js` to self-heal (see `call()`), and
// THAT script needs `playwright-core`; steady-state calls never take that path and never load it.
//
// Auth glance:   node slack.js --check
//                (calls auth.test; prints the signed-in user/team; non-zero exit on auth failure)
// List unread:   node slack.js --list-unread [--top=50] [--json]
//                (unread DMs + group DMs + @-mentions + channel unreads + unread subscribed-thread
//                 replies, newest-first; muting a channel/DM skips its plain unreads and @-mentions, but
//                 a thread you're actually a participant in (replied to, or mentioned in) still surfaces
//                 even when its channel is muted — same distinction Slack's own Threads panel makes,
//                 since muting silences ambient channel noise but doesn't unsubscribe you from a thread
//                 you're personally on; --json emits a structured array. Message text includes
//                 bot attachment/block content when `text` is empty (GitHub app posts etc.). Each item also carries `unread`:
//                 the FULL span of unread messages since the last read cursor, oldest-first, each with
//                 from/received/text — so a conversation that accreted several distinct asks between
//                 reads exposes every one, not only its newest message. Each item also carries
//                 `knownContact`: true when the DM/group DM/thread's history already has a message from
//                 you — channel items always get false, since a per-sender lookup there is too costly)
// Show one:      node slack.js --show --channel=<C> --ts=<ts> [--thread-ts=<tts>] [--json]
//                (the message text + a chat.getPermalink url; pass --thread-ts for a threaded reply)
// History:       node slack.js --history --channel=<C> [--thread-ts=<tts>] [--limit=50] [--json]
//                (recent messages, oldest first — a whole thread when --thread-ts is given, else the
//                 channel/DM/group-DM timeline; use this before deciding a move so you see anything
//                 posted after the one message a captured item happens to link to, including your own
//                 follow-up)
// React:         node slack.js --react --channel=<C> --ts=<ts> --emoji=<name>
//                (reactions.add; emoji name without colons, e.g. "thumbsup", "+1", "tada")
// Mark read:     node slack.js --mark --channel=<C> --ts=<ts> [--thread-ts=<tts>]
//                (conversations.mark up to <ts>, or subscriptions.thread.mark when --thread-ts is given —
//                 the conversation/thread's "gone"; reversible, never deletes)
// Send (REAL):   node slack.js --send --channel=<C> --body-file=<file> [--thread-ts=<tts> [--broadcast]] [--file=<path>[,<path>...]]
//                (chat.postMessage of the reviewed body in <file> as the signed-in user, then prints the
//                 sent message's permalink. The body is Slack mrkdwn — a link is `<url|anchor text>`. This
//                 is the one write that reaches another person: human-in-the-loop only, run solely on
//                 Russ's explicit per-message say-so after he's reviewed this exact body this turn, and
//                 gated by the writing-review receipt on <file>. See "Sending" in the skill's SKILL.md.
//                 With --file=<path>, uploads that local file instead of posting plain text: files.
//                 getUploadURLExternal, POST the bytes to the returned URL, then files.completeUploadExternal
//                 with the --body-file text as initial_comment — same gate, same human-in-the-loop bar.
//                 A comma-separated --file=a.png,b.png uploads each file's bytes, then one
//                 completeUploadExternal call posts the body and every file as a single message.
//                 --broadcast (thread replies only, not with --file) ticks "Also send to <channel>":
//                 chat.postMessage with reply_broadcast=true.)
// List drafts:   node slack.js --list-drafts [--channel=<C>] [--thread-ts=<tts>] [--json]
//                (drafts.list, paginated, keeping only active drafts - not sent, not deleted - and, with
//                 --channel, only those addressed to that conversation (plus --thread-ts for one thread).
//                 This is the server's own draft list, the one Russell's Slack apps sync from, so it is
//                 the proof a staged draft actually saved; a browser reload can restore a local-only copy)
// Create draft:  node slack.js --create-draft --channel=<C> --body-file=<file> [--thread-ts=<tts> [--broadcast]] [--replace]
//                (drafts.create of the reviewed body as an unsent draft in that conversation - it reaches
//                 no one, and shows under Drafts & sent in Russell's own Slack for him to review and send.
//                 Links written as Slack mrkdwn `<url|anchor text>` become real links. Gated by the
//                 writing-review receipt on <file>, the same as --send.
//                 --broadcast stages the thread reply with "Also send to <channel>" ticked. Slack allows
//                 one draft per thread, so a second create fails with attached_draft_exists unless
//                 --replace deletes the thread's existing draft first)
// Delete draft:  node slack.js --delete-draft=<draft id>
//                (drafts.delete of one unsent draft, found via --list-drafts; it never reaches anyone)
// Find DM:       node slack.js --find-dm=<name substring> [--json]
//                (users.list matched against real name, then conversations.open per match to resolve the
//                 1:1 DM channel id — conversations.open only opens/returns the existing DM, it never
//                 sends anything; use the returned channel id with --history to read a named contact's
//                 DM before --list-unread would show anything, e.g. a reply Russell already read)
// Open DM:       node slack.js --open-dm=<user ID> [--json]
//                (conversations.open for a Slack user ID directly, for when you already have the ID and
//                 --find-dm's name-substring search would be the wrong tool — e.g. resolving 47 known
//                 member IDs from a roster rather than searching by name one at a time)
// Find by domain: node slack.js --find-by-domain=<email domain> [--json]
//                (users.list filtered on profile.email ending in @<domain> — surfaces an existing
//                 member who already works at a company, a warm path into a cold outreach target
//                 instead of a generic company inbox)

// TOKEN/COOKIE are `let`, not `const` — refreshCreds() below reassigns them in place after a successful
// auto-refresh, so every call after the first retry (in THIS process) uses the fresh pair too.
let TOKEN = process.env.SLACK_BOT_TOKEN;
let COOKIE = process.env.SLACK_COOKIE_D;
const TEAM = process.env.SLACK_TEAM_ID;

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);

function requireAuth() {
  if (!TOKEN || !COOKIE) {
    throw new Error('Not signed in: set SLACK_BOT_TOKEN and SLACK_COOKIE_D (the `d` session cookie) in the environment.');
  }
}

// The xoxc token + `d` cookie pair rotates periodically (usually just the cookie, not the token) and
// every call then fails with invalid_auth until someone re-derives a fresh pair. Rather than surfacing
// that to every caller, ONE retry is built into the call layer itself: on invalid_auth, re-derive a fresh
// pair from the persistent, already-logged-in browser-chauffeur browser (the sibling `slack-token-refresh.js`
// - a plain, deterministic script, no AI involved) and retry once. This fixes every command that funnels
// through `call()` - list-unread, show, mark, react, send, everything - not just whichever one happened
// to trip over the rotation first. It can only ever READ a session that's already authenticated there; if
// that browser's OWN Slack session has also logged out, there's nothing to re-derive, and the error below
// says so plainly rather than retrying forever.
function refreshCreds() {
  if (!TEAM) return false;
  const { execFileSync } = require('child_process');
  const path = require('path');
  try {
    const out = execFileSync(
      'node', [path.join(__dirname, 'slack-token-refresh.js'), `--team=${TEAM}`],
      { encoding: 'utf8', timeout: 60000, stdio: ['ignore', 'pipe', 'pipe'] });
    const result = JSON.parse(out.trim());
    if (result.status !== 'ok' || !result.token || !result.cookie) return false;
    TOKEN = result.token;
    COOKIE = result.cookie;
    persistToRegistry({ SLACK_BOT_TOKEN: TOKEN, SLACK_COOKIE_D: COOKIE });
    return true;
  } catch {
    return false;  // a crashed/timed-out refresh attempt is still just a failed one
  }
}

// Push the fresh pair into HKCU\Environment (Windows User-scope env vars) so the NEXT process to run
// this script - a fresh `pythonw` the drainer's scheduled task launches every 5 minutes, a worker
// session, a manual run - picks it up too, without waiting for a human to open a new terminal. Built-in
// `reg.exe`, not an npm package, so this script's own "no npm deps" story stays true even after this.
// Windows-only by design (this only ever runs on Russell's personal machine); a no-op elsewhere. Never
// logs either value - `stdio: 'ignore'` on the child, nothing printed here either way.
function persistToRegistry(values) {
  if (process.platform !== 'win32') return;
  const { execFileSync } = require('child_process');
  for (const [name, value] of Object.entries(values)) {
    try {
      execFileSync('reg', ['add', 'HKCU\\Environment', '/v', name, '/d', value, '/f'], { stdio: 'ignore' });
    } catch { /* best-effort - the in-process value above still applies for the rest of this run */ }
  }
}

// One Slack Web API call. The token goes in the Authorization header; the companion `d` cookie rides
// along in the Cookie header (most api/ paths and all client.* paths reject a browser xoxc token alone).
async function call(method, params = {}, _isRetry = false) {
  const body = new URLSearchParams(params).toString();
  const res = await fetch(`https://slack.com/api/${method}`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${TOKEN}`,
      'Cookie': `d=${COOKIE}`,
      'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
    },
    body,
  });
  const json = await res.json();
  if (!json.ok) {
    if (json.error === 'invalid_auth' && !_isRetry) {
      if (refreshCreds()) return call(method, params, true);
      // The exact wording here is a load-bearing signal - the drainer's slack-adapter.py greps for
      // "auto-refresh did not fix it" to tell "already tried and failed, escalate to Russell now" apart
      // from an ordinary transient auth blip. Keep this string and that match in sync if either changes.
      throw new Error(`${method} failed: invalid_auth - auto-refresh did not fix it (check the `
        + `persistent browser's own Slack sign-in, or SLACK_TEAM_ID if unset)`);
    }
    throw new Error(`${method} failed: ${json.error || 'unknown'}`);
  }
  return json;
}

const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
const tsToIso = (ts) => new Date(parseFloat(ts) * 1000).toISOString();
const newer = (a, b) => parseFloat(a) > parseFloat(b || '0');

// Slack drops an image/file's metadata into a `files` array on the message object — a screenshot or
// document attached alongside (or instead of) text. Surface just enough that a caller knows one exists
// and where to look: name, a mime/file type, and the permalink a human can open. Never fetch the bytes
// here — that's a deliberate non-goal (see the handoff this closes).
const fileMeta = (files) => (files || []).map(f => ({
  name: f.name || f.title || '(unnamed)',
  mimetype: f.mimetype || f.filetype || '',
  permalink: f.permalink || '',
}));

// ---- name/info resolution (cached within one run) ----
const userCache = new Map();
async function userName(id) {
  if (!id) return '?';
  if (userCache.has(id)) return userCache.get(id);
  let name = id;
  try {
    const r = await call('users.info', { user: id });
    const u = r.user || {};
    name = u.real_name || (u.profile && u.profile.real_name) || u.name || id;
  } catch { /* fall back to the id */ }
  userCache.set(id, name);
  return name;
}

const convCache = new Map();
async function convInfo(channel) {
  if (convCache.has(channel)) return convCache.get(channel);
  let info = {};
  try { info = (await call('conversations.info', { channel })).channel || {}; } catch { /* ignore */ }
  convCache.set(channel, info);
  return info;
}

// The set of channel/DM ids the user has muted. Slack stores this per-conversation under
// all_notifications_prefs.channels[id].muted; the legacy comma-string `muted_channels` pref is empty on
// modern accounts. A muted conversation should never surface as a drainer item — muting IS the stop.
async function mutedSet() {
  try {
    const prefs = (await call('users.prefs.get')).prefs || {};
    let anp = prefs.all_notifications_prefs;
    if (typeof anp === 'string') anp = JSON.parse(anp);
    const chans = (anp && anp.channels) || {};
    const muted = new Set(Object.keys(chans).filter(id => chans[id] && chans[id].muted === true));
    // Fold in the legacy pref too, if present.
    for (const id of String(prefs.muted_channels || '').split(',').filter(Boolean)) muted.add(id);
    return muted;
  } catch {
    return new Set();  // fail-open: better to surface than to silently drop everything
  }
}

// Resolve <@U...> mentions in message text to readable @Name for previews.
async function renderText(text) {
  let out = text || '';
  const ids = [...new Set([...out.matchAll(/<@([A-Z0-9]+)>/g)].map(m => m[1]))];
  for (const id of ids) out = out.replaceAll(`<@${id}>`, `@${await userName(id)}`);
  return clean(out);
}

// Bot/app posts (the GitHub app, CI, forms) usually carry an empty `text` and put the readable content
// in `attachments` (pretext/title/text, else the plain-text fallback) and `blocks` (section/context
// text). Flatten those to plain lines so a caller sees what the post says.
const blockText = (t) => (t && (t.text || '')) || '';
function attachmentLines(m) {
  return (m.attachments || []).map(a => {
    const parts = [a.pretext, a.title, a.text].map(clean).filter(Boolean);
    if (!parts.length) parts.push(clean(a.fallback));
    return parts.filter(Boolean).join(' - ');
  }).filter(Boolean);
}
function blockLines(m) {
  const lines = [];
  for (const b of m.blocks || []) {
    if (b.type === 'section') {
      if (b.text) lines.push(blockText(b.text));
      for (const f of b.fields || []) lines.push(blockText(f));
    } else if (b.type === 'context') {
      const els = (b.elements || []).map(blockText).filter(Boolean);
      if (els.length) lines.push(els.join(' '));
    } else if (b.type === 'header') {
      if (b.text) lines.push(blockText(b.text));
    }
  }
  return lines.map(clean).filter(Boolean);
}

// The readable text of a message: its `text`, plus attachment content, and block content when `text` is
// empty (on a normal message the blocks just restate `text`, so they're only a fallback). Slack's
// `<url|label>` link markup is reduced to the label.
async function messageText(m) {
  const base = await renderText(m.text);
  const extra = attachmentLines(m);
  if (!base) extra.push(...blockLines(m));
  const joined = [base, ...extra.filter(l => l !== base)].filter(Boolean).join('\n');
  return joined.replace(/<([^|>]+)\|([^>]+)>/g, '$2').replace(/<(https?:[^>]+)>/g, '$1');
}

// Build a short joined preview from up to 5 messages (oldest-first), rendered and trimmed.
async function previewText(msgs) {
  return clean((await Promise.all(msgs.slice(0, 5).reverse().map(messageText))).join(' / ')).slice(0, 600);
}

// Build the full unread span (oldest-first) for an item body: every unread message kept whole, with its
// author and time. Where `preview` joins and truncates a handful of messages into one snippet, this keeps
// each message separate so a conversation that accreted several distinct asks between reads exposes every
// one. msgs arrive newest-first (as unreadMessages sorts them); the body reads oldest-first.
async function unreadSpan(msgs) {
  const out = [];
  for (const m of msgs.slice().reverse()) {
    const entry = { ts: m.ts, from: await userName(m.user), received: tsToIso(m.ts), text: await messageText(m) };
    if (m.files && m.files.length) entry.files = fileMeta(m.files);
    out.push(entry);
  }
  return out;
}

// Unread top-level messages in one conversation: recent history filtered to ts > last_read, excluding our
// own and pure system join/leave noise. (Passing oldest=last_read is unreliable — some conversations
// carry a last_read value Slack rejects with invalid_ts_oldest — so we filter client-side.)
async function unreadMessages(channel, lastRead, myId, limit = 30) {
  const r = await call('conversations.history', { channel, limit: String(limit) });
  return (r.messages || [])
    .filter(m => m.ts && newer(m.ts, lastRead))
    .filter(m => m.user && m.user !== myId)
    .filter(m => !m.subtype || m.subtype === 'thread_broadcast' || m.subtype === 'me_message')
    .sort((a, b) => parseFloat(b.ts) - parseFloat(a.ts));
}

// Whether Russell has ever posted in this conversation/thread before — a signal the junk gate
// downstream uses, since a message from a known contact is never junk (a first-ever message from
// a stranger still can be). Checked with its own history fetch, separate from the unread window,
// so it still finds an old exchange the 30-message unread window wouldn't reach. Fails closed
// (false = treat as a stranger) on any API error, since that's already the safe default here.
async function hasMeMessage(channel, me, threadTs) {
  try {
    const r = threadTs
      ? await call('conversations.replies', { channel, ts: threadTs, limit: '100' })
      : await call('conversations.history', { channel, limit: '100' });
    return (r.messages || []).some(m => m.user === me);
  } catch {
    return false;
  }
}

async function listUnread() {
  const me = (await call('auth.test')).user_id;
  const muted = await mutedSet();
  const counts = await call('client.counts');
  const items = [];

  // DMs (im) and group DMs (mpim): one item per conversation, keyed to the latest unread message.
  for (const kind of ['ims', 'mpims']) {
    for (const c of counts[kind] || []) {
      if (!c.has_unreads || muted.has(c.id)) continue;
      const msgs = await unreadMessages(c.id, c.last_read, me);
      if (!msgs.length) continue;
      const latest = msgs[0];
      const info = await convInfo(c.id);
      const from = await userName(latest.user);
      const subject = kind === 'ims' ? `DM from ${from}` : `Group DM (${info.name || 'group'})`;
      const channelName = kind === 'ims' ? `@${from}` : (info.name ? `mpdm:${info.name}` : 'group DM');
      items.push({
        id: `${c.id}:${latest.ts}`, channel: c.id, channelType: kind === 'ims' ? 'im' : 'mpim',
        ts: latest.ts, threadTs: '', from, fromId: latest.user, subject, channelName,
        received: tsToIso(latest.ts), isRead: false, unreadCount: msgs.length,
        preview: await previewText(msgs), unread: await unreadSpan(msgs),
        knownContact: await hasMeMessage(c.id, me),
      });
    }
  }

  // Channels with unread activity: an @-mention message (since last_read) that names me becomes one item
  // per mention; otherwise — including when `mention_count` says there's a mention but none turns up in
  // the fetch window, e.g. a stale badge left over from a mention that's actually older than last_read —
  // fall back to one plain "Unread in #channel" item keyed to the latest unread message. Merging these
  // into a single pass (rather than skipping the plain-unread pass whenever mention_count >= 1) means a
  // channel's genuinely new message is never silently dropped just because Slack's mention badge hasn't
  // caught up to the read cursor.
  for (const c of counts.channels || []) {
    if (muted.has(c.id) || (!c.has_unreads && !(c.mention_count > 0))) continue;
    const msgs = await unreadMessages(c.id, c.last_read, me);
    if (!msgs.length) continue;
    const mentions = msgs.filter(m => (m.text || '').includes(`<@${me}>`));
    const info = await convInfo(c.id);
    const chName = info.name ? `#${info.name}` : c.id;
    if (mentions.length) {
      for (const m of mentions) {
        const from = await userName(m.user);
        const rendered = await messageText(m);
        const unreadEntry = { ts: m.ts, from, received: tsToIso(m.ts), text: rendered };
        if (m.files && m.files.length) unreadEntry.files = fileMeta(m.files);
        items.push({
          id: `${c.id}:${m.ts}`, channel: c.id, channelType: 'channel',
          ts: m.ts, threadTs: '', from, fromId: m.user, subject: `@mention in ${chName}`, channelName: chName,
          received: tsToIso(m.ts), isRead: false, unreadCount: 1,
          preview: rendered.slice(0, 600),
          unread: [unreadEntry],
          knownContact: false,
        });
      }
    } else {
      const latest = msgs[0];
      const from = await userName(latest.user);
      items.push({
        id: `${c.id}:${latest.ts}`, channel: c.id, channelType: 'channel',
        ts: latest.ts, threadTs: '', from, fromId: latest.user, subject: `Unread in ${chName}`,
        channelName: chName, received: tsToIso(latest.ts), isRead: false, unreadCount: msgs.length,
        preview: await previewText(msgs), unread: await unreadSpan(msgs),
        knownContact: false,
      });
    }
  }

  // Subscribed threads with unread replies: one item per thread, keyed to the latest unread reply. A
  // thread carries its OWN read cursor (root_msg.last_read) separate from the channel's, so thread
  // replies never appear in conversations.history above — they're enumerated here.
  // Deliberately NOT muted-filtered: `subscriptions.thread.getView` only returns threads you replied to
  // or were mentioned in, so muting the parent channel (ambient noise) doesn't apply — Slack's own
  // Threads panel surfaces these the same way, mute or not.
  try {
    const view = await call('subscriptions.thread.getView', { limit: '50' });
    for (const t of view.threads || []) {
      const root = t.root_msg || {};
      const channel = root.channel;
      if (!channel) continue;
      if (!newer(root.latest_reply, root.last_read)) continue;  // no unread replies
      const unread = (t.unread_replies || [])
        .filter(m => m.ts && newer(m.ts, root.last_read) && m.user && m.user !== me)
        .sort((a, b) => parseFloat(b.ts) - parseFloat(a.ts));
      if (!unread.length) continue;
      const latest = unread[0];
      const info = await convInfo(channel);
      const chName = info.name ? `#${info.name}` : channel;
      const from = await userName(latest.user);
      const mentioned = unread.some(m => (m.text || '').includes(`<@${me}>`));
      const threadTs = root.thread_ts || root.ts;
      items.push({
        id: `${channel}:${latest.ts}`, channel, channelType: 'thread',
        ts: latest.ts, threadTs, from, fromId: latest.user,
        subject: mentioned ? `@mention in thread in ${chName}` : `Thread reply in ${chName}`,
        channelName: chName, received: tsToIso(latest.ts), isRead: false, unreadCount: unread.length,
        preview: await previewText(unread), unread: await unreadSpan(unread),
        knownContact: await hasMeMessage(channel, me, threadTs),
      });
    }
  } catch { /* threads view unavailable — DMs/mentions still enumerate */ }

  items.sort((a, b) => parseFloat(b.ts) - parseFloat(a.ts));
  const top = parseInt(args.top || '50', 10);
  const out = items.slice(0, top);

  if (args.json) { console.log(JSON.stringify(out, null, 2)); return; }
  if (!out.length) { console.log('No unread DMs, mentions, channel messages, or thread replies.'); return; }
  console.log(`${out.length} unread item(s) (newest first):`);
  for (const it of out) {
    console.log(`\n--- ${it.received.slice(0, 16)}  |  ${it.subject}`);
    console.log(`    from: ${it.from}  (${it.channelName})`);
    console.log(`    id:   ${it.id}${it.threadTs ? `  thread:${it.threadTs}` : ''}`);
    console.log(`    text: ${it.preview.slice(0, 160)}`);
  }
}

// Fetch one message — from the thread (conversations.replies) when --thread-ts is given, else the
// channel timeline (conversations.history). A threaded reply is not reliably returned by history.
async function fetchOne(channel, ts, threadTs) {
  if (threadTs && threadTs !== ts) {
    const r = await call('conversations.replies', { channel, ts: threadTs, limit: '100' });
    return (r.messages || []).find(m => m.ts === ts) || null;
  }
  const r = await call('conversations.history',
    { channel, latest: ts, oldest: ts, inclusive: 'true', limit: '1' });
  return (r.messages || [])[0] || null;
}

async function show() {
  if (!args.channel || !args.ts) throw new Error('--show requires --channel and --ts');
  const m = await fetchOne(args.channel, args.ts, args['thread-ts']);
  if (!m) { console.log('Message not found.'); return; }
  const from = await userName(m.user);
  const text = await messageText(m);
  const files = fileMeta(m.files);
  let permalink = '';
  try { permalink = (await call('chat.getPermalink', { channel: args.channel, message_ts: args.ts })).permalink || ''; }
  catch { /* permalink optional */ }
  if (args.json) {
    console.log(JSON.stringify({ channel: args.channel, ts: args.ts, threadTs: args['thread-ts'] || '',
      from, fromId: m.user, received: tsToIso(args.ts), text, permalink,
      ...(files.length ? { files } : {}) }, null, 2));
    return;
  }
  console.log(`From: ${from}`);
  console.log(`When: ${tsToIso(args.ts)}`);
  if (permalink) console.log(`Link: ${permalink}`);
  console.log(`\n${text || '(no text)'}`);
  for (const f of files) console.log(`\n[Attachment: ${f.name} (${f.mimetype})] ${f.permalink}`);
}

// Recent messages, oldest first: a whole thread (conversations.replies) when --thread-ts is given,
// else the channel/DM/group-DM timeline (conversations.history). Unlike --show, this surfaces
// everything around a captured message — including a reply posted after capture, in either direction —
// so a situational check never mistakes one linked message for the whole conversation.
async function history() {
  if (!args.channel) throw new Error('--history requires --channel');
  const limit = String(args.limit || '50');
  let msgs;
  if (args['thread-ts']) {
    const r = await call('conversations.replies', { channel: args.channel, ts: args['thread-ts'], limit });
    msgs = r.messages || [];
  } else {
    const r = await call('conversations.history', { channel: args.channel, limit });
    msgs = (r.messages || []).slice().reverse();
  }
  const out = [];
  for (const m of msgs) {
    const entry = {
      ts: m.ts, threadTs: m.thread_ts || '', replyCount: m.reply_count || 0,
      from: await userName(m.user || m.bot_id), fromId: m.user,
      received: tsToIso(m.ts), text: await messageText(m),
    };
    if (m.files && m.files.length) entry.files = fileMeta(m.files);
    out.push(entry);
  }
  if (args.json) { console.log(JSON.stringify(out, null, 2)); return; }
  if (!out.length) { console.log('No messages.'); return; }
  console.log(`${out.length} message(s), oldest first:`);
  for (const m of out) {
    console.log(`\n--- ${m.received.slice(0, 16)} | ${m.from} (ts=${m.ts}` +
      `${m.threadTs ? `, thread=${m.threadTs}` : ''}${m.replyCount ? `, replies=${m.replyCount}` : ''})`);
    console.log(m.text || '(no text)');
    for (const f of m.files || []) console.log(`[Attachment: ${f.name} (${f.mimetype})] ${f.permalink}`);
  }
}

async function react() {
  if (!args.channel || !args.ts || !args.emoji) throw new Error('--react requires --channel, --ts, and --emoji');
  const name = args.emoji.replace(/^:|:$/g, '');
  await call('reactions.add', { channel: args.channel, timestamp: args.ts, name });
  console.log(`Reacted :${name}: on message ${args.ts} in ${args.channel}.`);
}

// Advance the read cursor to `ts` — subscriptions.thread.mark for a threaded reply, else
// conversations.mark. Shared by --mark and the post-send auto-mark below.
async function markRead(channel, ts, threadTs) {
  if (threadTs) {
    await call('subscriptions.thread.mark', { channel, thread_ts: threadTs, ts, read: '1' });
    return;
  }
  await call('conversations.mark', { channel, ts });
}

async function mark() {
  if (!args.channel || !args.ts) throw new Error('--mark requires --channel and --ts');
  await markRead(args.channel, args.ts, args['thread-ts']);
  if (args['thread-ts']) {
    console.log(`Marked thread ${args['thread-ts']} in ${args.channel} read up to ${args.ts}. Reversible.`);
    return;
  }
  console.log(`Marked ${args.channel} read up to ${args.ts}. Reversible — re-reading the conversation re-surfaces it.`);
}

// Upload one or more local files to Slack via the current (v2) upload flow: for each file, reserve an
// upload URL sized to it and POST the raw bytes there (multipart/form-data — the one call in this script
// that isn't a plain call() since it hits the reserved upload_url, not api/<method>, and carries a file
// body instead of form params). Then ONE files.completeUploadExternal call with every file id is what
// actually posts them into the conversation (with channel_id/initial_comment/thread_ts, same as
// chat.postMessage's params), so the body and all the files land as a single message. Every file is
// read before anything is uploaded, so a bad path fails before Slack sees a byte. Returns the
// completeUploadExternal response's first file entry.
async function uploadFiles(filePaths, channel, initialComment, threadTs) {
  const fs = require('fs');
  const path = require('path');
  const loaded = filePaths.map(filePath => {
    try {
      return { data: fs.readFileSync(filePath), filename: path.basename(filePath) };
    } catch {
      throw new Error(`--send: cannot read --file ${filePath}`);
    }
  });

  const uploaded = [];
  for (const { data, filename } of loaded) {
    const reserved = await call('files.getUploadURLExternal', { filename, length: String(data.length) });
    const form = new FormData();
    form.append('file', new Blob([data]), filename);
    const uploadRes = await fetch(reserved.upload_url, { method: 'POST', body: form });
    if (!uploadRes.ok) throw new Error(`file upload POST failed for ${filename}: HTTP ${uploadRes.status}`);
    uploaded.push({ id: reserved.file_id, title: filename });
  }

  const completeParams = {
    files: JSON.stringify(uploaded),
    channel_id: channel,
  };
  if (initialComment) completeParams.initial_comment = initialComment;
  if (threadTs) completeParams.thread_ts = threadTs;
  const r = await call('files.completeUploadExternal', completeParams);
  return (r.files && r.files[0]) || {};
}

// A freshly-uploaded file's message ts, if Slack's response says where it landed — used only to mark
// the conversation read afterward (best-effort, see below). completeUploadExternal nests share info
// under shares.public/private, keyed by channel id, each an array of share entries with a ts.
function postedTs(fileEntry, channel) {
  const shares = fileEntry.shares || {};
  for (const bucket of [shares.private, shares.public]) {
    const entries = bucket && bucket[channel];
    if (entries && entries.length) return entries[0].ts;
  }
  return null;
}

// REAL SEND — post a reviewed body (optionally with an attached file) to a conversation as the signed-in
// user. This is the only write in this script that reaches another person, so it stays human-in-the-loop:
// run it solely on Russ's explicit per-message instruction to send, after he has reviewed this exact body
// this turn. The body comes from a file (never an inline arg) so the writing-review gate can read and
// receipt it, the same way the mail staging commands take --body-file. The body is Slack mrkdwn:
// chat.postMessage (and a file's initial_comment) render `<url|text>` as a link and `<@U…>` as a mention.
async function send() {
  if (!args.channel) {
    throw new Error('--send requires --channel (a DM/group/channel/conversation id; resolve a person with --find-dm)');
  }
  const text = readBody('--send');
  if (args.broadcast && !(args['thread-ts'] && args['thread-ts'] !== true)) {
    throw new Error('--broadcast only applies to a thread reply (pass --thread-ts)');
  }
  if (args.broadcast && args.file && args.file !== true) {
    throw new Error('--broadcast cannot be combined with --file (file uploads have no also-send-to-channel option)');
  }

  if (args.file && args.file !== true) {
    const filePaths = String(args.file).split(',').map(p => p.trim()).filter(Boolean);
    if (!filePaths.length) throw new Error('--send: --file names no files');
    const fileEntry = await uploadFiles(filePaths, args.channel, text, args['thread-ts']);
    const ts = postedTs(fileEntry, args.channel);
    if (ts) {
      try {
        await markRead(args.channel, ts, args['thread-ts']);
      } catch (e) {
        console.error(`Warning: sent, but failed to mark read: ${e.message}`);
      }
    }
    const permalink = fileEntry.permalink || '';
    const noun = filePaths.length === 1 ? 'file' : `${filePaths.length} files`;
    console.log(`Sent ${noun} to ${args.channel}.${permalink ? ` Link: ${permalink}` : ''}`);
    return;
  }

  const params = { channel: args.channel, text };
  if (args['thread-ts']) params.thread_ts = args['thread-ts'];
  if (args.broadcast) params.reply_broadcast = 'true';
  const r = await call('chat.postMessage', params);
  // A post doesn't advance the read cursor the way the Slack client does, so the conversation
  // stays bold forever with our own reply as the only "unread" message. Mark it ourselves —
  // best-effort: the send already succeeded, so a mark failure is a warning, never a retry/fail.
  try {
    await markRead(r.channel, r.ts, params.thread_ts);
  } catch (e) {
    console.error(`Warning: sent, but failed to mark read: ${e.message}`);
  }
  let permalink = '';
  try {
    permalink = (await call('chat.getPermalink', { channel: r.channel, message_ts: r.ts })).permalink || '';
  } catch { /* permalink optional — the send already succeeded */ }
  console.log(`Sent to ${r.channel} at ts ${r.ts}.${permalink ? ` Link: ${permalink}` : ''}`);
}

// Read a --body-file the same way --send does: normalized newlines, trimmed, never empty.
function readBody(verb) {
  const bodyFile = args['body-file'];
  if (!bodyFile || bodyFile === true) {
    throw new Error(`${verb} requires --body-file (the reviewed message body; the writing-review gate reads it)`);
  }
  let text;
  try {
    text = require('fs').readFileSync(bodyFile, 'utf8').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
  } catch {
    throw new Error(`${verb}: cannot read --body-file ${bodyFile}`);
  }
  if (!text) throw new Error(`${verb}: --body-file ${bodyFile} is empty`);
  return text;
}

// A draft's body is rich_text blocks, not mrkdwn: turn the body into one rich_text section, splitting
// out each `<url|anchor>` (or bare `<url>`) into a link element so the link survives as a link.
function richTextBlocks(text) {
  const elements = [];
  const re = /<(https?:[^|>]+)(?:\|([^>]+))?>/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) elements.push({ type: 'text', text: text.slice(last, m.index) });
    elements.push(m[2] ? { type: 'link', url: m[1], text: m[2] } : { type: 'link', url: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) elements.push({ type: 'text', text: text.slice(last) });
  return [{ type: 'rich_text', elements: [{ type: 'rich_text_section', elements }] }];
}

// The plain text of a draft's rich_text blocks, for listing.
function draftText(blocks) {
  const out = [];
  const walk = (els) => {
    for (const e of els || []) {
      if (e.type === 'text') out.push(e.text || '');
      else if (e.type === 'link') out.push(e.text || e.url || '');
      else if (e.type === 'user') out.push(`<@${e.user_id}>`);
      else if (e.type === 'channel') out.push(`<#${e.channel_id}>`);
      else if (e.type === 'emoji') out.push(`:${e.name}:`);
      else if (e.elements) walk(e.elements);
    }
  };
  for (const b of blocks || []) walk(b.elements);
  return out.join('');
}

async function listDrafts() {
  const drafts = await activeDrafts();
  const channel = args.channel && args.channel !== true ? args.channel : '';
  const threadTs = args['thread-ts'] && args['thread-ts'] !== true ? args['thread-ts'] : '';
  const out = drafts
    .filter(d => !channel || (d.destinations || []).some(x =>
      x.channel_id === channel && (!threadTs || x.thread_ts === threadTs)))
    .map(d => ({
      id: d.id,
      destinations: (d.destinations || []).map(x => ({ channel: x.channel_id, threadTs: x.thread_ts || '', broadcast: !!x.broadcast })),
      updated: d.last_updated_ts ? tsToIso(d.last_updated_ts) : '',
      text: draftText(d.blocks),
    }));
  if (args.json) { console.log(JSON.stringify(out, null, 2)); return; }
  if (!out.length) { console.log(channel ? `No active draft in ${channel}${threadTs ? ` thread ${threadTs}` : ''}.` : 'No active drafts.'); return; }
  for (const d of out) {
    const dest = d.destinations.map(x => x.channel + (x.threadTs ? ` thread:${x.threadTs}` : '') + (x.broadcast ? ' +channel' : '')).join(', ');
    console.log(`\n--- ${d.id}  |  ${dest}  |  updated ${d.updated.slice(0, 16)}`);
    console.log(d.text || '(no text)');
  }
}

// Every active draft from drafts.list, paginated.
async function activeDrafts() {
  const drafts = [];
  let cursor = '';
  do {
    const r = await call('drafts.list', cursor ? { limit: '100', cursor } : { limit: '100' });
    drafts.push(...(r.drafts || []));
    cursor = (r.response_metadata && r.response_metadata.next_cursor) || '';
  } while (cursor);
  return drafts.filter(d => !d.is_sent && !d.is_deleted);
}

// drafts.delete wants the draft's last_updated_ts plus one second as client_last_updated_ts: the exact
// value is rejected as draft_has_conflict and omitting it as invalid_arguments.
async function deleteDraftById(draft) {
  const clientTs = (parseFloat(draft.last_updated_ts || '0') + 1).toFixed(6);
  await call('drafts.delete', { draft_id: draft.id, client_last_updated_ts: clientTs });
}

async function deleteDraft() {
  const id = args['delete-draft'];
  if (!id || id === true) throw new Error('--delete-draft requires a draft id (see --list-drafts)');
  const draft = (await activeDrafts()).find(d => d.id === id);
  if (!draft) throw new Error(`--delete-draft: no active draft ${id}`);
  await deleteDraftById(draft);
  console.log(`Draft ${id} deleted. It was never sent.`);
}

// Create an unsent draft on the server - reaches no one; Russell sends it himself from his own Slack.
// --broadcast ticks the thread reply's "Also send to <channel>" box; --replace swaps out the thread's
// existing draft (Slack allows one per thread) instead of failing with attached_draft_exists.
async function createDraft() {
  if (!args.channel || args.channel === true) throw new Error('--create-draft requires --channel');
  const text = readBody('--create-draft');
  const destination = { channel_id: args.channel };
  if (args['thread-ts'] && args['thread-ts'] !== true) destination.thread_ts = args['thread-ts'];
  if (args.broadcast) {
    if (!destination.thread_ts) throw new Error('--broadcast only applies to a thread reply (pass --thread-ts)');
    destination.broadcast = true;
  }
  const create = () => call('drafts.create', {
    client_msg_id: require('crypto').randomUUID(),
    blocks: JSON.stringify(richTextBlocks(text)),
    file_ids: '[]',
    destinations: JSON.stringify([destination]),
    is_from_composer: 'false',
  });
  let r;
  try {
    r = await create();
  } catch (e) {
    if (!args.replace || !e.message.includes('attached_draft_exists')) throw e;
    const stale = (await activeDrafts()).filter(d => (d.destinations || []).some(x =>
      x.channel_id === destination.channel_id && (x.thread_ts || '') === (destination.thread_ts || '')));
    for (const d of stale) await deleteDraftById(d);
    r = await create();
  }
  const id = (r.draft && r.draft.id) || '';
  console.log(`Draft ${id} saved in ${args.channel}${destination.thread_ts ? ` thread ${destination.thread_ts}` : ''}${destination.broadcast ? ' (also sent to channel)' : ''}. Not sent.`);
}

async function check() {
  const r = await call('auth.test');
  console.log(`Signed in as ${r.user} (${r.team}, team ${r.team_id}). user_id ${r.user_id}.`);
}

async function findDm() {
  const query = String(args['find-dm'] || '').toLowerCase();
  if (!query) throw new Error('--find-dm requires a name, e.g. --find-dm="Jane Doe"');
  const matches = [];
  let cursor = '';
  do {
    const r = await call('users.list', { limit: '200', cursor });
    for (const u of r.members || []) {
      if (u.deleted || u.is_bot || u.id === 'USLACKBOT') continue;
      const name = u.real_name || (u.profile && u.profile.real_name) || u.name || '';
      if (name.toLowerCase().includes(query)) matches.push({ id: u.id, name });
    }
    cursor = (r.response_metadata && r.response_metadata.next_cursor) || '';
  } while (cursor);
  const out = [];
  for (const u of matches) {
    const r = await call('conversations.open', { users: u.id });
    out.push({ userId: u.id, name: u.name, channel: (r.channel && r.channel.id) || '' });
  }
  if (args.json) { console.log(JSON.stringify(out, null, 2)); return; }
  if (!out.length) { console.log(`No user matching "${args['find-dm']}".`); return; }
  for (const o of out) console.log(`${o.name} (${o.userId}) -> DM channel ${o.channel}`);
}

// Open (or find the existing) 1:1 DM with a known Slack user ID — the ID-based counterpart to --find-dm's
// name-substring search, for when a batch of member IDs is already in hand (e.g. from a roster) and
// searching users.list by name one at a time would be the wrong tool.
async function openDm() {
  const userId = args['open-dm'];
  if (!userId || userId === true) {
    throw new Error('--open-dm requires a Slack user ID, e.g. --open-dm=U0123456');
  }
  const r = await call('conversations.open', { users: userId });
  const channel = (r.channel && r.channel.id) || '';
  if (args.json) { console.log(JSON.stringify({ userId, channel }, null, 2)); return; }
  console.log(`DM channel with ${userId}: ${channel}`);
}

async function findByDomain() {
  const domain = String(args['find-by-domain'] || '').toLowerCase().replace(/^@/, '');
  if (!domain) throw new Error('--find-by-domain requires an email domain, e.g. --find-by-domain=opentext.com');
  const matches = [];
  let cursor = '';
  do {
    const r = await call('users.list', { limit: '200', cursor });
    for (const u of r.members || []) {
      if (u.deleted || u.is_bot || u.id === 'USLACKBOT') continue;
      const email = ((u.profile && u.profile.email) || '').toLowerCase();
      if (email.endsWith(`@${domain}`)) {
        const name = u.real_name || (u.profile && u.profile.real_name) || u.name || '';
        matches.push({ id: u.id, name, email, title: (u.profile && u.profile.title) || '' });
      }
    }
    cursor = (r.response_metadata && r.response_metadata.next_cursor) || '';
  } while (cursor);
  if (args.json) { console.log(JSON.stringify(matches, null, 2)); return; }
  if (!matches.length) { console.log(`No member with an @${domain} email.`); return; }
  for (const m of matches) console.log(`${m.name} (${m.id}) <${m.email}>${m.title ? ' - ' + m.title : ''}`);
}

(async () => {
  requireAuth();
  if (args.check) return await check();
  if (args['list-unread']) return await listUnread();
  if (args.show) return await show();
  if (args.history) return await history();
  if (args.react) return await react();
  if (args.mark) return await mark();
  if (args.send) return await send();
  if (args['list-drafts']) return await listDrafts();
  if (args['create-draft']) return await createDraft();
  if (args['delete-draft']) return await deleteDraft();
  if (args['find-dm']) return await findDm();
  if (args['open-dm']) return await openDm();
  if (args['find-by-domain']) return await findByDomain();
  throw new Error('Specify --check, --list-unread, --show, --history, --react, --mark, --send, --list-drafts, --create-draft, --delete-draft, --find-dm, --open-dm, or --find-by-domain');
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
