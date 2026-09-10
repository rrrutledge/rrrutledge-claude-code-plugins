// Read / draft / clear personal Gmail (or Google Workspace) mail via the Gmail REST API over OAuth.
//
// Auth: a single OAuth token (shared with filters.js) authorizes every operation — see gmail-oauth.js
// for the client and gmail-auth.js for the one-time sign-in. Secrets are the OAuth client id/secret in
// GMAIL_OAUTH_CLIENT_ID / GMAIL_OAUTH_CLIENT_SECRET; the token caches to ~/.claude/gmail/oauth-token.json
// and auto-refreshes, so this runs silently after the first consent. The account is whatever that token
// authorizes (read from the profile) — no address env var needed.
//
// Signature: optionally set GMAIL_SIGNATURE_HTML in the environment (an HTML snippet, e.g.
// "Name<br>Title<br><a href=\"...\">...</a>"). When set, --draft-new and --reply append it to every
// staged draft (after a blank line; before the quoted original on replies) so it isn't retyped by hand.
//
// List inbox:    node gmail.js --list-inbox [--top=50] [--json]
//                (inbox, newest-first; --json emits a structured array for scripts)
// List sent:     node gmail.js --list-sent [--top=50] [--json]
//                (sent mail, newest-first; same output format as --list-inbox)
// Search:        node gmail.js --search=<query> [--folder=all|inbox|sent] [--top=50] [--json]
//                (search for messages matching query; --folder defaults to "all" (All Mail — everything
//                 except Spam/Trash); --query is Gmail's own search syntax, matching headers + body;
//                 newest-first)
// Show one:      node gmail.js --show=<message-id>
//                (<message-id> is the RFC822 Message-ID header, e.g. <abc@mail.gmail.com>; looked up
//                 across All Mail, so it shows whether the message is still in the inbox or archived)
// Auth headers:  node gmail.js --auth=<message-id>
//                (emits a JSON object with the message's envelope-authentication headers as Gmail
//                 stamped them — the raw Authentication-Results value(s) and any Received-SPF value(s),
//                 plus the From. This is the SPF/DKIM/DMARC provenance the spoofable From: line can't
//                 give; the drainer's security screen reads it per item. Looked up in All Mail like --show.)
// List drafts:   node gmail.js --list-drafts [--top=30]
// Save attachments: node gmail.js --save-attachments=<message-id> [--out-dir=<dir>]
//                (downloads every attachment on a message to <dir> (default: cwd); looked up in All Mail
//                 like --show. Prints each saved filename + size. Attachments with no filename are named
//                 "attachment-N".)
// Draft a reply: node gmail.js --reply --message-id=<id> --body-file=reply.md [--attach=a.pdf,b.png] [--inline=s1.png,s2.png] [--no-quote]
//                (stages a DRAFT reply in the thread; never sends; replaces any prior draft on the same
//                 thread; prints a draft-id for --send-draft. <id> is looked up across All Mail — pass the
//                 most recent message in the thread, even one the user sent; a reply to the user's own
//                 message keeps its recipients instead of self. --to overrides the computed recipient (for
//                 threading off a no-reply relay whose real correspondent is in Reply-To, e.g. a Google
//                 "shared a file" notification). --body-file is Markdown — bold, links, lists all work;
//                 HTML tags pass through. Appends GMAIL_SIGNATURE_HTML, if set, after the body and before
//                 the quoted original. --no-quote suppresses the auto-appended quoted original (threading
//                 still set), for an interleaved reply where --body-file supplies its own quote with
//                 responses spliced between the sender's lines.)
// Draft new:     node gmail.js --draft-new --to="a@x,b@y" --subject="..." --body-file=msg.md [--cc=c@z] [--attach=a.pdf,b.png] [--inline=s1.png,s2.png]
//                (stages a fresh DRAFT; never sends; prints a draft-id. --body-file is Markdown — bold,
//                 links, lists all work; HTML tags pass through. Appends GMAIL_SIGNATURE_HTML, if set,
//                 after the body.)
//                (--attach takes one path or a comma-separated list; files ride along as attachments.
//                 --inline takes image paths the same way but embeds them in the body via cid: references
//                 (rendered after the body text), so they show inline instead of as a file list. Both
//                 flags may be combined.)
// Send a draft:  node gmail.js --send-draft --draft-id=<draft-id>
//                (promotes one already-staged draft: sends it, then removes it from Drafts — Gmail files
//                 the sent copy in Sent. The <draft-id> is what --reply / --draft-new printed when it
//                 staged the draft. SENDS REAL MAIL — only invoke on Russ's explicit per-message say-so
//                 after he has reviewed that exact draft.)
// Delete a draft: node gmail.js --delete-draft=<draft-id>
//                (discards one staged draft that's no longer wanted — e.g. the outreach it belonged to
//                 was abandoned. Removes it from Drafts entirely; drafts have no other home, so this isn't
//                 recoverable the way --archive is. The <draft-id> is the same id --reply / --draft-new
//                 printed when the draft was staged.)
// Archive one:   node gmail.js --archive=<message-id>
//                (removes the message from the inbox but keeps it in All Mail — the way mail is cleared)
// Auth glance:   node gmail.js --check
//                (signs in and reports the inbox count; non-zero exit on auth failure)

