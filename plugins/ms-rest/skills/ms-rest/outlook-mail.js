#!/usr/bin/env node
// outlook-mail.js - fast Outlook (WORK account) mailbox operations: read, delete, compose.
//
// Owned by the `ms-rest` skill. Calls the Outlook REST API v2.0
// (https://outlook.office.com/api/v2.0) directly. Reading is a single paged REST sweep instead of
// driving the web UI; composing creates server-side drafts that land un-sent in the Drafts folder.
//
// TOKEN: Outlook web (outlook.cloud.microsoft) holds a bearer token whose audience is
// https://outlook.office.com with Mail.ReadWrite/Mail.Send scopes. We sniff that token off the
// live browser session via CDP and cache it at .tmp/outlook-token.json with its real JWT expiry
// (typically 1-2 days). While the cached token is valid, commands run with no browser at all;
// the browser is opened only to (re)sniff when the cache is missing/expired or a call returns 401.
// (Target the Outlook REST API because the graph.microsoft.com token Outlook holds carries no
// Mail scopes.) See SKILL.md for the full token + endpoint rationale.
//
// This is the WORK account. For Russell's PERSONAL Microsoft account use the `ms-graph` skill
// (MSAL + Graph SDK) - a different auth path and a different mailbox.
//
// REQUIREMENT for a (re)sniff: Russell signed in to Outlook web in the CDP browser (port 9222).
// AUTH-GLANCE in SKILL.md establishes that sign-in.
//
// Usage:
//   node outlook-mail.js enumerate               -> JSON array of ALL inbox messages, newest-first
//   node outlook-mail.js get <id>                -> full message JSON (body, recipients, webLink)
//   node outlook-mail.js delete <id>             -> move to Archive (reversible, keeps it searchable)
//   node outlook-mail.js token [--force]         -> ensure/refresh cached token, print status
//   node outlook-mail.js create-draft --json <p> -> new draft email; prints {id, webLink}
//   node outlook-mail.js create-reply <id> --json <p> -> reply draft (quoted original below); prints {id, webLink}
//   node outlook-mail.js send-draft <id> --yes   -> SEND an existing draft (gated; needs --yes)
//
// Compose inputs come from a JSON file (--json <path>) so HTML bodies avoid shell escaping:
//   create-draft: { "subject": "...", "body": "<html>", "to": ["a@b.com"], "cc": [], "bodyType": "HTML" }
//   create-reply: { "comment": "<html new text>" }   // placed ABOVE the quoted original
//
// Default is DRAFT-ONLY: create-draft/create-reply never send. Sending requires send-draft --yes.
// All commands auto-acquire/refresh the token. JSON goes to stdout (except `token`, a status line).
// Errors go to stderr with a non-zero exit.

const fs = require('fs');
// Token + REST plumbing (sniff, cache, 401-refresh, 410-guard) lives in the shared core module so
// mail and calendar share exactly one auth path.
const { getToken, apiCall, enc } = require('./outlook-core');

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

const PAGE_SIZE = 100;     // per-request page; enumerate follows nextLink until exhausted

// Normalize a recipient (string address or {address,name}) to the REST shape.
function toRecipient(r) {
  if (typeof r === 'string') return { EmailAddress: { Address: r } };
  return { EmailAddress: { Address: r.address || r.Address, Name: r.name || r.Name } };
}

// --- Commands ----------------------------------------------------------------
// Enumerate the WHOLE inbox, following @odata.nextLink so nothing is left behind.
async function cmdEnumerate() {
  const select = 'Id,Subject,From,ReceivedDateTime,BodyPreview,IsRead,WebLink,HasAttachments';
  let urlPath = `/me/mailfolders/inbox/messages?$top=${PAGE_SIZE}&$select=${select}&$orderby=ReceivedDateTime%20desc`;
  const out = [];
  while (urlPath) {
    const res = await apiCall('GET', urlPath);
    if (res.status !== 200) throw new Error(`enumerate HTTP ${res.status}: ${res.body.slice(0, 400)}`);
    const json = JSON.parse(res.body);
    for (const m of (json.value || [])) {
      const ea = (m.From || {}).EmailAddress || {};
      out.push({
        id: m.Id,
        from: { name: ea.Name || '', address: ea.Address || '' },
        subject: m.Subject || '',
        received: m.ReceivedDateTime,
        preview: m.BodyPreview || '',
        isRead: m.IsRead,
        hasAttachments: m.HasAttachments,
        webLink: m.WebLink,
      });
    }
    urlPath = json['@odata.nextLink'] || null;
  }
  process.stdout.write(JSON.stringify(out, null, 2) + '\n');
}

