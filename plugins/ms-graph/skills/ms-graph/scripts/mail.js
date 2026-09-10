// Read / draft / send personal Outlook mail via Microsoft Graph.
//
// List unread:   node mail.js --list-unread [--top=30]
//                (inbox unread, newest-first; one block per message with id + webLink)
// List inbox:    node mail.js --list-inbox [--top=50] [--json]
//                (inbox read+unread, newest-first; count-capped by --top; --json emits a
//                 structured array for scripts)
// List junk:     node mail.js --list-junk [--top=50] [--json]
//                (Junk Email folder, read+unread, newest-first; same shape as --list-inbox)
// List drafts:   node mail.js --list-drafts [--top=30]
//                (drafts folder, most-recently-edited first; same block format with id)
// Search:        node mail.js --search="Griffiths" [--top=10]
//                (flags any result still sitting in Drafts with a "[DRAFT — NOT SENT]" tag,
//                 so a drafted-but-unsent reply is never mistaken for a sent message)
// Show one:      node mail.js --show=<messageId> [--html]
//                (default emits the body as plain text. --html emits the raw HTML body.content instead
//                 — the way to recover a newsletter's hosted-PDF link, or its inline/"view in browser"
//                 content, that the plaintext view strips out. Prints a "*** DRAFT — NOT SENT ***"
//                 banner up top when the message is still sitting in Drafts.)
// Auth headers:  node mail.js --auth=<messageId>
//                (emits a JSON object with the message's envelope-authentication headers as the
//                 receiving system stamped them — the raw Authentication-Results value(s) and any
//                 Received-SPF value(s), plus the From. This is the SPF/DKIM/DMARC provenance the
//                 spoofable From: line can't give; the drainer's security screen reads it per item.)
// Draft a reply: node mail.js --reply --message-id=<id> --body-file=reply.md [--attach=file1.pdf,file2.png]
//                (creates a DRAFT reply-all in the thread; never sends. --body-file is Markdown —
//                 blank-line paragraphs, lists, **bold**, and links all render as HTML; raw HTML
//                 passes through. --attach adds file attachments, same syntax as --draft-new.)
// Draft new:     node mail.js --draft-new --to="a@x,b@y" --subject="..." --body-file=msg.md \
//                  [--cc=c@z] [--attach=file1.pdf,file2.png] [--replace] [--text]
//                (creates a fresh DRAFT to specific recipients; never sends. --body-file is Markdown
//                 (same as --reply). --attach adds file attachments; --replace first deletes any
//                 existing drafts with the same subject so re-runs don't pile up duplicates; --text
//                 sends the body-file as literal plain text instead of rendering the Markdown.)
// Send to self:  node mail.js --send-self --subject="..." --body-file=note.txt
//                (sends a plain-text mail to your own inbox; handy for phone copy-paste)
// Send a draft:  node mail.js --send-draft --message-id=<draftId>
//                (promotes ONE staged draft — from --reply or --draft-new — to a real send via
//                 Graph's own send action; transmits exactly what's saved in Drafts, then Graph
//                 auto-files the sent copy in Sent Items. The only path that emits real mail —
//                 reserved for Russ's explicit per-message "send it" after he's reviewed this
//                 exact draft.)
// Delete one:    node mail.js --delete=<messageId>
//                (moves the message to Archive — reversible, keeps it searchable, never a permanent purge)
// Not junk:      node mail.js --not-junk=<messageId>
//                (un-junks a message: moves it to Inbox and reports it "not junk" to Microsoft's
//                 filter so future mail from that sender is less likely to be misfiled. Uses the
//                 beta reportMessage action; falls back to a plain move-to-Inbox if that ever fails.)
// Report phish:  node mail.js --report-phish=<messageId>
//                (reports a message to Microsoft as phishing — retrains the filter — and moves it to
//                 Junk Email. Reversible: recoverable from Junk. Beta reportMessage action; falls back
//                 to a plain move-to-Junk if that ever fails.)
// Unsubscribe:   node mail.js --unsubscribe=<messageId>
//                (unsubscribes from a marketing sender via its List-Unsubscribe header — an https
//                 one-click POST when offered, else a GET that may need a confirmation click. A
//                 mailto-only sender is surfaced to send by hand. For mail addressed to the user
//                 alone, never a shared list/group he doesn't own — archive-rule those instead.)
// Get attachments: node mail.js --get-attachments=<messageId> [--out-dir=.tmp]
//                (downloads every file attachment on a message to --out-dir, default the
//                 current directory; prints each saved path. Skips non-file attachments, e.g.
//                 inline/reference attachments Graph can't materialize as bytes.)
//
// --- Inbox rules (server-side filters) ---
// List rules:    node mail.js --list-rules [--json]
// Create rule:   node mail.js --create-rule --name="Corporate Subjects" \
//                  --subject-contains="A||B||C" [--body-contains=..] [--from-contains=..] \
//                  [--subject-or-body=..] [--except-from="gmail.com||outlook.com||icloud.com"] \
//                  --move-to=archive [--mark-read] [--delete-msg] [--no-stop]
//                (move-to takes a well-known folder id: 'archive' or 'deleteditems'.
//                 --except-from is the personal-domain fence. Multi-value flags split on `||`.
//                 Rules stop-processing by default; --no-stop disables that.)
// Append phrase: node mail.js --append-rule="Corporate Subjects" --subject-contains="D||E"
//                (adds phrases to an existing rule by id or display name — the "add to bucket" op)
// Delete rule:   node mail.js --delete-rule="<ruleId or name>"

