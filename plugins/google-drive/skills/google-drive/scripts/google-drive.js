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

async function shareWithPerson(drive, fileId, email, role) {
  if (!['reader', 'writer', 'commenter'].includes(role)) throw new Error('--role must be reader, commenter, or writer');
  const perm = await drive.permissions.create({
    fileId, requestBody: { type: 'user', role, emailAddress: email },
    sendNotificationEmail: !args['no-notify'], fields: 'id, role, emailAddress', ...ALL_DRIVES,
  });
  const file = await drive.files.get({ fileId, fields: 'webViewLink', ...ALL_DRIVES });
  return { permissionId: perm.data.id, role: perm.data.role, emailAddress: perm.data.emailAddress, webViewLink: file.data.webViewLink };
}

async function listPermissions(drive, fileId) {
  const res = await drive.permissions.list({ fileId, fields: 'permissions(id, type, role, emailAddress, displayName)', ...ALL_DRIVES });
  return res.data.permissions || [];
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

// A Google Doc's content goes in and out as Markdown: Drive converts the uploaded Markdown into a Doc
// (headings, lists, bold, italic, links), so create and edit share one format and one conversion.
const DOC_MIME = 'application/vnd.google-apps.document';

function markdownMedia(localPath) {
  return { mimeType: 'text/markdown', body: fs.createReadStream(localPath) };
}

async function createDoc(drive, localPath, folderId, title) {
  const res = await drive.files.create({
    requestBody: { name: title, mimeType: DOC_MIME, parents: folderId ? [folderId] : undefined },
    media: markdownMedia(localPath),
    fields: FILE_FIELDS, ...ALL_DRIVES,
  });
  return res.data;
}

async function editDoc(drive, fileId, localPath) {
  const res = await drive.files.update({
    fileId, media: markdownMedia(localPath), fields: FILE_FIELDS, ...ALL_DRIVES,
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

function defaultBox(pres) {
  const pw = pres.pageSize.width.magnitude / EMU_PER_PT;
  const ph = pres.pageSize.height.magnitude / EMU_PER_PT;
  return { x: pw * 0.3, y: ph * 0.3, width: pw * 0.4, height: ph * 0.4 };
}

function elementProps(pageObjectId, box) {
  return {
    pageObjectId,
    size: { width: { magnitude: box.width, unit: 'PT' }, height: { magnitude: box.height, unit: 'PT' } },
    transform: { scaleX: 1, scaleY: 1, translateX: box.x, translateY: box.y, unit: 'PT' },
  };
}

// Places an image on a slide from a public URL, a Drive file id, or a local file (uploaded first).
// The Slides API fetches an image only from a URL it can reach anonymously, so a Drive-held image gets link
// access just long enough for the insert - Slides stores its own copy - then it is removed, unless the file
// already had it. `objectId` optionally names the new image element.
async function placeImage(drive, slidesApi, deckId, slideId, src, box, objectId) {
  let url = src.url;
  let imageId = src.driveFile;
  if (src.path) {
    const folder = src.folder || ((await drive.files.get({ fileId: deckId, fields: 'parents', ...ALL_DRIVES })).data.parents || [])[0];
    imageId = (await upload(drive, src.path, folder)).id;
    console.log(`Uploaded ${src.path} -> ${imageId}`);
  }
  if (!url && !imageId) throw new Error('image needs url, driveFile, or path');

  let tempPermId = null;
  if (!url) {
    const perms = (await drive.permissions.list({ fileId: imageId, fields: 'permissions(id,type)', ...ALL_DRIVES })).data.permissions || [];
    if (!perms.some(p => p.type === 'anyone')) {
      tempPermId = (await drive.permissions.create({ fileId: imageId, requestBody: { type: 'anyone', role: 'reader' }, fields: 'id', ...ALL_DRIVES })).data.id;
    }
    url = `https://drive.google.com/uc?export=download&id=${imageId}`;
  }
  try {
    const createImage = { url, elementProperties: elementProps(slideId, box) };
    if (objectId) createImage.objectId = objectId;
    const res = await slidesApi.presentations.batchUpdate({ presentationId: deckId, requestBody: { requests: [{ createImage }] } });
    return res.data.replies[0].createImage.objectId;
  } finally {
    if (tempPermId) await drive.permissions.delete({ fileId: imageId, permissionId: tempPermId, ...ALL_DRIVES });
  }
}

async function insertImage(drive, slidesApi, deckId) {
  const pres = await readDeck(slidesApi, deckId);
  const slide = pickSlide(pres);
  if (!args['image-path'] && !args['image-file']) throw new Error('--image-path=<local file> or --image-file=<drive id> required');

  let box;
  if (args['box-shape']) {
    const el = (slide.pageElements || []).find(e => e.objectId === args['box-shape']);
    if (!el) throw new Error(`No element ${args['box-shape']} on that slide`);
    box = boxOf(el);
  } else if (args.width && args.height) {
    box = { x: Number(args.x || 0), y: Number(args.y || 0), width: Number(args.width), height: Number(args.height) };
  } else {
    box = defaultBox(pres);
  }
  const imageObjectId = await placeImage(drive, slidesApi, deckId, slide.objectId,
    { path: args['image-path'], driveFile: args['image-file'], folder: args.folder }, box);
  return { slide: slide.objectId, imageObjectId, box };
}

// ---- Slides: spec-driven operations ----
//
// `--slides-apply --deck=<id> --spec=<json file>` runs a list of operations in order, one batchUpdate each,
// so a later operation can use an element or slide id an earlier one created (name new ids with "id").
// Boxes are in points. A slide is named by object id, or by its 0-based index as a number or "index:N".

function colorOf(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex));
  if (!m) throw new Error(`color must be #rrggbb, got "${hex}"`);
  const n = parseInt(m[1], 16);
  return { rgbColor: { red: ((n >> 16) & 255) / 255, green: ((n >> 8) & 255) / 255, blue: (n & 255) / 255 } };
}

function slideIdOf(pres, ref) {
  const slides = pres.slides || [];
  if (typeof ref === 'number' || /^(index:)?\d+$/.test(String(ref))) {
    const i = Number(String(ref).replace('index:', ''));
    if (!slides[i]) throw new Error(`No slide at index ${i} (deck has ${slides.length})`);
    return slides[i].objectId;
  }
  if (!slides.some(s => s.objectId === ref)) throw new Error(`No slide with object id "${ref}"`);
  return ref;
}

function newId(op, prefix) {
  return op.id || `${prefix}_${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;
}

// op.style: { bold, italic, underline, fontSize (pt), fontFamily, color, bgColor, link } over op.range
// { start, end } of the shape's text (whole text when omitted). op.paragraph: { align, spaceAbove, spaceBelow }.
function styleRequests(objectId, op, cell) {
  const reqs = [];
  const range = op.range ? { type: 'FIXED_RANGE', startIndex: op.range.start, endIndex: op.range.end } : { type: 'ALL' };
  const base = { objectId, textRange: range };
  if (cell) base.cellLocation = cell;
  const s = op.style || {};
  const style = {};
  const fields = [];
  const put = (field, key, val) => { style[key] = val; fields.push(field); };
  if (s.bold !== undefined) put('bold', 'bold', s.bold);
  if (s.italic !== undefined) put('italic', 'italic', s.italic);
  if (s.underline !== undefined) put('underline', 'underline', s.underline);
  if (s.fontSize !== undefined) put('fontSize', 'fontSize', { magnitude: s.fontSize, unit: 'PT' });
  if (s.fontFamily !== undefined) put('fontFamily', 'fontFamily', s.fontFamily);
  if (s.color !== undefined) put('foregroundColor', 'foregroundColor', { opaqueColor: colorOf(s.color) });
  if (s.bgColor !== undefined) put('backgroundColor', 'backgroundColor', { opaqueColor: colorOf(s.bgColor) });
  if (s.link !== undefined) put('link', 'link', { url: s.link });
  if (fields.length) reqs.push({ updateTextStyle: { ...base, style, fields: fields.join(',') } });
  const p = op.paragraph || {};
  const pstyle = {};
  const pfields = [];
  if (p.align) { pstyle.alignment = p.align; pfields.push('alignment'); }
  if (p.spaceAbove !== undefined) { pstyle.spaceAbove = { magnitude: p.spaceAbove, unit: 'PT' }; pfields.push('spaceAbove'); }
  if (p.spaceBelow !== undefined) { pstyle.spaceBelow = { magnitude: p.spaceBelow, unit: 'PT' }; pfields.push('spaceBelow'); }
  if (pfields.length) reqs.push({ updateParagraphStyle: { ...base, style: pstyle, fields: pfields.join(',') } });
  return reqs;
}

function findElement(pres, objectId) {
  for (const s of pres.slides || []) {
    for (const el of s.pageElements || []) {
      if (el.objectId === objectId) return el;
      for (const child of (el.elementGroup && el.elementGroup.children) || []) if (child.objectId === objectId) return child;
    }
  }
  return null;
}

function replaceTextRequests(objectId, existing, text) {
  const reqs = [];
  if (existing.trim().length) reqs.push({ deleteText: { objectId, textRange: { type: 'ALL' } } });
  if (text) reqs.push({ insertText: { objectId, insertionIndex: 0, text } });
  return reqs;
}

function shapeRequests(pres, op) {
  const id = newId(op, 'shape');
  const slideId = slideIdOf(pres, op.slide);
  const box = { x: op.x ?? 36, y: op.y ?? 36, width: op.width ?? 300, height: op.height ?? 100 };
  const reqs = [{ createShape: { objectId: id, shapeType: op.shape || 'TEXT_BOX', elementProperties: elementProps(slideId, box) } }];
  if (op.text) reqs.push({ insertText: { objectId: id, insertionIndex: 0, text: op.text } });
  if (op.text) reqs.push(...styleRequests(id, op));
  const props = {};
  const fields = [];
  if (op.fill) { props.shapeBackgroundFill = { solidFill: { color: colorOf(op.fill) } }; fields.push('shapeBackgroundFill.solidFill.color'); }
  if (op.outline === 'none') { props.outline = { propertyState: 'NOT_RENDERED' }; fields.push('outline.propertyState'); }
  else if (op.outline) {
    props.outline = { outlineFill: { solidFill: { color: colorOf(op.outline) } }, weight: { magnitude: op.outlineWeight || 1, unit: 'PT' } };
    fields.push('outline.outlineFill.solidFill.color', 'outline.weight');
  }
  if (op.contentAlignment) { props.contentAlignment = op.contentAlignment; fields.push('contentAlignment'); }
  if (fields.length) reqs.push({ updateShapeProperties: { objectId: id, shapeProperties: props, fields: fields.join(',') } });
  return { reqs, note: { objectId: id } };
}

function tableRequests(pres, op) {
  const id = newId(op, 'table');
  const slideId = slideIdOf(pres, op.slide);
  const cells = op.cells || [];
  const rows = op.rows || cells.length;
  const columns = op.columns || Math.max(0, ...cells.map(r => r.length));
  if (!rows || !columns) throw new Error('add-table needs rows/columns or cells');
  const box = { x: op.x ?? 36, y: op.y ?? 36, width: op.width ?? 400, height: op.height ?? rows * 30 };
  const reqs = [{ createTable: { objectId: id, rows, columns, elementProperties: elementProps(slideId, box) } }];
  cells.forEach((row, r) => row.forEach((text, c) => {
    if (text === '' || text == null) return;
    const cellLocation = { rowIndex: r, columnIndex: c };
    reqs.push({ insertText: { objectId: id, cellLocation, insertionIndex: 0, text: String(text) } });
    if (op.style || op.paragraph) reqs.push(...styleRequests(id, { style: op.style, paragraph: op.paragraph }, cellLocation));
  }));
  return { reqs, note: { objectId: id } };
}

// Each operation returns { reqs, note, after } - the requests for one batchUpdate, a result note, and an
// optional follow-up for work that must wait on that batch's replies.
async function buildOp(ctx, op) {
  const { pres } = ctx;
  switch (op.op) {
    case 'duplicate-slide': {
      const slideId = slideIdOf(pres, op.slide);
      const id = op.id || `${slideId}_copy_${Date.now().toString(36)}`;
      const reqs = [{ duplicateObject: { objectId: slideId, objectIds: { [slideId]: id } } }];
      // The copy lands right after the original; move it when "index" asks for another spot.
      if (op.index !== undefined) reqs.push({ updateSlidesPosition: { slideObjectIds: [id], insertionIndex: op.index } });
      return { reqs, note: { objectId: id } };
    }
    case 'delete-slide':
      return { reqs: [{ deleteObject: { objectId: slideIdOf(pres, op.slide) } }] };
    case 'move-slides': {
      const ids = (op.slides || [op.slide]).map(r => slideIdOf(pres, r));
      return { reqs: [{ updateSlidesPosition: { slideObjectIds: ids, insertionIndex: op.index } }] };
    }
    case 'add-shape': return shapeRequests(pres, op);
    case 'add-table': return tableRequests(pres, op);
    case 'replace-all-text': {
      const req = { replaceAllText: { containsText: { text: op.find, matchCase: !!op.matchCase }, replaceText: op.replace ?? '' } };
      if (op.slides) req.replaceAllText.pageObjectIds = op.slides.map(r => slideIdOf(pres, r));
      return { reqs: [req] };
    }
    case 'set-text': {
      const el = findElement(pres, op.shape);
      if (!el || !el.shape) throw new Error(`set-text: no shape "${op.shape}"`);
      const existing = textOf(el.shape);
      let reqs;
      if (op.append && existing.trim()) reqs = [{ insertText: { objectId: op.shape, insertionIndex: existing.length - 1, text: '\n' + op.text } }];
      else reqs = replaceTextRequests(op.shape, existing, op.text ?? '');
      // Styling an appended paragraph would restyle the old text too, so only a replace or an explicit range styles.
      if (!op.append || op.range) reqs.push(...styleRequests(op.shape, op));
      return { reqs };
    }
    case 'style-text':
      return { reqs: styleRequests(op.shape, op) };
    case 'set-notes': {
      const slide = pres.slides.find(s => s.objectId === slideIdOf(pres, op.slide));
      const notesId = slide.slideProperties.notesPage.notesProperties.speakerNotesObjectId;
      const notesEl = (slide.slideProperties.notesPage.pageElements || []).find(e => e.objectId === notesId);
      return { reqs: replaceTextRequests(notesId, notesEl ? textOf(notesEl.shape) : '', op.text ?? ''), note: { notesObjectId: notesId } };
    }
    case 'insert-image': {
      const slideId = slideIdOf(pres, op.slide);
      const box = op.width && op.height ? { x: op.x ?? 0, y: op.y ?? 0, width: op.width, height: op.height } : defaultBox(pres);
      const objectId = await placeImage(ctx.drive, ctx.slidesApi, ctx.deckId, slideId,
        { url: op.url, driveFile: op.driveFile, path: op.path, folder: op.folder }, box, op.id);
      return { reqs: [], note: { objectId } };
    }
    case 'thumbnail': {
      const slideId = slideIdOf(pres, op.slide);
      return { reqs: [], note: await saveThumbnail(ctx.slidesApi, ctx.deckId, slideId, op.out, op.size) };
    }
    case 'raw':
      return { reqs: op.requests };
    default:
      throw new Error(`Unknown op "${op.op}"`);
  }
}

async function saveThumbnail(slidesApi, deckId, slideId, out, size) {
  if (!out) throw new Error('thumbnail needs an output path (out)');
  const res = await slidesApi.presentations.pages.getThumbnail({
    presentationId: deckId, pageObjectId: slideId,
    'thumbnailProperties.mimeType': 'PNG', 'thumbnailProperties.thumbnailSize': size || 'LARGE',
  });
  const img = await fetch(res.data.contentUrl);
  if (!img.ok) throw new Error(`thumbnail download failed: HTTP ${img.status}`);
  fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });
  fs.writeFileSync(out, Buffer.from(await img.arrayBuffer()));
  return { slide: slideId, out, width: res.data.width, height: res.data.height };
}

async function applySpec(drive, slidesApi, deckId, specPath) {
  const spec = JSON.parse(fs.readFileSync(specPath, 'utf8'));
  const ops = Array.isArray(spec) ? spec : spec.ops;
  if (!Array.isArray(ops)) throw new Error('spec must be a JSON array of ops, or { "ops": [...] }');
  const results = [];
  for (const [i, op] of ops.entries()) {
    const pres = await readDeck(slidesApi, deckId);
    const built = await buildOp({ pres, drive, slidesApi, deckId }, op);
    if (args['dry-run']) { console.log(JSON.stringify({ i, op: op.op, requests: built.reqs }, null, 2)); continue; }
    let replies = 0;
    if (built.reqs.length) {
      const res = await slidesApi.presentations.batchUpdate({ presentationId: deckId, requestBody: { requests: built.reqs } });
      replies = (res.data.replies || []).length;
    }
    results.push({ i, op: op.op, ...(built.note || {}), requests: built.reqs.length, replies });
    console.log(JSON.stringify(results[results.length - 1]));
  }
  return results;
}

async function applyRaw(slidesApi, deckId, requestsPath) {
  const body = JSON.parse(fs.readFileSync(requestsPath, 'utf8'));
  const requests = Array.isArray(body) ? body : body.requests;
  if (!Array.isArray(requests)) throw new Error('requests file must be a JSON array, or { "requests": [...] }');
  if (args['dry-run']) return { requests };
  return (await slidesApi.presentations.batchUpdate({ presentationId: deckId, requestBody: { requests } })).data;
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
  } else if (args.share) {
    const r = await shareWithPerson(drive, need('file'), need('email'), need('role'));
    console.log(args.json ? JSON.stringify(r, null, 2) : `Shared with ${r.emailAddress}: ${r.role}\n${r.webViewLink}`);
  } else if (args.permissions) {
    const perms = await listPermissions(drive, need('file'));
    if (args.json) console.log(JSON.stringify(perms, null, 2));
    else {
      for (const p of perms) console.log(`${p.id}  ${p.type}  ${p.role}  ${p.emailAddress || p.displayName || ''}`);
      if (!perms.length) console.log('(no permissions)');
    }
  } else if (args.upload) {
    const f = await upload(drive, need('path'), args.folder, args.title);
    console.log(args.json ? JSON.stringify(f, null, 2) : `Uploaded -> ${f.id}  ${f.name}`);
  } else if (args['create-doc']) {
    const f = await createDoc(drive, need('path'), args.folder, need('title'));
    console.log(args.json ? JSON.stringify(f, null, 2) : `Created -> ${f.id}  ${f.name}\n${f.webViewLink}`);
  } else if (args['edit-doc']) {
    const f = await editDoc(drive, need('file'), need('path'));
    console.log(args.json ? JSON.stringify(f, null, 2) : `Edited -> ${f.id}  ${f.name}\n${f.webViewLink}`);
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
  } else if (args['slides-apply']) {
    await applySpec(drive, slidesApi, need('deck'), need('spec'));
  } else if (args['slides-raw']) {
    console.log(JSON.stringify(await applyRaw(slidesApi, need('deck'), need('requests-file')), null, 2));
  } else if (args['slides-thumbnail']) {
    const pres = await readDeck(slidesApi, need('deck'));
    const ref = args['slide-index'] !== undefined ? Number(args['slide-index']) : args.slide || pickSlide(pres).objectId;
    console.log(JSON.stringify(await saveThumbnail(slidesApi, args.deck, slideIdOf(pres, ref), need('out'), args.size), null, 2));
  } else if (args['slides-set-text']) {
    console.log(JSON.stringify(await setText(slidesApi, need('deck')), null, 2));
  } else {
    throw new Error('Nothing to do - pass a command (see the header of google-drive.js)');
  }
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
