// Google Drive + Slides operations via the official `googleapis` client (OAuth path - see
// google-drive-oauth.js). Every command accepts --account=<name> to act on a named account instead of the
// default one, and --json for machine-readable output where it prints a listing.
//
// Usage: see ../SKILL.md "Commands".

const fs = require('fs');
const path = require('path');

const { google } = require('googleapis');
const { getAuthedClient, assertAccountEmail, fetchAccountEmail } = require('./google-drive-oauth');

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);

const EMU_PER_PT = 12700;
const FILE_FIELDS = 'id, name, mimeType, parents, modifiedTime, webViewLink';
const ALL_DRIVES = { supportsAllDrives: true };
const MIME_BY_EXT = {
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
  '.webp': 'image/webp', '.heic': 'image/heic', '.pdf': 'application/pdf',
};

function need(name) {
  if (!args[name] || args[name] === true) throw new Error(`--${name}=<value> required`);
  return args[name];
}

function quoteQ(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function printFiles(files) {
  if (args.json) { console.log(JSON.stringify(files, null, 2)); return; }
  for (const f of files) console.log(`${f.modifiedTime || ''}  ${f.id}  ${f.mimeType.replace('application/vnd.google-apps.', 'g:')}  ${f.name}`);
  if (!files.length) console.log('(no files)');
}

// ---- Drive ----

async function listFiles(drive) {
  const clauses = ['trashed = false'];
  if (args.folder) clauses.push(`'${quoteQ(args.folder)}' in parents`);
  if (args.name) clauses.push(`name contains '${quoteQ(args.name)}'`);
  const res = await drive.files.list({
    q: clauses.join(' and '),
    orderBy: args.folder || args.name ? 'name' : 'modifiedTime desc',
    pageSize: Number(args.top || 50),
    fields: `files(${FILE_FIELDS})`,
    includeItemsFromAllDrives: true, ...ALL_DRIVES,
  });
  return res.data.files || [];
}

async function fileInfo(drive, fileId) {
  const res = await drive.files.get({ fileId, fields: FILE_FIELDS, ...ALL_DRIVES });
  const file = res.data;
  file.parentFolders = [];
  for (const pid of file.parents || []) {
    const p = await drive.files.get({ fileId: pid, fields: 'id, name', ...ALL_DRIVES });
    file.parentFolders.push(p.data);
  }
  return file;
}

async function copyFile(drive, fileId, title, folderId) {
  const parents = folderId ? [folderId] : (await drive.files.get({ fileId, fields: 'parents', ...ALL_DRIVES })).data.parents;
  const res = await drive.files.copy({ fileId, fields: FILE_FIELDS, requestBody: { name: title, parents }, ...ALL_DRIVES });
  return res.data;
}

async function shareLink(drive, fileId, role) {
  if (!['reader', 'writer', 'commenter'].includes(role)) throw new Error('--role must be reader, commenter, or writer');
  const perm = await drive.permissions.create({ fileId, requestBody: { type: 'anyone', role, allowFileDiscovery: false }, fields: 'id', ...ALL_DRIVES });
  const file = await drive.files.get({ fileId, fields: 'webViewLink', ...ALL_DRIVES });
  return { permissionId: perm.data.id, webViewLink: file.data.webViewLink };
}

async function upload(drive, localPath, folderId, title) {
  const ext = path.extname(localPath).toLowerCase();
  const mimeType = MIME_BY_EXT[ext] || 'application/octet-stream';
  const res = await drive.files.create({
    requestBody: { name: title || path.basename(localPath), parents: folderId ? [folderId] : undefined },
    media: { mimeType, body: fs.createReadStream(localPath) },
    fields: FILE_FIELDS, ...ALL_DRIVES,
  });
  return res.data;
}

// ---- Slides ----

function textOf(shape) {
  const els = (shape && shape.text && shape.text.textElements) || [];
  return els.map(e => (e.textRun && e.textRun.content) || '').join('');
}

function boxOf(el) {
  const t = el.transform || {};
  const unitToPt = (u) => (u === 'PT' ? 1 : 1 / EMU_PER_PT);
  const w = el.size ? el.size.width.magnitude * (t.scaleX ?? 1) * unitToPt(el.size.width.unit) : null;
  const h = el.size ? el.size.height.magnitude * (t.scaleY ?? 1) * unitToPt(el.size.height.unit) : null;
  const k = unitToPt(t.unit);
  const r = (n) => (n == null ? null : Math.round(n * 10) / 10);
  return { x: r((t.translateX || 0) * k), y: r((t.translateY || 0) * k), width: r(w), height: r(h) };
}

function describeElement(el) {
  const kind = el.shape ? 'shape' : el.image ? 'image' : el.table ? 'table' : el.elementGroup ? 'group' : el.video ? 'video' : el.line ? 'line' : 'other';
  const out = { objectId: el.objectId, kind, box: boxOf(el) };
  if (el.shape) {
    out.shapeType = el.shape.shapeType;
    if (el.shape.placeholder) out.placeholder = el.shape.placeholder.type;
    const txt = textOf(el.shape).trim();
    if (txt) out.text = txt;
  }
  return out;
}

function slideTitle(slide) {
  const els = slide.pageElements || [];
  const titled = els.find(e => e.shape && e.shape.placeholder && ['TITLE', 'CENTERED_TITLE'].includes(e.shape.placeholder.type) && textOf(e.shape).trim());
  const first = titled || els.find(e => e.shape && textOf(e.shape).trim());
  return first ? textOf(first.shape).trim() : '';
}

function pickSlide(pres) {
  const slides = pres.slides || [];
  if (args['slide-index'] !== undefined) {
    const s = slides[Number(args['slide-index'])];
    if (!s) throw new Error(`No slide at index ${args['slide-index']} (deck has ${slides.length})`);
    return s;
  }
  const want = need('slide-title').trim().toLowerCase();
  const exact = slides.filter(s => slideTitle(s).toLowerCase() === want);
  const loose = exact.length ? exact : slides.filter(s => (s.pageElements || []).some(e => e.shape && textOf(e.shape).toLowerCase().includes(want)));
  if (loose.length !== 1) throw new Error(`--slide-title="${args['slide-title']}" matched ${loose.length} slides; pass a more specific title or --slide-index`);
  return loose[0];
}

async function readDeck(slidesApi, deckId) {
  return (await slidesApi.presentations.get({ presentationId: deckId })).data;
}

async function insertImage(drive, slidesApi, deckId) {
  const pres = await readDeck(slidesApi, deckId);
  const slide = pickSlide(pres);

  let imageId = args['image-file'];
  if (args['image-path']) {
    const folder = args.folder || ((await drive.files.get({ fileId: deckId, fields: 'parents', ...ALL_DRIVES })).data.parents || [])[0];
    imageId = (await upload(drive, args['image-path'], folder)).id;
    console.log(`Uploaded ${args['image-path']} -> ${imageId}`);
  }
  if (!imageId || imageId === true) throw new Error('--image-path=<local file> or --image-file=<drive id> required');

  let box;
  if (args['box-shape']) {
    const el = (slide.pageElements || []).find(e => e.objectId === args['box-shape']);
    if (!el) throw new Error(`No element ${args['box-shape']} on that slide`);
    box = boxOf(el);
  } else if (args.width && args.height) {
    box = { x: Number(args.x || 0), y: Number(args.y || 0), width: Number(args.width), height: Number(args.height) };
  } else {
    const pw = pres.pageSize.width.magnitude / EMU_PER_PT;
    const ph = pres.pageSize.height.magnitude / EMU_PER_PT;
    box = { x: pw * 0.3, y: ph * 0.3, width: pw * 0.4, height: ph * 0.4 };
  }

  // The Slides API fetches an image only from a URL it can reach anonymously. Grant link access just long
  // enough for the insert - Slides stores its own copy - then remove it, unless the file already had it.
  const perms = (await drive.permissions.list({ fileId: imageId, fields: 'permissions(id,type)', ...ALL_DRIVES })).data.permissions || [];
  const alreadyPublic = perms.some(p => p.type === 'anyone');
  let tempPermId = null;
  if (!alreadyPublic) {
    tempPermId = (await drive.permissions.create({ fileId: imageId, requestBody: { type: 'anyone', role: 'reader' }, fields: 'id', ...ALL_DRIVES })).data.id;
  }
  try {
    const res = await slidesApi.presentations.batchUpdate({
      presentationId: deckId,
      requestBody: {
        requests: [{
          createImage: {
            url: `https://drive.google.com/uc?export=download&id=${imageId}`,
            elementProperties: {
              pageObjectId: slide.objectId,
              size: { width: { magnitude: box.width, unit: 'PT' }, height: { magnitude: box.height, unit: 'PT' } },
              transform: { scaleX: 1, scaleY: 1, translateX: box.x, translateY: box.y, unit: 'PT' },
            },
          },
        }],
      },
    });
    return { slide: slide.objectId, imageObjectId: res.data.replies[0].createImage.objectId, box };
  } finally {
    if (tempPermId) await drive.permissions.delete({ fileId: imageId, permissionId: tempPermId, ...ALL_DRIVES });
  }
}

async function setText(slidesApi, deckId) {
  const pres = await readDeck(slidesApi, deckId);
  const slide = pickSlide(pres);
  const els = slide.pageElements || [];
  let el;
  if (args.shape) {
    el = els.find(e => e.objectId === args.shape);
  } else {
    const type = need('placeholder').toUpperCase();
    const matches = els.filter(e => e.shape && e.shape.placeholder && e.shape.placeholder.type === type);
    if (matches.length > 1) throw new Error(`${matches.length} ${type} placeholders on that slide; pick one with --shape=<element id> (see --slides-read)`);
    el = matches[0];
  }
  if (!el || !el.shape) throw new Error('Target shape not found on that slide (see --slides-read for element ids)');

  const text = args['text-file'] ? fs.readFileSync(args['text-file'], 'utf8').replace(/\s+$/, '') : need('text');
  const existing = textOf(el.shape);
  const hasText = existing.trim().length > 0;
  const requests = [];
  if (args.append && hasText) {
    // Shape text always ends in a paragraph-closing newline; insert just before it as a new paragraph.
    requests.push({ insertText: { objectId: el.objectId, insertionIndex: existing.length - 1, text: '\n' + text } });
  } else {
    if (hasText) requests.push({ deleteText: { objectId: el.objectId, textRange: { type: 'ALL' } } });
    requests.push({ insertText: { objectId: el.objectId, insertionIndex: 0, text } });
  }
  await slidesApi.presentations.batchUpdate({ presentationId: deckId, requestBody: { requests } });
  return { slide: slide.objectId, shape: el.objectId, mode: args.append && hasText ? 'appended' : 'replaced' };
}

(async () => {
  const auth = getAuthedClient();
  await assertAccountEmail(auth);
  const drive = google.drive({ version: 'v3', auth });
  const slidesApi = google.slides({ version: 'v1', auth });

  if (args.whoami) {
    console.log(await fetchAccountEmail(auth));
  } else if (args.list) {
    printFiles(await listFiles(drive));
  } else if (args.info) {
    console.log(JSON.stringify(await fileInfo(drive, need('file')), null, 2));
  } else if (args.copy) {
    const f = await copyFile(drive, need('file'), need('title'), args.folder);
    console.log(args.json ? JSON.stringify(f, null, 2) : `Copied -> ${f.id}  ${f.name}\n${f.webViewLink}`);
  } else if (args['share-link']) {
    const r = await shareLink(drive, need('file'), need('role'));
    console.log(args.json ? JSON.stringify(r, null, 2) : `Anyone with the link: ${args.role}\n${r.webViewLink}`);
  } else if (args.upload) {
    const f = await upload(drive, need('path'), args.folder, args.title);
    console.log(args.json ? JSON.stringify(f, null, 2) : `Uploaded -> ${f.id}  ${f.name}`);
  } else if (args.trash) {
    const f = (await drive.files.update({ fileId: need('file'), requestBody: { trashed: true }, fields: 'id, name', ...ALL_DRIVES })).data;
    console.log(`Trashed ${f.id}  ${f.name}`);
  } else if (args['slides-read']) {
    const pres = await readDeck(slidesApi, need('deck'));
    const out = (pres.slides || []).map((s, i) => ({ index: i, objectId: s.objectId, title: slideTitle(s), elements: (s.pageElements || []).map(describeElement) }));
    if (args.json) { console.log(JSON.stringify({ title: pres.title, slides: out }, null, 2)); return; }
    console.log(`${pres.title}  (${out.length} slides)`);
    for (const s of out) {
      console.log(`\n[${s.index}] ${s.objectId}  "${s.title}"`);
      for (const e of s.elements) {
        const b = e.box;
        const label = [e.kind, e.placeholder, e.shapeType].filter(Boolean).join('/');
        const txt = e.text ? `  "${e.text.replace(/\s+/g, ' ').slice(0, 80)}"` : '';
        console.log(`    ${e.objectId}  ${label}  @(${b.x},${b.y}) ${b.width}x${b.height}pt${txt}`);
      }
    }
  } else if (args['slides-insert-image']) {
    console.log(JSON.stringify(await insertImage(drive, slidesApi, need('deck')), null, 2));
  } else if (args['slides-set-text']) {
    console.log(JSON.stringify(await setText(slidesApi, need('deck')), null, 2));
  } else {
    throw new Error('Nothing to do - pass a command (see the header of google-drive.js)');
  }
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