const fs = require('fs');
const path = require('path');
const { marked } = require('marked');
const { getGraphClient } = require('./graph-client');

// Outlook's own compose editor stamps newly-typed text with Calibri 12pt black. An HTML body posted
// via the API with no font-family renders in the client's fallback font instead (often a serif), so
// if Russell types alongside it, his edits visibly mismatch the drafted text. A style on <body> alone
// doesn't survive - Outlook re-normalizes the whole draft the moment it's opened for editing, and that
// pass drops an inherited body-level font from the existing paragraphs (confirmed: it stamped a
// Russell-typed addition with explicit Calibri while leaving the pre-existing paragraphs unstyled).
// Match Outlook's own per-paragraph convention instead - the same margin + font style it stamps on
// text as you type - so the styling reads as Outlook's own and survives that normalization pass.
const DEFAULT_FONT_STYLE = 'margin-top:1em; margin-bottom:1em; font-family:Calibri,Helvetica,sans-serif; font-size:12pt; color:rgb(0,0,0)';
function withDefaultFont(html) {
  let styled = html.replace(/<p(\s[^>]*)?>/gi, (m, attrs) => {
    attrs = attrs || '';
    if (/style\s*=/i.test(attrs)) return m; // already styled - leave it
    return `<p${attrs} style="${DEFAULT_FONT_STYLE}">`;
  });
  if (!/<p[\s>]/i.test(styled)) styled = `<p style="${DEFAULT_FONT_STYLE}">${styled}</p>`;
  return styled;
}

// A --body-file is authored as Markdown (matching the gmail skill), so blank-line-separated
// paragraphs, `1.`/`-` lists, **bold**, and [links](url) render as structured HTML with their line
// breaks intact instead of collapsing into one run-on block. Raw HTML in the file passes through
// untouched. `--text` (draft-new) bypasses this to send the file as literal plain text.
function renderBody(md) {
  return withDefaultFont(marked.parse(md));
}

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);

const addr = (r) => r?.emailAddress ? `${r.emailAddress.name || ''} <${r.emailAddress.address}>` : '?';
const strip = (html) => (html || '')
  .replace(/<style[\s\S]*?<\/style>/gi, '').replace(/<[^>]+>/g, ' ')
  .replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();

async function listUnread(client) {
  const data = await client.api('/me/mailFolders/inbox/messages')
    .filter('isRead eq false')
    .orderby('receivedDateTime desc')
    .top(parseInt(args.top || '30', 10))
    .select('id,conversationId,subject,from,toRecipients,receivedDateTime,bodyPreview,webLink')
    .get();
  const msgs = data.value || [];
  if (!msgs.length) { console.log('No unread messages.'); return; }
  console.log(`${msgs.length} unread message(s), newest first:`);
  for (const m of msgs) {
    console.log(`\n--- ${m.receivedDateTime?.slice(0, 16)}  |  ${m.subject}`);
    console.log(`    from: ${addr(m.from)}`);
    console.log(`    id:   ${m.id}`);
    console.log(`    link: ${m.webLink}`);
    console.log(`    > ${(m.bodyPreview || '').replace(/\s+/g, ' ').slice(0, 200)}`);
  }
}