const fs = require('fs');
const path = require('path');
const os = require('os');

// Resolve deps from a stable per-user location so this script works regardless of which plugin copy
// (cache vs marketplace clone) runs it. Seed it once with setup.js. Mirrors ms-graph's graph-client.js.
module.paths.push(path.join(os.homedir(), '.claude', 'gmail', 'node_modules'));

const { google } = require('googleapis');
const { simpleParser } = require('mailparser');
const { marked } = require('marked');
const MailComposer = require('nodemailer/lib/mail-composer');
const addressparser = require('nodemailer/lib/addressparser');
const { getAuthedClient } = require('./gmail-oauth');

const USER_ID = 'me';
let gmail; // the Gmail API client, built in main() after auth so a sign-in error is reported cleanly.

// Gmail's own compose editor defaults newly-typed text to Arial/sans-serif at "small" size unless the
// user has picked a different default under Settings > General > Default text style. An HTML body sent
// with no font-family renders in the client's fallback font instead, so if the recipient (or Russell
// himself, editing before send) types alongside it, the two visibly mismatch. A single wrapping <div>
// is fragile against a rich-text editor's own normalize-on-open pass (confirmed for Outlook: it drops
// an inherited wrapper style from existing paragraphs while stamping newly-typed text with its own
// explicit font) - style each paragraph directly instead, so the font is baked into content Gmail's
// editor would otherwise treat as already "finished."
const DEFAULT_FONT_STYLE = 'font-family:Arial,Helvetica,sans-serif; font-size:small; color:rgb(0,0,0)';
function withDefaultFont(html) {
  let styled = html.replace(/<p(\s[^>]*)?>/gi, (m, attrs) => {
    attrs = attrs || '';
    if (/style\s*=/i.test(attrs)) return m; // already styled - leave it
    return `<p${attrs} style="${DEFAULT_FONT_STYLE}">`;
  });
  if (!/<p[\s>]/i.test(styled)) styled = `<p style="${DEFAULT_FONT_STYLE}">${styled}</p>`;
  return styled;
}

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);

function withSignature(html) {
  const sig = process.env.GMAIL_SIGNATURE_HTML;
  return sig ? `${html}<br><br>${sig}` : html;
}

