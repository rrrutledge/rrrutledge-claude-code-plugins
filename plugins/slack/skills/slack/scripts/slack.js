// Read / mark-read a Slack workspace via the Slack Web API.
//
// Auth: set SLACK_BOT_TOKEN to a Slack API token, SLACK_COOKIE_D to the companion `d` session cookie
// (required when the token type needs it — e.g. a browser `xoxc` token is invalid_auth without it; a
// bot/app token that doesn't need a cookie can set it to any non-empty value), and SLACK_TEAM_ID to the
// workspace's team id. No npm deps — uses Node's built-in fetch (Node 18+).
//
// Auth glance:   node slack.js --check
//                (calls auth.test; prints the signed-in user/team; non-zero exit on auth failure)
// List unread:   node slack.js --list-unread [--top=50] [--json]
//                (unread DMs + group DMs + @-mentions + channel unreads + unread subscribed-thread
//                 replies, newest-first; muting a channel/DM skips its plain unreads and @-mentions, but
//                 a thread you're actually a participant in (replied to, or mentioned in) still surfaces
//                 even when its channel is muted — same distinction Slack's own Threads panel makes,
//                 since muting silences ambient channel noise but doesn't unsubscribe you from a thread
//                 you're personally on; --json emits a structured array. Each item also carries `unread`:
//                 the FULL span of unread messages since the last read cursor, oldest-first, each with
//                 from/received/text — so a conversation that accreted several distinct asks between
//                 reads exposes every one, not only its newest message)
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
// Send (REAL):   node slack.js --send --channel=<C> --body-file=<file> [--thread-ts=<tts>]
//                (chat.postMessage of the reviewed body in <file> as the signed-in user, then prints the
//                 sent message's permalink. The body is Slack mrkdwn — a link is `<url|anchor text>`. This
//                 is the one write that reaches another person: human-in-the-loop only, run solely on
//                 Russ's explicit per-message say-so after he's reviewed this exact body this turn, and
//                 gated by the writing-review receipt on <file>. See "Sending" in the skill's SKILL.md.)
// Find DM:       node slack.js --find-dm=<name substring> [--json]
//                (users.list matched against real name, then conversations.open per match to resolve the
//                 1:1 DM channel id — conversations.open only opens/returns the existing DM, it never
//                 sends anything; use the returned channel id with --history to read a named contact's
//                 DM before --list-unread would show anything, e.g. a reply Russell already read)
// Find by domain: node slack.js --find-by-domain=<email domain> [--json]
//                (users.list filtered on profile.email ending in @<domain> — surfaces an existing
//                 member who already works at a company, a warm path into a cold outreach target
//                 instead of a generic company inbox)

const TOKEN = process.env.SLACK_BOT_TOKEN;
const COOKIE = process.env.SLACK_COOKIE_D;
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

// One Slack Web API call. The token goes in the Authorization header; the companion `d` cookie rides
// along in the Cookie header (most api/ paths and all client.* paths reject a browser xoxc token alone).
async function call(method, params = {}) {
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
  if (!json.ok) throw new Error(`${method} failed: ${json.error || 'unknown'}`);
  return json;
}

const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
const tsToIso = (ts) => new Date(parseFloat(ts) * 1000).toISOString();
const newer = (a, b) => parseFloat(a) > parseFloat(b || '0');

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

// Build a short joined preview from up to 5 messages (oldest-first), rendered and trimmed.
async function previewText(msgs) {
  return clean((await Promise.all(msgs.slice(0, 5).reverse().map(m => renderText(m.text)))).join(' / ')).slice(0, 600);
}