async function listInbox(client) {
  // Count-capped by --top (default 50); newest-first, no time window — the keeper drains the
  // whole inbox a batch at a time across cycles.
  const data = await client.api('/me/mailFolders/inbox/messages')
    .orderby('receivedDateTime desc')
    .top(parseInt(args.top || '50', 10))
    .select('id,conversationId,subject,from,toRecipients,receivedDateTime,bodyPreview,webLink,isRead')
    .get();
  const msgs = data.value || [];
  if (args.json) {
    // The mailbox owner's own address, so a consumer can tell a message he sent from inbound mail. On any
    // lookup failure myAddr stays empty → fromMe/toMe are false → nothing is treated as own-outbound.
    let myAddr = '';
    try {
      const me = await client.api('/me').select('mail,userPrincipalName').get();
      myAddr = (me.mail || me.userPrincipalName || '').toLowerCase();
    } catch { /* leave myAddr empty */ }
    console.log(JSON.stringify(msgs.map(m => ({
      id: m.id, conversationId: m.conversationId, subject: m.subject,
      from: addr(m.from), fromAddress: m.from?.emailAddress?.address || '',
      fromMe: !!myAddr && (m.from?.emailAddress?.address || '').toLowerCase() === myAddr,
      toMe: !!myAddr && (m.toRecipients || []).some(r => (r.emailAddress?.address || '').toLowerCase() === myAddr),
      received: m.receivedDateTime, isRead: m.isRead, webLink: m.webLink,
      preview: (m.bodyPreview || '').replace(/\s+/g, ' ').slice(0, 300),
    })), null, 2));
    return;
  }
  if (!msgs.length) { console.log('No inbox messages.'); return; }
  console.log(`${msgs.length} inbox message(s) (newest first):`);
  for (const m of msgs) {
    console.log(`\n--- ${m.receivedDateTime?.slice(0, 16)}  |  ${m.isRead ? 'read ' : 'UNREAD'} | ${m.subject}`);
    console.log(`    from: ${addr(m.from)}`);
    console.log(`    id:   ${m.id}`);
    console.log(`    link: ${m.webLink}`);
    console.log(`    > ${(m.bodyPreview || '').replace(/\s+/g, ' ').slice(0, 200)}`);
  }
}

async function listJunk(client) {
  // Same shape as listInbox, reading the Junk Email folder instead — kept as a separate function
  // (not a shared helper) so each stays a simple, self-contained read of its own well-known folder.
  const data = await client.api('/me/mailFolders/junkemail/messages')
    .orderby('receivedDateTime desc')
    .top(parseInt(args.top || '50', 10))
    .select('id,conversationId,subject,from,toRecipients,receivedDateTime,bodyPreview,webLink,isRead')
    .get();
  const msgs = data.value || [];
  if (args.json) {
    // The mailbox owner's own address, so a consumer can tell a message he sent from inbound mail. On any
    // lookup failure myAddr stays empty → fromMe/toMe are false → nothing is treated as own-outbound.
    let myAddr = '';
    try {
      const me = await client.api('/me').select('mail,userPrincipalName').get();
      myAddr = (me.mail || me.userPrincipalName || '').toLowerCase();
    } catch { /* leave myAddr empty */ }
    console.log(JSON.stringify(msgs.map(m => ({
      id: m.id, conversationId: m.conversationId, subject: m.subject,
      from: addr(m.from), fromAddress: m.from?.emailAddress?.address || '',
      fromMe: !!myAddr && (m.from?.emailAddress?.address || '').toLowerCase() === myAddr,
      toMe: !!myAddr && (m.toRecipients || []).some(r => (r.emailAddress?.address || '').toLowerCase() === myAddr),
      received: m.receivedDateTime, isRead: m.isRead, webLink: m.webLink,
      preview: (m.bodyPreview || '').replace(/\s+/g, ' ').slice(0, 300),
    })), null, 2));
    return;
  }
  if (!msgs.length) { console.log('No junk messages.'); return; }
  console.log(`${msgs.length} junk message(s) (newest first):`);
  for (const m of msgs) {
    console.log(`\n--- ${m.receivedDateTime?.slice(0, 16)}  |  ${m.isRead ? 'read ' : 'UNREAD'} | ${m.subject}`);
    console.log(`    from: ${addr(m.from)}`);
    console.log(`    id:   ${m.id}`);
    console.log(`    link: ${m.webLink}`);
    console.log(`    > ${(m.bodyPreview || '').replace(/\s+/g, ' ').slice(0, 200)}`);
  }
}