// nodemailer's MailComposer auto-generates a plain-text alternative when a mail object carries only
// `html`, and that auto-generated part has been observed to silently drop whole paragraphs on a body
// containing a deeply nested quoted history (the common Outlook-style "From:/Date:/To:" chain embedded
// as literal HTML inside a <blockquote>) - the HTML part staged correctly, but `--show` and any other
// reader of `parsed.text` saw a truncated message. Deriving text explicitly from the same html string
// this function already built removes the mismatch: both MIME parts trace back to one source.
function htmlToPlainText(html) {
  return (html || '')
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|blockquote|li|h[1-6])>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

const addr = (a) => a ? `${a.name || ''} <${a.address}>`.trim() : '?';
const fromList = (arr) => (arr || []).map(addr).join(', ');
const stripId = (id) => String(id || '').replace(/^<|>$/g, '');
const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

// Decode RFC2047 encoded-words (=?charset?B/Q?text?=) that appear in raw header values (Subject, display
// names). The metadata fetch returns headers as raw strings, so encoded non-ASCII names arrive as
// =?UTF-8?...?= and must be decoded to render as the sender wrote them. Adjacent encoded words separated
// only by whitespace join with no space between (per the RFC). Unknown charsets fall back to UTF-8.
function decodeMimeWords(str) {
  if (!str || str.indexOf('=?') === -1) return str || '';
  const joined = str.replace(/\?=\s+=\?/g, '?==?');
  return joined.replace(/=\?([^?]+)\?([bBqQ])\?([^?]*)\?=/g, (m, charset, enc, text) => {
    try {
      let buf;
      if (enc.toUpperCase() === 'B') {
        buf = Buffer.from(text, 'base64');
      } else {
        const bytes = text.replace(/_/g, ' ').replace(/=([0-9A-Fa-f]{2})/g, (mm, h) => String.fromCharCode(parseInt(h, 16)));
        buf = Buffer.from(bytes, 'binary');
      }
      const cs = charset.toLowerCase();
      const nodeCs = /utf-?8/.test(cs) ? 'utf8'
        : /(iso-8859-1|latin1|windows-1252)/.test(cs) ? 'latin1'
        : /us-ascii|ascii/.test(cs) ? 'ascii' : 'utf8';
      return buf.toString(nodeCs);
    } catch { return m; }
  });
}

// A Gmail message.payload.headers array -> { lowercased-name: [values] }.
function indexHeaders(headers) {
  const idx = {};
  for (const h of headers || []) {
    const k = (h.name || '').toLowerCase();
    (idx[k] = idx[k] || []).push(h.value || '');
  }
  return idx;
}
const hdr = (idx, name) => (idx[name] || [])[0] || '';
const hdrAll = (idx, name) => (idx[name] || []).join(', ');
const parseAddrs = (value) => addressparser(value || '').map(a => ({ name: decodeMimeWords(a.name || ''), address: a.address || '' }));

// The account's own address, read once from the profile and cached. Drives fromMe/toMe.
let _account = null;
async function account() {
  if (_account !== null) return _account;
  const r = await gmail.users.getProfile({ userId: USER_ID });
  _account = (r.data.emailAddress || '').toLowerCase();
  return _account;
}

// Resolve an RFC822 Message-ID header to Gmail's internal message id (or null). Searches All Mail (every
// label except Spam/Trash), so it finds a message whether it's in the inbox, archived, or sent.
async function findMessageId(messageId) {
  const q = `rfc822msgid:${stripId(messageId)}`;
  const r = await gmail.users.messages.list({ userId: USER_ID, q, maxResults: 5 });
  const msgs = r.data.messages || [];
  return msgs.length ? msgs[0].id : null;
}

// Fetch one message's raw RFC822 and parse it — reused by every path that needs the full body, headers,
// or attachments (--show, --auth, --reply, --save-attachments). Also returns the message's threadId and
// labels so callers that need them (reply threading) don't refetch.
async function getParsed(gmailId) {
  const r = await gmail.users.messages.get({ userId: USER_ID, id: gmailId, format: 'raw' });
  const parsed = await simpleParser(Buffer.from(r.data.raw, 'base64url'));
  return { parsed, threadId: r.data.threadId, labelIds: r.data.labelIds || [], internalDate: r.data.internalDate };
}

