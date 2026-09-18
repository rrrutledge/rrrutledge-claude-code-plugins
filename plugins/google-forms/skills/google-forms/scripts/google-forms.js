// Create and edit a Google Form via the official `googleapis` Forms v1 client (OAuth path -see
// google-forms-oauth.js). Creates the form, lays out instruction sections, and adds short-answer
// questions -the reusable pieces the current toolset otherwise can't produce (it can create
// Docs/Sheets/Slides, but not Forms).
//
// Create form:   node google-forms.js --create-form --title="..." [--document-title="..."]
//                (info.title is the form's visible header; document-title is the Drive file name,
//                 defaulting to the title. Prints formId, the edit URL, and the responder URL.)
//
// Add text:      node google-forms.js --add-text --form-id=<id> --title="Section heading"
//                --description-file=<path to md/txt> [--index=N]
//                (a TextItem -a heading + body block for instructions; the file's contents become
//                 the body. Appends to the end of the form unless --index (0-based) is given. Repeat
//                 to build several instruction sections.)
//
// Add short answer: node google-forms.js --add-short-answer --form-id=<id> --title="Your name"
//                [--required] [--index=N]
//                (a single-line text question. --required marks it mandatory; appends to the end
//                 unless --index is given.)
//
// Add file upload: node google-forms.js --add-file-upload --form-id=<id>
//                (the Forms API cannot create a file-upload question -a documented, long-standing
//                 gap: https://issuetracker.google.com/issues/229136447. This command does not fail
//                 silently; it prints the exact one-click steps to add the question by hand in the
//                 Forms editor, and the form's edit URL, so the manual step is unmistakable.)
//
// Show form:     node google-forms.js --show-form --form-id=<id>
//                (dumps the form's title and every item -index, type, title -for verification.)
//
// Every read/write below goes through the official `googleapis` Forms v1 resource's typed
// forms.create/forms.get/forms.batchUpdate methods -never a hand-built REST URL. A form is created
// with only info.title/documentTitle (all forms.create accepts); every item is then added with a
// follow-up batchUpdate, which is how the Forms API itself is shaped.

const fs = require('fs');

const { google } = require('googleapis');
const { getAuthedClient } = require('./google-forms-oauth');

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);

function editUrl(formId) { return `https://docs.google.com/forms/d/${formId}/edit`; }

async function getForm(formsClient, formId) {
  const res = await formsClient.forms.get({ formId });
  return res.data;
}

// createItem needs an explicit 0-based location index. Default to appending after the last item;
// honor an explicit --index when the caller wants a specific slot.
async function resolveIndex(formsClient, formId, explicit) {
  if (explicit !== undefined) return parseInt(explicit, 10);
  const form = await getForm(formsClient, formId);
  return (form.items || []).length;
}

async function createForm(formsClient, title, documentTitle) {
  const info = { title };
  if (documentTitle) info.documentTitle = documentTitle;
  const res = await formsClient.forms.create({ requestBody: { info } });
  return res.data;
}

async function addItem(formsClient, formId, item, index) {
  await formsClient.forms.batchUpdate({
    formId,
    requestBody: { requests: [{ createItem: { item, location: { index } } }] },
  });
}

async function addText(formsClient, formId, title, description, indexArg) {
  const index = await resolveIndex(formsClient, formId, indexArg);
  await addItem(formsClient, formId, { title, description, textItem: {} }, index);
  return index;
}

async function addShortAnswer(formsClient, formId, title, required, indexArg) {
  const index = await resolveIndex(formsClient, formId, indexArg);
  await addItem(formsClient, formId, {
    title,
    questionItem: { question: { required: !!required, textQuestion: { paragraph: false } } },
  }, index);
  return index;
}

function itemType(item) {
  if (item.questionItem) {
    const q = item.questionItem.question || {};
    if (q.fileUploadQuestion) return 'file-upload question';
    if (q.textQuestion) return q.textQuestion.paragraph ? 'paragraph question' : 'short-answer question';
    if (q.choiceQuestion) return `choice question (${q.choiceQuestion.type})`;
    if (q.scaleQuestion) return 'scale question';
    if (q.dateQuestion) return 'date question';
    if (q.timeQuestion) return 'time question';
    return 'question';
  }
  if (item.textItem) return 'text/instructions';
  if (item.pageBreakItem) return 'page break';
  if (item.imageItem) return 'image';
  if (item.videoItem) return 'video';
  if (item.questionGroupItem) return 'question group';
  return 'unknown';
}

function showForm(form) {
  console.log(`Title:     ${form.info?.title ?? '(none)'}`);
  console.log(`Doc name:  ${form.info?.documentTitle ?? '(none)'}`);
  console.log(`Edit URL:  ${editUrl(form.formId)}`);
  console.log(`Responder: ${form.responderUri ?? '(none)'}`);
  const items = form.items || [];
  console.log(`Items (${items.length}):`);
  items.forEach((item, i) => {
    const req = item.questionItem?.question?.required ? ' [required]' : '';
    console.log(`  ${i}. [${itemType(item)}]${req} ${item.title ?? ''}`);
  });
}

// The Forms API has no createItem support for a file-upload question. Rather than fail or emit a
// broken request, guide the one manual step that adds it in the Forms editor.
function fileUploadGuidance(formId) {
  console.log('The Forms API cannot create a file-upload question (issuetracker.google.com/issues/229136447).');
  console.log('Add it by hand in the Forms editor -one question, ~30 seconds:');
  console.log(`  1. Open ${editUrl(formId)}`);
  console.log('  2. Click "＋" (Add question), then the question-type dropdown → "File upload".');
  console.log('  3. Accept the "This form now needs Drive access" prompt (Forms auto-creates the destination folder in the owner\'s Drive).');
  console.log('  4. Set the file type (e.g. Video), max number of files, and max file size as needed.');
  console.log('Every other part of the form -title, instruction sections, short-answer questions -is already scripted here.');
  console.log('Note: a file-upload question requires respondents to be signed in to a Google account.');
}

(async () => {
  const authClient = getAuthedClient();
  const formsClient = google.forms({ version: 'v1', auth: authClient });

  if (args['create-form']) {
    if (!args['title']) throw new Error('--title required');
    const form = await createForm(formsClient, args['title'], args['document-title']);
    console.log(`formId:    ${form.formId}`);
    console.log(`Edit URL:  ${editUrl(form.formId)}`);
    console.log(`Responder: ${form.responderUri}`);
  } else if (args['add-text']) {
    if (!args['form-id']) throw new Error('--form-id required');
    if (!args['title']) throw new Error('--title required');
    if (!args['description-file']) throw new Error('--description-file required');
    const description = fs.readFileSync(args['description-file'], 'utf8');
    const index = await addText(formsClient, args['form-id'], args['title'], description, args['index']);
    console.log(`Added text section "${args['title']}" at index ${index}.`);
  } else if (args['add-short-answer']) {
    if (!args['form-id']) throw new Error('--form-id required');
    if (!args['title']) throw new Error('--title required');
    const index = await addShortAnswer(formsClient, args['form-id'], args['title'], !!args['required'], args['index']);
    console.log(`Added short-answer question "${args['title']}" at index ${index}.`);
  } else if (args['add-file-upload']) {
    if (!args['form-id']) throw new Error('--form-id required');
    fileUploadGuidance(args['form-id']);
  } else if (args['show-form']) {
    if (!args['form-id']) throw new Error('--form-id required');
    showForm(await getForm(formsClient, args['form-id']));
  } else {
    throw new Error('Nothing to do -pass --create-form, --add-text, --add-short-answer, --add-file-upload, or --show-form');
  }
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