async function cmdGet(id) {
  const select = 'Id,Subject,From,ToRecipients,CcRecipients,ReceivedDateTime,Body,BodyPreview,WebLink,HasAttachments';
  const res = await apiCall('GET', `/me/messages/${enc(id)}?$select=${select}`);
  if (res.status !== 200) throw new Error(`get HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  const m = JSON.parse(res.body);
  const addr = (r) => ((r || {}).EmailAddress || {});
  const out = {
    id: m.Id,
    from: addr(m.From),
    to: (m.ToRecipients || []).map(addr),
    cc: (m.CcRecipients || []).map(addr),
    subject: m.Subject || '',
    received: m.ReceivedDateTime,
    bodyType: (m.Body || {}).ContentType,
    body: (m.Body || {}).Content || '',
    preview: m.BodyPreview || '',
    webLink: m.WebLink,
    hasAttachments: m.HasAttachments,
  };
  process.stdout.write(JSON.stringify(out, null, 2) + '\n');
}

async function cmdDelete(id) {
  const res = await apiCall('POST', `/me/messages/${enc(id)}/move`, { DestinationId: 'archive' });
  if (res.status !== 201 && res.status !== 200) throw new Error(`delete HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  process.stdout.write(JSON.stringify({ deleted: id, movedTo: 'Archive' }) + '\n');
}

function readJsonArg(p) {
  if (!p) throw new Error('missing --json <path> (the draft/reply payload file)');
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

// create-draft: POST a new message. With no send, it lands in Drafts (IsDraft=true).
async function cmdCreateDraft(jsonPath) {
  const spec = readJsonArg(jsonPath);
  if (!spec.body) throw new Error('create-draft payload needs a "body" (HTML or text)');
  const bodyType = spec.bodyType || 'HTML';
  const msg = {
    Subject: spec.subject || '',
    Body: { ContentType: bodyType, Content: bodyType === 'HTML' ? withDefaultFont(spec.body) : spec.body },
    ToRecipients: (spec.to || []).map(toRecipient),
    CcRecipients: (spec.cc || []).map(toRecipient),
  };
  const res = await apiCall('POST', '/me/messages', msg);
  if (res.status !== 201 && res.status !== 200) throw new Error(`create-draft HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  const m = JSON.parse(res.body);
  process.stdout.write(JSON.stringify({ draftId: m.Id, webLink: m.WebLink, folder: 'Drafts', sent: false }) + '\n');
}

// create-reply: createreply yields a draft with the quoted original; we prepend our comment HTML on
// top so the new text sits above the quoted original (Russell's reply-draft format rule).
async function cmdCreateReply(id, jsonPath) {
  if (!id) throw new Error('create-reply needs the source message <id>');
  const spec = readJsonArg(jsonPath);
  const comment = spec.comment || spec.body || '';
  if (!comment) throw new Error('create-reply payload needs a "comment" (HTML new text)');

  const mk = await apiCall('POST', `/me/messages/${enc(id)}/createreply`, {});
  if (mk.status !== 201 && mk.status !== 200) throw new Error(`createreply HTTP ${mk.status}: ${mk.body.slice(0, 400)}`);
  const draft = JSON.parse(mk.body);

  // Fetch the draft body (the quoted original) and prepend our new text.
  const got = await apiCall('GET', `/me/messages/${enc(draft.Id)}?$select=Id,Body,WebLink`);
  if (got.status !== 200) throw new Error(`create-reply read-back HTTP ${got.status}: ${got.body.slice(0, 400)}`);
  const d = JSON.parse(got.body);
  const original = (d.Body || {}).Content || '';
  const merged = `${withDefaultFont(comment)}${original}`;

  const patch = await apiCall('PATCH', `/me/messages/${enc(draft.Id)}`,
    { Body: { ContentType: 'HTML', Content: merged } });
  if (patch.status !== 200) throw new Error(`create-reply patch HTTP ${patch.status}: ${patch.body.slice(0, 400)}`);
  process.stdout.write(JSON.stringify({ draftId: draft.Id, webLink: d.WebLink, folder: 'Drafts', sent: false }) + '\n');
}

// send-draft: SENDS an existing draft. Gated - refuses without --yes. Sending mail is in the
// "requires Russell's OK" set; default everywhere else is draft-only.
async function cmdSendDraft(id, confirmed) {
  if (!id) throw new Error('send-draft needs the draft <id>');
  if (!confirmed) throw new Error('send-draft refuses without --yes (sending mail requires explicit OK; default is draft-only)');
  const res = await apiCall('POST', `/me/messages/${enc(id)}/send`, undefined);
  if (res.status !== 202 && res.status !== 200) throw new Error(`send-draft HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  process.stdout.write(JSON.stringify({ sent: id }) + '\n');
}

async function cmdToken(force) {
  const meta = await getToken(force);
  process.stdout.write(`Token OK ✅  aud=${meta.aud} exp=${meta.expISO}\n`);
}

// --- Main --------------------------------------------------------------------
(async () => {
  const [cmd, ...rest] = process.argv.slice(2);
  const has = (name) => rest.includes(name);
  const flagVal = (name) => { const i = rest.indexOf(name); return i >= 0 ? rest[i + 1] : undefined; };
  const positional = rest.filter((a, i) => !a.startsWith('--') && rest[i - 1] !== '--json');
  try {
    switch (cmd) {
      case 'enumerate': await cmdEnumerate(); break;
      case 'get': await cmdGet(positional[0]); break;
      case 'delete': await cmdDelete(positional[0]); break;
      case 'token': await cmdToken(has('--force')); break;
      case 'create-draft': await cmdCreateDraft(flagVal('--json')); break;
      case 'create-reply': await cmdCreateReply(positional[0], flagVal('--json')); break;
      case 'send-draft': await cmdSendDraft(positional[0], has('--yes')); break;
      default:
        process.stderr.write('Usage: outlook-mail.js <enumerate|get <id>|delete <id>|token|'
          + 'create-draft --json <p>|create-reply <id> --json <p>|send-draft <id> --yes> [--force]\n');
        process.exit(1);
    }
  } catch (e) {
    process.stderr.write('ERROR: ' + (e && e.message || e) + '\n');
    process.exit(2);
  }
})();