// Bounded-concurrency map — the enumerate/search/list-drafts paths fetch per-message metadata, and a
// small pool keeps that fast without tripping Gmail's per-user rate limits. Preserves input order.
async function mapLimit(items, limit, fn) {
  const out = new Array(items.length);
  let i = 0;
  async function worker() {
    while (i < items.length) {
      const cur = i++;
      out[cur] = await fn(items[cur], cur);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) || 0 }, worker));
  return out;
}

const META_HEADERS = ['From', 'To', 'Cc', 'Subject', 'Date', 'Message-ID'];

// One envelope-level item (the --json / listing shape) from a message id, using a metadata fetch (no
// body) so listings stay cheap. `acct` is the account address for the fromMe/toMe flags.
async function metaItem(gmailId, acct) {
  const r = await gmail.users.messages.get({
    userId: USER_ID, id: gmailId, format: 'metadata', metadataHeaders: META_HEADERS,
  });
  const idx = indexHeaders(r.data.payload && r.data.payload.headers);
  const fromAddrs = parseAddrs(hdr(idx, 'from'));
  const toAddrs = parseAddrs(hdrAll(idx, 'to'));
  const dateHdr = hdr(idx, 'date');
  const d = dateHdr ? new Date(dateHdr) : null;
  const received = d && !isNaN(d.getTime()) ? d.toISOString()
    : r.data.internalDate ? new Date(Number(r.data.internalDate)).toISOString()
    : new Date().toISOString();
  const labelIds = r.data.labelIds || [];
  return {
    id: hdr(idx, 'message-id') || `gmail-${gmailId}`,
    uid: gmailId,
    subject: decodeMimeWords(hdr(idx, 'subject')) || '(no subject)',
    from: fromList(fromAddrs),
    fromAddress: fromAddrs[0] ? fromAddrs[0].address : '',
    fromMe: !!(fromAddrs[0] && (fromAddrs[0].address || '').toLowerCase() === acct),
    toMe: toAddrs.some(a => (a.address || '').toLowerCase() === acct),
    received,
    isRead: !labelIds.includes('UNREAD'),
  };
}

function printListing(items, header) {
  console.log(header);
  for (const m of items) {
    console.log(`\n--- ${m.received.slice(0, 16)}  |  ${m.isRead ? 'read ' : 'UNREAD'} | ${m.subject}`);
    console.log(`    from: ${m.from}`);
    console.log(`    id:   ${m.id}`);
  }
}

async function listFolder(labelId, name) {
  const top = parseInt(args.top || '50', 10);
  const acct = await account();
  const r = await gmail.users.messages.list({ userId: USER_ID, labelIds: [labelId], maxResults: top });
  const ids = (r.data.messages || []).map(m => m.id);
  if (!ids.length) { if (args.json) console.log('[]'); else console.log(`No messages in ${name}.`); return; }
  const out = await mapLimit(ids, 15, id => metaItem(id, acct)); // messages.list is already newest-first
  if (args.json) { console.log(JSON.stringify(out, null, 2)); return; }
  printListing(out, `${out.length} message(s) in ${name} (newest first):`);
}

async function search() {
  const query = String(args.search);
  const top = parseInt(args.top || '50', 10);
  const folderArg = String(args.folder || 'all').toLowerCase();
  const labelIds = folderArg === 'inbox' ? ['INBOX'] : folderArg === 'sent' ? ['SENT'] : null;
  const where = folderArg === 'inbox' ? 'INBOX' : folderArg === 'sent' ? 'Sent Mail' : 'All Mail';
  const acct = await account();
  const params = { userId: USER_ID, q: query, maxResults: top };
  if (labelIds) params.labelIds = labelIds;
  const r = await gmail.users.messages.list(params);
  const ids = (r.data.messages || []).map(m => m.id);
  if (!ids.length) {
    if (args.json) console.log('[]'); else console.log(`No messages found matching "${query}" in ${where}.`);
    return;
  }
  const out = await mapLimit(ids, 15, id => metaItem(id, acct)); // already newest-first
  if (args.json) { console.log(JSON.stringify(out, null, 2)); return; }
  printListing(out, `${out.length} message(s) matching "${query}" in ${where} (newest first):`);
}