async function del(client) {
  await client.api(`/me/messages/${args.delete}/move`).post({ destinationId: 'archive' });
  console.log(`Moved to Archive (id ${String(args.delete).slice(0, 20)}...). Reversible, and searchable later.`);
}

async function notJunk(client) {
  const id = args['not-junk'];
  try {
    // Beta-only: the stable markAsNotJunk action was retired Dec 2025. reportMessage is Microsoft's
    // documented replacement — it both moves the message to Inbox and retrains the junk filter.
    await client.api(`/me/messages/${id}/reportMessage`).version('beta')
      .post({ IsMessageMoveRequested: true, ReportAction: 'notJunk' });
    console.log(`Reported "not junk" and moved to Inbox (id ${String(id).slice(0, 20)}...).`);
  } catch (e) {
    // Beta endpoints can change without notice; degrade to "still un-junks the message" rather
    // than a hard failure.
    await client.api(`/me/messages/${id}/move`).post({ destinationId: 'inbox' });
    console.log(`reportMessage (beta) failed [${e.message}] — fell back to a plain move to Inbox `
      + `(id ${String(id).slice(0, 20)}...). Filter was not retrained.`);
  }
}

async function reportPhish(client) {
  const id = args['report-phish'];
  // The beta reportMessage action notJunk uses, in the other direction: report the message to Microsoft
  // (retraining the filter) and, with IsMessageMoveRequested, move it out of the inbox to Junk Email.
  // Reversible — the message stays recoverable from Junk. Personal Outlook.com accepts ReportAction
  // 'junk' but not 'phishing', so try 'phishing' first (work/enterprise), fall back to 'junk', and only
  // then to a plain move that at least gets it out of the inbox.
  for (const action of ['phishing', 'junk']) {
    try {
      await client.api(`/me/messages/${id}/reportMessage`).version('beta')
        .post({ IsMessageMoveRequested: true, ReportAction: action });
      console.log(`Reported ${action} and moved to Junk Email (id ${String(id).slice(0, 20)}...).`);
      return;
    } catch (e) {
      if (action === 'junk') {
        await client.api(`/me/messages/${id}/move`).post({ destinationId: 'junkemail' });
        console.log(`reportMessage (beta) failed [${e.message}] — fell back to a plain move to Junk `
          + `Email (id ${String(id).slice(0, 20)}...). Filter was not retrained.`);
      }
    }
  }
}

async function unsubscribe(client) {
  // Unsubscribe from a marketing sender using its RFC 2369 List-Unsubscribe header (and RFC 8058
  // one-click where offered). Reserve this for mail addressed to the user alone — a shared list or
  // group he doesn't own should get a his-side archive rule instead, never an unsubscribe that drops
  // other people. Prefer an https one-click POST; fall back to a GET (which may need a confirmation
  // click on the landing page); a mailto-only sender is surfaced for the user to send himself, since
  // this tool never sends mail.
  const id = args['unsubscribe'];
  const msg = await client.api(`/me/messages/${id}`)
    .select('subject,internetMessageHeaders').get();
  const headers = msg.internetMessageHeaders || [];
  const get = name => (headers.find(h => h.name.toLowerCase() === name) || {}).value || '';
  const lu = get('list-unsubscribe');
  const lup = get('list-unsubscribe-post');
  console.log(`Subject: ${msg.subject}`);
  if (!lu) { console.log('No List-Unsubscribe header — use the sender footer link by hand.'); return; }
  const urls = [...lu.matchAll(/<([^>]+)>/g)].map(m => m[1]);
  const https = urls.find(u => /^https?:/i.test(u));
  const mailto = urls.find(u => /^mailto:/i.test(u));
  if (https && /one-click/i.test(lup)) {
    const r = await fetch(https, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'List-Unsubscribe=One-Click' });
    console.log(`One-click POST -> ${https}\n  HTTP ${r.status} ${r.statusText}`);
  } else if (https) {
    const r = await fetch(https, { method: 'GET', headers: { 'User-Agent': 'Mozilla/5.0' } });
    console.log(`GET -> ${https}\n  HTTP ${r.status} ${r.statusText} (may need a confirmation click)`);
  } else if (mailto) {
    console.log(`Only a mailto unsubscribe is offered — send it yourself: ${mailto}`);
  }
}