// Build the full unread span (oldest-first) for an item body: every unread message kept whole, with its
// author and time. Where `preview` joins and truncates a handful of messages into one snippet, this keeps
// each message separate so a conversation that accreted several distinct asks between reads exposes every
// one. msgs arrive newest-first (as unreadMessages sorts them); the body reads oldest-first.
async function unreadSpan(msgs) {
  const out = [];
  for (const m of msgs.slice().reverse()) {
    out.push({ ts: m.ts, from: await userName(m.user), received: tsToIso(m.ts), text: await renderText(m.text) });
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
        const rendered = await renderText(m.text);
        items.push({
          id: `${c.id}:${m.ts}`, channel: c.id, channelType: 'channel',
          ts: m.ts, threadTs: '', from, fromId: m.user, subject: `@mention in ${chName}`, channelName: chName,
          received: tsToIso(m.ts), isRead: false, unreadCount: 1,
          preview: rendered.slice(0, 600),
          unread: [{ ts: m.ts, from, received: tsToIso(m.ts), text: rendered }],
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
      items.push({
        id: `${channel}:${latest.ts}`, channel, channelType: 'thread',
        ts: latest.ts, threadTs: root.thread_ts || root.ts, from, fromId: latest.user,
        subject: mentioned ? `@mention in thread in ${chName}` : `Thread reply in ${chName}`,
        channelName: chName, received: tsToIso(latest.ts), isRead: false, unreadCount: unread.length,
        preview: await previewText(unread), unread: await unreadSpan(unread),
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
  const text = await renderText(m.text);
  let permalink = '';
  try { permalink = (await call('chat.getPermalink', { channel: args.channel, message_ts: args.ts })).permalink || ''; }
  catch { /* permalink optional */ }
  if (args.json) {
    console.log(JSON.stringify({ channel: args.channel, ts: args.ts, threadTs: args['thread-ts'] || '',
      from, fromId: m.user, received: tsToIso(args.ts), text, permalink }, null, 2));
    return;
  }
  console.log(`From: ${from}`);
  console.log(`When: ${tsToIso(args.ts)}`);
  if (permalink) console.log(`Link: ${permalink}`);
  console.log(`\n${text || '(no text)'}`);
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
    out.push({
      ts: m.ts, threadTs: m.thread_ts || '', replyCount: m.reply_count || 0,
      from: await userName(m.user), fromId: m.user,
      received: tsToIso(m.ts), text: await renderText(m.text),
    });
  }
  if (args.json) { console.log(JSON.stringify(out, null, 2)); return; }
  if (!out.length) { console.log('No messages.'); return; }
  console.log(`${out.length} message(s), oldest first:`);
  for (const m of out) {
    console.log(`\n--- ${m.received.slice(0, 16)} | ${m.from} (ts=${m.ts}` +
      `${m.threadTs ? `, thread=${m.threadTs}` : ''}${m.replyCount ? `, replies=${m.replyCount}` : ''})`);
    console.log(m.text || '(no text)');
  }
}

async function react() {
  if (!args.channel || !args.ts || !args.emoji) throw new Error('--react requires --channel, --ts, and --emoji');
  const name = args.emoji.replace(/^:|:$/g, '');
  await call('reactions.add', { channel: args.channel, timestamp: args.ts, name });
  console.log(`Reacted :${name}: on message ${args.ts} in ${args.channel}.`);
}

async function mark() {
  if (!args.channel || !args.ts) throw new Error('--mark requires --channel and --ts');
  if (args['thread-ts']) {
    await call('subscriptions.thread.mark',
      { channel: args.channel, thread_ts: args['thread-ts'], ts: args.ts, read: '1' });
    console.log(`Marked thread ${args['thread-ts']} in ${args.channel} read up to ${args.ts}. Reversible.`);
    return;
  }
  await call('conversations.mark', { channel: args.channel, ts: args.ts });
  console.log(`Marked ${args.channel} read up to ${args.ts}. Reversible — re-reading the conversation re-surfaces it.`);
}

// REAL SEND — post a reviewed body to a conversation as the signed-in user. This is the only write in
// this script that reaches another person, so it stays human-in-the-loop: run it solely on Russ's
// explicit per-message instruction to send, after he has reviewed this exact body this turn. The body
// comes from a file (never an inline arg) so the writing-review gate can read and receipt it, the same way
// the mail staging commands take --body-file. The body is Slack mrkdwn: chat.postMessage renders `<url|text>`
// as a link and `<@U…>` as a mention.
async function send() {
  if (!args.channel) {
    throw new Error('--send requires --channel (a DM/group/channel/conversation id; resolve a person with --find-dm)');
  }
  const bodyFile = args['body-file'];
  if (!bodyFile || bodyFile === true) {
    throw new Error('--send requires --body-file (the reviewed message body; the writing-review gate reads it)');
  }
  let text;
  try {
    text = require('fs').readFileSync(bodyFile, 'utf8').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
  } catch {
    throw new Error(`--send: cannot read --body-file ${bodyFile}`);
  }
  if (!text) throw new Error(`--send: --body-file ${bodyFile} is empty`);

  const params = { channel: args.channel, text };
  if (args['thread-ts']) params.thread_ts = args['thread-ts'];
  const r = await call('chat.postMessage', params);
  let permalink = '';
  try {
    permalink = (await call('chat.getPermalink', { channel: r.channel, message_ts: r.ts })).permalink || '';
  } catch { /* permalink optional — the send already succeeded */ }
  console.log(`Sent to ${r.channel} at ts ${r.ts}.${permalink ? ` Link: ${permalink}` : ''}`);
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
  if (args['find-dm']) return await findDm();
  if (args['find-by-domain']) return await findByDomain();
  throw new Error('Specify --check, --list-unread, --show, --history, --react, --mark, --send, --find-dm, or --find-by-domain');
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