async function show() {
  const gid = await findMessageId(args.show);
  if (!gid) { console.log('Message not found.'); return; }
  const { parsed } = await getParsed(gid);
  console.log(`Subject: ${parsed.subject || ''}`);
  console.log(`From: ${parsed.from ? parsed.from.text : ''}`);
  console.log(`To: ${parsed.to ? parsed.to.text : ''}`);
  if (parsed.cc) console.log(`Cc: ${parsed.cc.text}`);
  console.log(`Date: ${parsed.date ? parsed.date.toISOString() : ''}`);
  console.log('\n' + (parsed.text || clean(parsed.html) || '(no body)'));
}

async function auth() {
  // The envelope-authentication headers Gmail stamped onto the message, as JSON for the drainer's
  // security screen. Authentication-Results carries the SPF/DKIM/DMARC verdicts (and the domains that
  // actually authenticated); Received-SPF carries the SPF check on its own. Both can appear more than
  // once (each relay adds its own), so headerLines is read directly to keep every instance — the value
  // is the header line with its "Name:" prefix stripped. Interpretation lives in the drainer's Python
  // adapters, so this just exposes the raw values. Looked up in All Mail like --show.
  const gid = await findMessageId(args.auth);
  if (!gid) { console.log('{}'); return; }
  const { parsed } = await getParsed(gid);
  const headerValues = (name) => (parsed.headerLines || [])
    .filter(h => h.key === name)
    .map(h => h.line.replace(/^[^:]*:\s*/, '').trim())
    .filter(Boolean);
  console.log(JSON.stringify({
    from: parsed.from ? parsed.from.text : '',
    fromAddress: parsed.from && parsed.from.value && parsed.from.value[0] ? parsed.from.value[0].address : '',
    authenticationResults: headerValues('authentication-results'),
    receivedSpf: headerValues('received-spf'),
  }, null, 2));
}

async function saveAttachments() {
  const gid = await findMessageId(args['save-attachments']);
  if (!gid) { console.log('Message not found in inbox or All Mail.'); return; }
  const { parsed } = await getParsed(gid);
  const atts = parsed.attachments || [];
  if (!atts.length) { console.log('Message has no attachments.'); return; }
  const outDir = args['out-dir'] ? String(args['out-dir']) : process.cwd();
  fs.mkdirSync(outDir, { recursive: true });
  atts.forEach((a, i) => {
    const name = a.filename || `attachment-${i + 1}`;
    const dest = path.join(outDir, name);
    fs.writeFileSync(dest, a.content);
    console.log(`Saved ${dest} (${a.content.length} bytes, ${a.contentType || 'unknown type'})`);
  });
}

async function listDrafts() {
  const top = parseInt(args.top || '30', 10);
  const r = await gmail.users.drafts.list({ userId: USER_ID, maxResults: top });
  const drafts = r.data.drafts || [];
  if (!drafts.length) { console.log('No drafts.'); return; }
  const out = await mapLimit(drafts, 10, async (d) => {
    const g = await gmail.users.drafts.get({ userId: USER_ID, id: d.id, format: 'metadata' });
    const idx = indexHeaders(g.data.message && g.data.message.payload && g.data.message.payload.headers);
    return {
      subject: decodeMimeWords(hdr(idx, 'subject')) || '(no subject)',
      to: fromList(parseAddrs(hdrAll(idx, 'to'))),
      date: hdr(idx, 'date'),
      id: d.id,
      messageId: hdr(idx, 'message-id') || null,
    };
  });
  if (args.json) { console.log(JSON.stringify(out, null, 2)); return; }
  console.log(`${out.length} draft(s):`);
  for (const m of out) console.log(`\n--- ${m.subject}\n    to: ${m.to}`);
}