async function getAttachments(client) {
  const id = args['get-attachments'];
  const outDir = args['out-dir'] || '.';
  fs.mkdirSync(outDir, { recursive: true });
  const data = await client.api(`/me/messages/${id}/attachments`).get();
  const atts = data.value || [];
  if (!atts.length) { console.log('No attachments on this message.'); return []; }
  const saved = [];
  for (const a of atts) {
    if (a['@odata.type'] !== '#microsoft.graph.fileAttachment' || !a.contentBytes) {
      console.log(`Skipped (not a downloadable file attachment): ${a.name} [${a['@odata.type']}]`);
      continue;
    }
    const outPath = path.join(outDir, a.name);
    fs.writeFileSync(outPath, Buffer.from(a.contentBytes, 'base64'));
    saved.push(outPath);
    console.log(`Saved: ${outPath} (${a.contentType || 'unknown type'}, ${a.size} bytes)`);
  }
  return saved;
}

async function search(client) {
  const data = await client.api('/me/messages')
    .search(`"${args.search}"`)
    .top(parseInt(args.top || '10', 10))
    .select('id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,isDraft')
    .get();
  const msgs = data.value || [];
  if (!msgs.length) { console.log('No messages found.'); return; }
  for (const m of msgs) {
    const draftTag = m.isDraft ? '[DRAFT — NOT SENT] ' : '';
    console.log(`\n--- ${draftTag}${m.receivedDateTime?.slice(0, 16)}  |  ${m.subject}`);
    console.log(`    from: ${addr(m.from)}`);
    console.log(`    to:   ${(m.toRecipients || []).map(addr).join(', ')}`);
    if ((m.ccRecipients || []).length) console.log(`    cc:   ${m.ccRecipients.map(addr).join(', ')}`);
    console.log(`    id:   ${m.id}`);
    console.log(`    > ${(m.bodyPreview || '').replace(/\s+/g, ' ').slice(0, 200)}`);
  }
}

async function show(client) {
  const m = await client.api(`/me/messages/${args.show}`)
    .select('subject,from,toRecipients,ccRecipients,receivedDateTime,body,isDraft').get();
  if (m.isDraft) console.log('*** DRAFT — NOT SENT ***');
  console.log(`Subject: ${m.subject}`);
  console.log(`From: ${addr(m.from)}`);
  console.log(`To: ${(m.toRecipients || []).map(addr).join(', ')}`);
  if ((m.ccRecipients || []).length) console.log(`Cc: ${m.ccRecipients.map(addr).join(', ')}`);
  if (args.html) { console.log('\n' + (m.body?.content || '')); return; }
  console.log('\n' + strip(m.body?.content));
}

async function auth(client) {
  // The envelope-authentication headers the receiving system stamped onto the message, as JSON for the
  // drainer's security screen. Authentication-Results carries the SPF/DKIM/DMARC verdicts (and the
  // domains that actually authenticated); Received-SPF carries the SPF check on its own. Both can appear
  // more than once (each relay adds its own), so every instance is returned. internetMessageHeaders is
  // the same header source --unsubscribe already reads. Interpretation lives in the drainer's Python
  // adapters, so this just exposes the raw values.
  const m = await client.api(`/me/messages/${args.auth}`)
    .select('from,internetMessageHeaders').get();
  const headers = m.internetMessageHeaders || [];
  const all = (name) => headers
    .filter(h => (h.name || '').toLowerCase() === name)
    .map(h => h.value).filter(Boolean);
  console.log(JSON.stringify({
    from: addr(m.from),
    fromAddress: m.from?.emailAddress?.address || '',
    authenticationResults: all('authentication-results'),
    receivedSpf: all('received-spf'),
  }, null, 2));
}

