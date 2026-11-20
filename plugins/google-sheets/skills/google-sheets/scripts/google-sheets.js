// Read/append Google Sheet values, via the official `googleapis` Sheets v4 client (OAuth path - see
// google-sheets-oauth.js). Generic to any spreadsheet/range; built for a per-row activity log but
// makes no assumption about column shape.
//
// Append rows:  node google-sheets.js --append-rows --sheet-id=<id> --range=<A1 range> --rows-file=<path to JSON>
//               (rows-file is a JSON array of rows, each row an array of per-column cell values.
//                --range names the sheet/column span to append after, e.g. "Sheet1!A:D" - the API
//                finds the current last row in that range and inserts new rows immediately below it,
//                so no last-row detection is needed on the caller's side. Values are written with
//                valueInputOption=USER_ENTERED, so a string like "9/3/2026" is parsed into a real
//                date cell the same way typing or pasting it into the sheet would be.)
//
// Get values:   node google-sheets.js --get-values --sheet-id=<id> --range=<A1 range>
//               (prints the range's values as JSON - e.g. to confirm the row count before/after an
//                append.)
//
// List tabs:    node google-sheets.js --list-tabs --sheet-id=<id>
//               (prints one "sheetId<TAB>title" line per tab - the numeric sheetId is what --insert-rows
//                takes as --tab-id.)
//
// Insert rows:  node google-sheets.js --insert-rows --sheet-id=<id> --tab-id=<numeric sheetId> --at=<n>
//               --rows-file=<path to JSON>
//               (inserts rows.length blank rows before 0-based row index n - so --at=3 puts the first new
//                row on spreadsheet row 4, pushing the old row 4 down - then writes the rows into them
//                from column A with USER_ENTERED, so a row lands in the middle instead of at the bottom.)
//
// Update values: node google-sheets.js --update-values --sheet-id=<id> --range=<A1 range> --rows-file=<path to JSON>
//               (overwrites the range in place with the rows, USER_ENTERED; no rows are inserted.)

const fs = require('fs');
const path = require('path');

const { google } = require('googleapis');
const { getAuthedClient } = require('./google-sheets-oauth');

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);

async function appendRows(sheetsClient, spreadsheetId, range, rows) {
  const res = await sheetsClient.spreadsheets.values.append({
    spreadsheetId,
    range,
    valueInputOption: 'USER_ENTERED',
    insertDataOption: 'INSERT_ROWS',
    requestBody: { values: rows },
  });
  return res.data;
}

async function getValues(sheetsClient, spreadsheetId, range) {
  const res = await sheetsClient.spreadsheets.values.get({ spreadsheetId, range });
  return res.data.values || [];
}

async function listTabs(sheetsClient, spreadsheetId) {
  const res = await sheetsClient.spreadsheets.get({
    spreadsheetId,
    fields: 'sheets.properties(sheetId,title)',
  });
  return res.data.sheets.map(s => s.properties);
}

async function updateValues(sheetsClient, spreadsheetId, range, rows) {
  const res = await sheetsClient.spreadsheets.values.update({
    spreadsheetId,
    range,
    valueInputOption: 'USER_ENTERED',
    requestBody: { values: rows },
  });
  return res.data;
}

async function insertRows(sheetsClient, spreadsheetId, tabId, at, rows) {
  const tab = (await listTabs(sheetsClient, spreadsheetId)).find(t => t.sheetId === tabId);
  if (!tab) throw new Error(`No tab with sheetId ${tabId} (run --list-tabs)`);
  const range = { sheetId: tabId, dimension: 'ROWS', startIndex: at, endIndex: at + rows.length };
  await sheetsClient.spreadsheets.batchUpdate({
    spreadsheetId,
    requestBody: { requests: [{ insertDimension: { range, inheritFromBefore: at > 0 } }] },
  });
  const quoted = `'${tab.title.replace(/'/g, "''")}'`;
  try {
    return await updateValues(sheetsClient, spreadsheetId, `${quoted}!A${at + 1}`, rows);
  } catch (e) {
    // Drop the blank rows so a failed write leaves the sheet as it was and a retry does not stack more.
    await sheetsClient.spreadsheets.batchUpdate({
      spreadsheetId,
      requestBody: { requests: [{ deleteDimension: { range } }] },
    });
    throw e;
  }
}

function readRows(file) {
  if (!file) throw new Error('--rows-file required');
  const rows = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!Array.isArray(rows) || !rows.length || !rows.every(Array.isArray)) {
    throw new Error('--rows-file must be a non-empty JSON array of rows, each row an array');
  }
  return rows;
}

(async () => {
  if (!args['sheet-id']) throw new Error('--sheet-id required');
  const authClient = getAuthedClient();
  const sheetsClient = google.sheets({ version: 'v4', auth: authClient });

  if (args['list-tabs']) {
    for (const t of await listTabs(sheetsClient, args['sheet-id'])) console.log(`${t.sheetId}\t${t.title}`);
    return;
  }
  if (args['insert-rows']) {
    if (!/^\d+$/.test(String(args['tab-id']))) throw new Error('--tab-id required (numeric sheetId, see --list-tabs)');
    if (!/^\d+$/.test(String(args['at']))) throw new Error('--at required (0-based row index)');
    const rows = readRows(args['rows-file']);
    const result = await insertRows(sheetsClient, args['sheet-id'], parseInt(args['tab-id'], 10), parseInt(args['at'], 10), rows);
    console.log(`Inserted ${rows.length} row(s) and wrote ${result.updatedRange}.`);
    return;
  }
  if (!args['range']) throw new Error('--range required');

  if (args['update-values']) {
    const result = await updateValues(sheetsClient, args['sheet-id'], args['range'], readRows(args['rows-file']));
    console.log(`Updated ${result.updatedCells} cell(s) at ${result.updatedRange}.`);
  } else if (args['append-rows']) {
    const rows = readRows(args['rows-file']);
    const result = await appendRows(sheetsClient, args['sheet-id'], args['range'], rows);
    console.log(`Appended ${rows.length} row(s) at ${result.updates.updatedRange}.`);
  } else if (args['get-values']) {
    const values = await getValues(sheetsClient, args['sheet-id'], args['range']);
    console.log(JSON.stringify(values));
  } else {
    throw new Error('Nothing to do - pass --append-rows, --get-values, --update-values, --insert-rows, or --list-tabs');
  }
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