function inlineList() {
  // --inline=<path[,path]> — images embedded in the body via cid: references (see inlineImagesHtml),
  // so they render in-line where the reader is looking rather than as a file list at the bottom. Each
  // gets a stable index-based cid so attachments() and inlineImagesHtml() agree on the same reference.
  if (!args.inline) return [];
  const paths = String(args.inline).split(',').map(s => s.trim()).filter(Boolean);
  return paths.map((p, i) => ({ path: p, filename: path.basename(p), cid: `inline${i}@gmail-skill` }));
}

function inlineImagesHtml() {
  // One <img> per --inline file, referencing its cid attachment, appended after the body text so the
  // images appear in the message body. Each on its own line, capped to the message width.
  const items = inlineList();
  if (!items.length) return '';
  return '<br><br>' + items.map(a =>
    `<div style="margin:8px 0"><img src="cid:${a.cid}" alt="${a.filename}" style="max-width:100%;height:auto"></div>`
  ).join('');
}

function attachments() {
  // --attach=<path[,path]> — regular file attachments (comma-separated for several). --inline=<path[,path]>
  // — images cid-embedded in the body instead. Both flags may be used together.
  const regular = args.attach
    ? String(args.attach).split(',').map(s => s.trim()).filter(Boolean).map(p => ({ filename: path.basename(p), path: p }))
    : [];
  const inline = inlineList().map(a => ({ ...a, contentDisposition: 'inline' }));
  const all = [...regular, ...inline];
  return all.length ? all : undefined;
}

// Build the outgoing RFC822 MIME (a Buffer) with MailComposer — transport-independent; the Gmail API
// takes it base64url-encoded as the draft/message `raw`. htmlToPlainText supplies the text alternative;
// In-Reply-To/References thread a reply.
async function buildMime({ to, cc, subject, html, inReplyTo, references }) {
  const styledHtml = withDefaultFont(html);
  const mail = {
    from: await account(), to, cc, subject,
    html: styledHtml,
    text: htmlToPlainText(styledHtml),
    attachments: attachments(),
    inReplyTo: inReplyTo ? `<${stripId(inReplyTo)}>` : undefined,
    references,
  };
  return new MailComposer(mail).compile().build();
}

// Create a draft from built MIME, threading it onto threadId when given so Gmail files it in the same
// conversation. Returns the Gmail draft id (what --send-draft / --delete-draft take).
async function createDraft(rawBuffer, threadId) {
  const message = { raw: rawBuffer.toString('base64url') };
  if (threadId) message.threadId = threadId;
  const r = await gmail.users.drafts.create({ userId: USER_ID, requestBody: { message } });
  return r.data.id;
}

async function dropDraftsInThread(threadId) {
  // Before staging a fresh reply, remove any prior draft on the same thread so only one draft per thread
  // ever exists — kills the "opened the wrong (stale) draft" hazard. drafts.list carries each draft's
  // message.threadId, so matching by thread is a single list call plus a delete per stale draft.
  if (!threadId) return 0;
  const r = await gmail.users.drafts.list({ userId: USER_ID, maxResults: 500 });
  const stale = (r.data.drafts || []).filter(d => d.message && d.message.threadId === threadId);
  for (const d of stale) await gmail.users.drafts.delete({ userId: USER_ID, id: d.id });
  return stale.length;
}