async function listDrafts(client) {
  const data = await client.api('/me/mailFolders/drafts/messages')
    .orderby('lastModifiedDateTime desc')
    .top(parseInt(args.top || '30', 10))
    .select('id,subject,toRecipients,lastModifiedDateTime,bodyPreview')
    .get();
  const msgs = data.value || [];
  if (!msgs.length) { console.log('No drafts.'); return; }
  console.log(`${msgs.length} draft(s), most-recently-edited first:`);
  for (const m of msgs) {
    console.log(`\n--- ${m.lastModifiedDateTime?.slice(0, 16)}  |  ${m.subject}`);
    console.log(`    to: ${(m.toRecipients || []).map(addr).join(', ')}`);
    console.log(`    id: ${m.id}`);
    console.log(`    > ${(m.bodyPreview || '').replace(/\s+/g, ' ').slice(0, 200)}`);
  }
}

// Reusable: normalize an --attach value (path, comma-separated string, or array) into Graph
// fileAttachment objects.
function buildAttachments(attach) {
  const attachPaths = Array.isArray(attach)
    ? attach
    : (attach ? String(attach).split(',').map(s => s.trim()).filter(Boolean) : []);
  return attachPaths.map(p => ({
    '@odata.type': '#microsoft.graph.fileAttachment',
    name: path.basename(p),
    contentBytes: fs.readFileSync(p).toString('base64'),
  }));
}

async function reply(client) {
  if (!args['message-id'] || !args['body-file']) {
    throw new Error('--reply requires --message-id and --body-file');
  }
  const raw = fs.readFileSync(args['body-file'], 'utf8');
  const draft = await client.api(`/me/messages/${args['message-id']}/createReplyAll`).post({});
  const quoted = draft.body?.content || '';
  await client.api(`/me/messages/${draft.id}`).patch({ body: { contentType: 'html', content: renderBody(raw) + quoted } });
  const attachments = buildAttachments(args.attach);
  for (const a of attachments) {
    await client.api(`/me/messages/${draft.id}/attachments`).post(a);
  }
  const extra = attachments.length ? ` with ${attachments.length} attachment(s)` : '';
  console.log(`Draft reply created in thread${extra} (id ${draft.id.slice(0, 20)}...). Review in Outlook Drafts.`);
}

// Reusable: create a draft message (never sends). Returns the created draft.
//   createDraft(client, { to, subject, body, cc, attach, replace, contentType })
//   - attach: a path or comma-separated string or array of paths -> file attachments
//   - replace: delete any existing drafts with the same subject first (avoids duplicates)
async function createDraft(client, { to, subject, body = '', cc, attach, replace = false, contentType = 'html' } = {}) {
  if (!to || !subject) throw new Error('createDraft requires { to, subject }');
  client = client || await getGraphClient();
  if (replace) {
    const existing = await client.api('/me/mailFolders/drafts/messages')
      .filter(`subject eq '${String(subject).replace(/'/g, "''")}'`)
      .select('id,subject').top(20).get();
    for (const m of existing.value || []) await client.api(`/me/messages/${m.id}`).delete();
  }
  const recip = (s) => String(s).split(',').map(a => ({ emailAddress: { address: a.trim() } }));
  const finalBody = contentType === 'html' ? renderBody(body) : body;
  const message = { subject, body: { contentType, content: finalBody }, toRecipients: recip(to) };
  if (cc) message.ccRecipients = recip(cc);
  const attachments = buildAttachments(attach);
  if (attachments.length) message.attachments = attachments;
  return client.api('/me/messages').post(message);
}

