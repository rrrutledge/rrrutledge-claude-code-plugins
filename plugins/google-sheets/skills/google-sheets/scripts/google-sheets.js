// Read/append Google Sheet values, via the official `googleapis` Sheets v4 client (OAuth path — see
// google-sheets-oauth.js). Generic to any spreadsheet/range; built for a per-row activity log but
// makes no assumption about column shape.
//
// Append rows:  node google-sheets.js --append-rows --sheet-id=<id> --range=<A1 range> --rows-file=<path to JSON>
//               (rows-file is a JSON array of rows, each row an array of per-column cell values.
//                --range names the sheet/column span to append after, e.g. "Sheet1!A:D" — the API
//                finds the current last row in that range and inserts new rows immediately below it,
//                so no last-row detection is needed on the caller's side. Values are written with
//                valueInputOption=USER_ENTERED, so a string like "9/3/2026" is parsed into a real
//                date cell the same way typing or pasting it into the sheet would be.)
//
// Get values:   node google-sheets.js --get-values --sheet-id=<id> --range=<A1 range>
//               (prints the range's values as JSON — e.g. to confirm the row count before/after an
//                append.)

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

(async () => {
  if (!args['sheet-id']) throw new Error('--sheet-id required');
  if (!args['range']) throw new Error('--range required');
  const authClient = getAuthedClient();
  const sheetsClient = google.sheets({ version: 'v4', auth: authClient });

  if (args['append-rows']) {
    if (!args['rows-file']) throw new Error('--rows-file required');
    const rows = JSON.parse(fs.readFileSync(args['rows-file'], 'utf8'));
    if (!Array.isArray(rows) || !rows.length) throw new Error('--rows-file must be a non-empty JSON array of rows');
    const result = await appendRows(sheetsClient, args['sheet-id'], args['range'], rows);
    console.log(`Appended ${rows.length} row(s) at ${result.updates.updatedRange}.`);
  } else if (args['get-values']) {
    const values = await getValues(sheetsClient, args['sheet-id'], args['range']);
    console.log(JSON.stringify(values));
  } else {
    throw new Error('Nothing to do — pass --append-rows or --get-values');
  }
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