async function reply() {
  if (!args['message-id'] || !args['body-file']) {
    throw new Error('--reply requires --message-id and --body-file');
  }
  const html = marked.parse(fs.readFileSync(args['body-file'], 'utf8'));
  const gid = await findMessageId(args['message-id']);
  if (!gid) throw new Error('Original message not found in inbox or All Mail.');
  const { parsed: orig, threadId } = await getParsed(gid);
  const acct = await account();

  const when = orig.date ? orig.date.toUTCString() : '';
  const quoted = `<br><br>On ${when}, ${orig.from ? orig.from.text : ''} wrote:<br>` +
    `<blockquote style="margin:0 0 0 .8ex;border-left:1px solid #ccc;padding-left:1ex">` +
    `${orig.html || (orig.text || '').replace(/\n/g, '<br>')}</blockquote>`;
  const refs = [orig.references, orig.messageId].flat().filter(Boolean).join(' ');
  const subject = /^re:/i.test(orig.subject || '') ? orig.subject : `Re: ${orig.subject || ''}`;
  const dropped = await dropDraftsInThread(threadId);
  // Pick recipients by who wrote the message we're answering:
  // - normal reply (someone else's message): To = its sender; CC = its other To+CC recipients.
  // - follow-up to our OWN sent message (the most-recent message is ours): keep its recipients, so the
  //   nudge goes to the people we wrote to (To/CC) rather than back to ourselves.
  const fromSelf = orig.from && orig.from.value.some(
    a => a.address && a.address.toLowerCase() === acct);
  // --to overrides the computed recipient: needed when replying into a thread whose From is a no-reply
  // relay (e.g. a Google "shared a file" notification) whose real correspondent is in Reply-To — pass
  // that address so the threaded reply reaches the person, not the no-reply box.
  const to = args.to
    ? args.to
    : (fromSelf
        ? (orig.to ? fromList(orig.to.value) : '')
        : (orig.from ? orig.from.text : ''));
  const ccSource = fromSelf
    ? (orig.cc ? orig.cc.value : [])
    : [...(orig.to ? orig.to.value : []), ...(orig.cc ? orig.cc.value : [])];
  const allRecips = ccSource
    .map(a => a.address).filter(a => a && a.toLowerCase() !== acct);
  const ccList = args.cc
    ? [...new Set([args.cc, ...allRecips])].join(', ')
    : allRecips.join(', ');
  const raw = await buildMime({
    to, cc: ccList || undefined, subject, html: withSignature(html + inlineImagesHtml()) + (args['no-quote'] ? '' : quoted),
    inReplyTo: orig.messageId, references: refs,
  });
  const draftId = await createDraft(raw, threadId);
  const note = dropped ? ` (replaced ${dropped} prior draft on this thread)` : '';
  console.log(`Draft reply staged in Drafts (re: "${clean(subject).slice(0, 60)}")${note}. Review in Gmail; never sent.`);
  console.log(`draft-id: ${draftId}`);
}

async function draftNew() {
  if (!args.to || !args.subject || !args['body-file']) {
    throw new Error('--draft-new requires --to, --subject, and --body-file');
  }
  const html = marked.parse(fs.readFileSync(args['body-file'], 'utf8'));
  const raw = await buildMime({ to: args.to, cc: args.cc || undefined, subject: args.subject, html: withSignature(html + inlineImagesHtml()) });
  const draftId = await createDraft(raw, null);
  console.log(`Draft staged in Drafts to ${args.to}. Review in Gmail; never sent.`);
  console.log(`draft-id: ${draftId}`);
}