async function draftNew(client) {
  if (!args.to || !args.subject || !args['body-file']) {
    throw new Error('--draft-new requires --to, --subject, and --body-file');
  }
  const body = fs.readFileSync(args['body-file'], 'utf8');
  const draft = await createDraft(client, {
    to: args.to, subject: args.subject, body, cc: args.cc,
    attach: args.attach, replace: !!args.replace, contentType: args.text ? 'text' : 'html',
  });
  const extra = args.attach ? ' with attachment' : '';
  console.log(`Draft created to ${args.to} (id ${draft.id.slice(0, 20)}...)${extra}. Review in Outlook Drafts; never sent.`);
}

async function sendDraft(client) {
  // Promotes ONE staged draft to a real send via Graph's own /send action on the message — it
  // transmits the draft's saved recipients/body exactly as staged, then Graph auto-files the sent
  // copy in Sent Items and removes it from Drafts. This is the only path that emits real mail —
  // gated upstream on Russ's explicit per-message instruction after he reviewed this exact draft.
  const id = args['message-id'];
  if (!id || id === true) throw new Error('--send-draft requires --message-id (the draft id printed when it was staged)');
  await client.api(`/me/messages/${id}/send`).post({});
  console.log(`Sent (id ${String(id).slice(0, 20)}...). Moved from Drafts to Sent Items.`);
}

async function sendSelf(client) {
  if (!args.subject || !args['body-file']) {
    throw new Error('--send-self requires --subject and --body-file');
  }
  const me = await client.api('/me').select('mail,userPrincipalName').get();
  const me_addr = me.mail || me.userPrincipalName;
  const content = fs.readFileSync(args['body-file'], 'utf8');
  await client.api('/me/sendMail').post({
    message: {
      subject: args.subject,
      body: { contentType: 'text', content },
      toRecipients: [{ emailAddress: { address: me_addr } }],
    },
    saveToSentItems: true,
  });
  console.log(`Sent to self (${me_addr}): "${args.subject}"`);
}

// --- Inbox rules (server-side filters) ---
// Multi-value fields are split on `||`, so a whole bucket goes in one flag:
//   --subject-contains="One Time Passcode||Your shipment was delivered||Accepted:"
const splitList = (v) => (v && v !== true)
  ? String(v).split('||').map(s => s.trim()).filter(Boolean) : undefined;

async function listRules(client) {
  const data = await client.api('/me/mailFolders/inbox/messageRules').get();
  const rules = data.value || [];
  if (args.json) { console.log(JSON.stringify(rules, null, 2)); return; }
  if (!rules.length) { console.log('No inbox rules.'); return; }
  console.log(`${rules.length} inbox rule(s), in run order:`);
  for (const r of rules) {
    console.log(`\n--- [${r.sequence}] ${r.displayName}${r.isEnabled ? '' : '  (disabled)'}`);
    console.log(`    id:     ${r.id}`);
    if (r.conditions) console.log(`    when:   ${JSON.stringify(r.conditions)}`);
    if (r.exceptions) console.log(`    except: ${JSON.stringify(r.exceptions)}`);
    if (r.actions) console.log(`    do:     ${JSON.stringify(r.actions)}`);
  }
}

// Build a conditions/exceptions/actions rule body from CLI flags.
function ruleBodyFromArgs() {
  const conditions = {};
  const subj = splitList(args['subject-contains']); if (subj) conditions.subjectContains = subj;
  const body = splitList(args['body-contains']); if (body) conditions.bodyContains = body;
  const from = splitList(args['from-contains']); if (from) conditions.senderContains = from;
  const subjOrBody = splitList(args['subject-or-body']); if (subjOrBody) conditions.subjectOrBodyContains = subjOrBody;
  const exceptions = {};
  const exc = splitList(args['except-from']); if (exc) exceptions.senderContains = exc;
  const actions = {};
  if (args['move-to']) actions.moveToFolder = args['move-to']; // well-known id: 'archive', 'deleteditems'
  if (args['mark-read']) actions.markAsRead = true;
  if (args['delete-msg']) actions.delete = true;
  actions.stopProcessingRules = args['no-stop'] ? false : true;
  return { conditions, exceptions, actions };
}