async function sendDraft() {
  // Promote ONE staged draft to a real send: drafts.send transmits it and removes it from Drafts (Gmail
  // files the sent copy in Sent). This is the only path that emits real mail — gated upstream on Russ's
  // explicit per-message instruction after he reviewed this draft.
  if (!args['draft-id']) throw new Error('--send-draft requires --draft-id (the id printed when the draft was staged)');
  const draftId = String(args['draft-id']);
  let subject = '', rcpt = [];
  try {
    const g = await gmail.users.drafts.get({ userId: USER_ID, id: draftId, format: 'metadata' });
    const idx = indexHeaders(g.data.message && g.data.message.payload && g.data.message.payload.headers);
    subject = decodeMimeWords(hdr(idx, 'subject'));
    rcpt = [...parseAddrs(hdrAll(idx, 'to')), ...parseAddrs(hdrAll(idx, 'cc'))].map(a => a.address).filter(Boolean);
  } catch (e) {
    throw new Error(`No draft with id ${draftId} in Drafts. Re-stage the draft and use the draft-id it prints. (${e.message})`);
  }
  if (!rcpt.length) throw new Error('Draft has no recipients; refusing to send.');
  await gmail.users.drafts.send({ userId: USER_ID, requestBody: { id: draftId } });
  console.log(`Sent to ${rcpt.join(', ')} (subject: "${clean(subject).slice(0, 60)}"). Draft removed; copy is in Sent.`);
}

async function deleteDraft() {
  const draftId = String(args['delete-draft']);
  try {
    await gmail.users.drafts.delete({ userId: USER_ID, id: draftId });
    console.log(`Deleted draft ${draftId} from Drafts.`);
  } catch (e) {
    const status = e.code || (e.response && e.response.status);
    if (status === 404) { console.log(`No draft with id ${draftId} in Drafts (already deleted?).`); return; }
    throw e;
  }
}

async function archive() {
  const gid = await findMessageId(args.archive);
  if (!gid) { console.log('Message not found (already cleared?).'); return; }
  // Removing the INBOX label leaves the message in All Mail — Gmail's archive. Unlike Trash there's no
  // 30-day purge timer; the message just leaves the inbox. A no-op if it's already out of the inbox.
  await gmail.users.messages.modify({ userId: USER_ID, id: gid, requestBody: { removeLabelIds: ['INBOX'] } });
  console.log(`Archived (left the inbox, kept in All Mail) id ${stripId(args.archive).slice(0, 40)}.... Find it in All Mail or search.`);
}

async function check() {
  const prof = await gmail.users.getProfile({ userId: USER_ID });
  const lbl = await gmail.users.labels.get({ userId: USER_ID, id: 'INBOX' });
  console.log(`Signed in as ${prof.data.emailAddress}. Inbox has ${lbl.data.messagesTotal} message(s).`);
}

// Surface API/auth failures with an actionable hint. An insufficient-scope or invalid-grant error means
// the cached token predates a scope change (or was revoked) — the fix is re-running the one-time sign-in.
function describeError(e) {
  const data = e.response && e.response.data;
  let msg = e.message;
  if (data && data.error) msg = data.error.message || data.error_description || data.error || msg;
  const blob = `${msg} ${JSON.stringify(data || {})}`;
  if (/insufficient|scope|invalid_grant|unauthorized|Not signed in/i.test(blob)) {
    return `${msg} — re-run the one-time sign-in (node <gmail>/scripts/gmail-auth.js via browser-chauffeur), then retry.`;
  }
  return msg;
}

(async () => {
  gmail = google.gmail({ version: 'v1', auth: getAuthedClient() });
  if (args['list-inbox']) return await listFolder('INBOX', 'INBOX');
  if (args['list-drafts']) return await listDrafts();
  if (args['save-attachments']) return await saveAttachments();
  if (args.show) return await show();
  if (args.auth) return await auth();
  if (args.reply) return await reply();
  if (args['draft-new']) return await draftNew();
  if (args['send-draft']) return await sendDraft();
  if (args['delete-draft']) return await deleteDraft();
  if (args.archive) return await archive();
  if (args['list-sent']) return await listFolder('SENT', 'Sent Mail');
  if (args.search) return await search();
  if (args.check) return await check();
  throw new Error('Specify --list-inbox, --list-sent, --search, --list-drafts, --save-attachments, --show, --auth, --reply, --draft-new, --send-draft, --delete-draft, --archive, or --check');
})().catch(e => { console.error('Error:', describeError(e)); process.exit(1); });