async function createRule(client) {
  if (!args.name) throw new Error('--create-rule requires --name');
  const { conditions, exceptions, actions } = ruleBodyFromArgs();
  if (!conditions.subjectContains && !conditions.bodyContains && !conditions.senderContains && !conditions.subjectOrBodyContains) {
    throw new Error('need a condition: --subject-contains, --body-contains, --subject-or-body, or --from-contains');
  }
  if (!actions.moveToFolder && !actions.markAsRead && !actions.delete) {
    throw new Error('need an action: --move-to=archive, --mark-read, or --delete-msg');
  }
  // Graph requires a positive sequence (run-order position); append after the last rule.
  const existing = await client.api('/me/mailFolders/inbox/messageRules').select('sequence').get();
  const maxSeq = (existing.value || []).reduce((m, r) => Math.max(m, r.sequence || 0), 0);
  const sequence = args.sequence ? parseInt(args.sequence, 10) : maxSeq + 1;
  const rule = { displayName: args.name, sequence, isEnabled: true, conditions, actions };
  if (Object.keys(exceptions).length) rule.exceptions = exceptions;
  const created = await client.api('/me/mailFolders/inbox/messageRules').post(rule);
  console.log(`Created rule "${created.displayName}" (id ${created.id}, sequence ${created.sequence}).`);
}

async function findRule(client, idOrName) {
  const data = await client.api('/me/mailFolders/inbox/messageRules').get();
  return (data.value || []).find(r => r.id === idOrName || r.displayName === idOrName);
}

// Append subject/body phrases to an existing rule's conditions — the "add a phrase to the bucket" op.
async function appendRule(client) {
  const rule = await findRule(client, args['append-rule']);
  if (!rule) throw new Error(`rule not found (by id or name): ${args['append-rule']}`);
  const conditions = rule.conditions || {};
  const addSubj = splitList(args['subject-contains']);
  const addBody = splitList(args['body-contains']);
  if (!addSubj && !addBody) throw new Error('--append-rule needs --subject-contains and/or --body-contains');
  if (addSubj) conditions.subjectContains = [...(conditions.subjectContains || []), ...addSubj];
  if (addBody) conditions.bodyContains = [...(conditions.bodyContains || []), ...addBody];
  await client.api(`/me/mailFolders/inbox/messageRules/${rule.id}`).patch({ conditions });
  console.log(`Appended to "${rule.displayName}": subjectContains=${(conditions.subjectContains || []).length}, bodyContains=${(conditions.bodyContains || []).length}.`);
}

async function deleteRule(client) {
  const id = args['delete-rule'];
  const rule = await findRule(client, id);
  const target = rule ? rule.id : id;
  await client.api(`/me/mailFolders/inbox/messageRules/${target}`).delete();
  console.log(`Deleted rule ${rule ? `"${rule.displayName}"` : id}.`);
}

module.exports = { createDraft };

// --- CLI (only when run directly, so `require` of this file is side-effect-free) ---
if (require.main === module) {
  (async () => {
    const client = await getGraphClient();
    if (args['list-unread']) return listUnread(client);
    if (args['list-inbox']) return listInbox(client);
    if (args['list-junk']) return listJunk(client);
    if (args['list-drafts']) return listDrafts(client);
    if (args.search) return search(client);
    if (args.show) return show(client);
    if (args.auth) return auth(client);
    if (args.reply) return reply(client);
    if (args['draft-new']) return draftNew(client);
    if (args['send-self']) return sendSelf(client);
    if (args['send-draft']) return sendDraft(client);
    if (args.delete) return del(client);
    if (args['not-junk']) return notJunk(client);
    if (args['report-phish']) return reportPhish(client);
    if (args.unsubscribe) return unsubscribe(client);
    if (args['get-attachments']) return getAttachments(client);
    if (args['list-rules']) return listRules(client);
    if (args['create-rule']) return createRule(client);
    if (args['append-rule']) return appendRule(client);
    if (args['delete-rule']) return deleteRule(client);
    throw new Error('Specify --list-unread, --list-inbox, --list-junk, --list-drafts, --search, --show, --auth, --reply, --draft-new, --send-self, --send-draft, --delete, --not-junk, --report-phish, --unsubscribe, --get-attachments, --list-rules, --create-rule, --append-rule, or --delete-rule');
  })().catch(e => { console.error('Error:', e.message); process.exit(1); });
}
