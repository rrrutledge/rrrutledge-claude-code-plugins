// Append rows to an existing table in a Google Doc, via the official `googleapis` Docs v1 client
// (OAuth path — see google-docs-oauth.js). Built for a board-secretary sensitive-docs tracking
// table, but generic: any doc with one table and a fixed column count works.
//
// Append rows:  node google-docs.js --append-rows --doc-id=<id> --rows-file=<path to JSON> [--table-index=0]
//               (rows-file is a JSON array of rows, each row an array of per-column cell strings —
//                the row length must match the target table's column count. --table-index picks which
//                table on the page when a doc has more than one; default 0, the first table.)
//
// Delete rows:  node google-docs.js --delete-rows --doc-id=<id> --row-indices=1,2,3 [--table-index=0]
//               (row-indices are 0-based table-row positions, e.g. row 0 is usually the header — each
//                deletion runs in its own call, highest index first, so earlier deletions never shift
//                the position of a not-yet-deleted row.)
//
// Set cell bullets: node google-docs.js --set-cell-bullets --doc-id=<id> --row-index=<n> --col-index=<n>
//               --items-file=<path to JSON> [--table-index=0]
//               (items-file is a JSON array of strings — replaces the cell's entire content with one
//                bulleted paragraph per item.)
//
// Set cell link: node google-docs.js --set-cell-link --doc-id=<id> --row-index=<n> --col-index=<n>
//               --text="short label" --url="https://..." [--table-index=0]
//               (replaces the cell's entire content with one line of clickable hyperlinked text —
//                the short label is what's shown; clicking it opens the URL.)
//
// Set cell text: node google-docs.js --set-cell-text --doc-id=<id> --row-index=<n> --col-index=<n>
//               --text="..." [--bold] [--table-index=0]
//               (replaces the cell's entire content with one line of plain text, optionally bold.)
//
// Plain rows/bullets are inserted as plain text (no hyperlinks) via --append-rows/--set-cell-bullets;
// use --set-cell-link when the cell should show short clickable text instead of a bare URL. One row
// is appended and re-fetched at a time: within a row, cells are filled rightmost-column-first so an
// earlier insertText in the same batchUpdate never shifts the not-yet-filled cells' indices (Docs API
// applies a batch's requests in order, each seeing the previous requests' edits).
//
// Every read/write below goes through the official `googleapis` Docs v1 resource's typed
// documents.get/documents.batchUpdate methods — never a hand-built REST URL. The index-math (finding
// a cell's start/end, clearing a cell down to one empty paragraph, rightmost-first ordering within a
// batch) is inherent to the Docs API's structural editing model itself; no client library abstracts
// that part away, since it's business logic, not transport.

const fs = require('fs');
const path = require('path');

const { google } = require('googleapis');
const { getAuthedClient } = require('./google-docs-oauth');

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);

async function getDocument(docsClient, docId) {
  const res = await docsClient.documents.get({ documentId: docId });
  return res.data;
}

async function batchUpdate(docsClient, docId, requests) {
  return docsClient.documents.batchUpdate({ documentId: docId, requestBody: { requests } });
}

// Depth-first walk of a StructuralElement list, yielding every `table` element found (so a table
// nested in e.g. a tableOfContents body is still found alongside top-level ones).
function findTables(elements) {
  const out = [];
  for (const el of elements || []) {
    if (el.table) out.push(el);
    if (el.tableOfContents) out.push(...findTables(el.tableOfContents.content));
  }
  return out;
}

async function appendRow(docsClient, docId, tableIndex, rowValues) {
  const doc = await getDocument(docsClient, docId);
  const tables = findTables(doc.body.content);
  const tableEl = tables[tableIndex];
  if (!tableEl) throw new Error(`No table at index ${tableIndex} (doc has ${tables.length} table(s))`);

  const table = tableEl.table;
  const columnCount = table.columns;
  if (rowValues.length !== columnCount) {
    throw new Error(`Row has ${rowValues.length} cells but table has ${columnCount} columns: ${JSON.stringify(rowValues)}`);
  }
  const lastRowIndex = table.tableRows.length - 1;

  // 1. Insert one empty row below the current last row.
  await batchUpdate(docsClient, docId, [{
    insertTableRow: {
      tableCellLocation: {
        tableStartLocation: { index: tableEl.startIndex },
        rowIndex: lastRowIndex,
        columnIndex: 0,
      },
      insertBelow: true,
    },
  }]);

  // 2. Re-fetch to find the new (empty) row's real cell start indices.
  const doc2 = await getDocument(docsClient, docId);
  const tables2 = findTables(doc2.body.content);
  const table2 = tables2[tableIndex].table;
  const newRow = table2.tableRows[table2.tableRows.length - 1];

  // 3. Fill cells rightmost-first so each insertText's target index is still valid when it runs —
  // an insert only shifts indices strictly after it, and we've already handled everything after the
  // cell we're about to fill.
  const requests = [];
  for (let col = newRow.tableCells.length - 1; col >= 0; col--) {
    const text = String(rowValues[col] ?? '');
    if (!text) continue;
    const cell = newRow.tableCells[col];
    // Every cell starts with one empty paragraph; its content's startIndex is where text goes.
    const insertAt = cell.content[0].startIndex;
    requests.push({ insertText: { location: { index: insertAt }, text } });
  }
  if (requests.length) await batchUpdate(docsClient, docId, requests);
}

async function deleteRows(docsClient, docId, tableIndex, rowIndices) {
  // One deletion per batchUpdate call, re-fetching in between. A single batch with many
  // deleteTableRow requests was observed to silently drop the last request (the highest original
  // row index went undeleted with no error) — re-fetching each time costs more round-trips but is
  // the same safe pattern appendRow already uses, and it's been reliable where the batched form wasn't.
  const sorted = [...rowIndices].sort((a, b) => b - a);
  for (const rowIndex of sorted) {
    const doc = await getDocument(docsClient, docId);
    const tables = findTables(doc.body.content);
    const tableEl = tables[tableIndex];
    if (!tableEl) throw new Error(`No table at index ${tableIndex} (doc has ${tables.length} table(s))`);
    await batchUpdate(docsClient, docId, [{
      deleteTableRow: {
        tableCellLocation: {
          tableStartLocation: { index: tableEl.startIndex },
          rowIndex,
          columnIndex: 0,
        },
      },
    }]);
  }
}

async function setCellBullets(docsClient, docId, tableIndex, rowIndex, colIndex, items) {
  // 1. Locate the cell and clear its existing content down to a single empty paragraph — a cell
  // must always end with a paragraph mark, so we delete everything except that trailing newline.
  const doc = await getDocument(docsClient, docId);
  const tables = findTables(doc.body.content);
  const tableEl = tables[tableIndex];
  if (!tableEl) throw new Error(`No table at index ${tableIndex} (doc has ${tables.length} table(s))`);
  const row = tableEl.table.tableRows[rowIndex];
  if (!row) throw new Error(`No row at index ${rowIndex}`);
  const cell = row.tableCells[colIndex];
  if (!cell) throw new Error(`No column at index ${colIndex}`);

  const startIndex = cell.content[0].startIndex;
  const lastParagraph = cell.content[cell.content.length - 1];
  const endIndex = lastParagraph.endIndex;
  if (endIndex - startIndex > 1) {
    await batchUpdate(docsClient, docId, [{
      deleteContentRange: { range: { startIndex, endIndex: endIndex - 1 } },
    }]);
  }

  // 2. Insert each item as its own paragraph. Text placed right before the cell's one remaining
  // (empty) paragraph's newline turns that newline into the last item's terminator, rather than
  // leaving a trailing empty paragraph after it.
  await batchUpdate(docsClient, docId, [{
    insertText: { location: { index: startIndex }, text: items.join('\n') },
  }]);

  // 3. Re-fetch to get the real range of the now-multi-paragraph cell, then bullet the whole thing.
  const doc2 = await getDocument(docsClient, docId);
  const tables2 = findTables(doc2.body.content);
  const cell2 = tables2[tableIndex].table.tableRows[rowIndex].tableCells[colIndex];
  const rangeStart = cell2.content[0].startIndex;
  const rangeEnd = cell2.content[cell2.content.length - 1].endIndex;
  await batchUpdate(docsClient, docId, [{
    createParagraphBullets: {
      range: { startIndex: rangeStart, endIndex: rangeEnd },
      bulletPreset: 'BULLET_DISC_CIRCLE_SQUARE',
    },
  }]);
}

async function setCellLink(docsClient, docId, tableIndex, rowIndex, colIndex, text, url) {
  // Same clear-to-one-empty-paragraph approach as setCellBullets, then insert the label text and
  // style just that range as a hyperlink in one batch — insertText's shift is visible to the
  // updateTextStyle request that follows it in the same call.
  const doc = await getDocument(docsClient, docId);
  const tables = findTables(doc.body.content);
  const tableEl = tables[tableIndex];
  if (!tableEl) throw new Error(`No table at index ${tableIndex} (doc has ${tables.length} table(s))`);
  const row = tableEl.table.tableRows[rowIndex];
  if (!row) throw new Error(`No row at index ${rowIndex}`);
  const cell = row.tableCells[colIndex];
  if (!cell) throw new Error(`No column at index ${colIndex}`);

  const startIndex = cell.content[0].startIndex;
  const lastParagraph = cell.content[cell.content.length - 1];
  const endIndex = lastParagraph.endIndex;
  if (endIndex - startIndex > 1) {
    await batchUpdate(docsClient, docId, [{
      deleteContentRange: { range: { startIndex, endIndex: endIndex - 1 } },
    }]);
  }

  // Strip any bullet the paragraph carried from a prior --set-cell-bullets call — a single-line
  // link cell should never render as a one-item bulleted list.
  await batchUpdate(docsClient, docId, [
    { insertText: { location: { index: startIndex }, text } },
    {
      updateTextStyle: {
        range: { startIndex, endIndex: startIndex + text.length },
        textStyle: { link: { url } },
        fields: 'link',
      },
    },
    { deleteParagraphBullets: { range: { startIndex, endIndex: startIndex + text.length } } },
  ]);
}

async function setCellText(docsClient, docId, tableIndex, rowIndex, colIndex, text, bold) {
  const doc = await getDocument(docsClient, docId);
  const tables = findTables(doc.body.content);
  const tableEl = tables[tableIndex];
  if (!tableEl) throw new Error(`No table at index ${tableIndex} (doc has ${tables.length} table(s))`);
  const row = tableEl.table.tableRows[rowIndex];
  if (!row) throw new Error(`No row at index ${rowIndex}`);
  const cell = row.tableCells[colIndex];
  if (!cell) throw new Error(`No column at index ${colIndex}`);

  const startIndex = cell.content[0].startIndex;
  const lastParagraph = cell.content[cell.content.length - 1];
  const endIndex = lastParagraph.endIndex;
  if (endIndex - startIndex > 1) {
    await batchUpdate(docsClient, docId, [{
      deleteContentRange: { range: { startIndex, endIndex: endIndex - 1 } },
    }]);
  }

  const requests = [{ insertText: { location: { index: startIndex }, text } }];
  if (bold) {
    requests.push({
      updateTextStyle: {
        range: { startIndex, endIndex: startIndex + text.length },
        textStyle: { bold: true },
        fields: 'bold',
      },
    });
  }
  await batchUpdate(docsClient, docId, requests);
}

(async () => {
  if (!args['doc-id']) throw new Error('--doc-id required');
  const authClient = getAuthedClient();
  const docsClient = google.docs({ version: 'v1', auth: authClient });
  const tableIndex = args['table-index'] ? parseInt(args['table-index'], 10) : 0;

  if (args['append-rows']) {
    if (!args['rows-file']) throw new Error('--rows-file required');
    const rows = JSON.parse(fs.readFileSync(args['rows-file'], 'utf8'));
    for (const [i, row] of rows.entries()) {
      await appendRow(docsClient, args['doc-id'], tableIndex, row);
      console.log(`Row ${i + 1}/${rows.length} appended.`);
    }
    console.log('Done.');
  } else if (args['delete-rows']) {
    if (!args['row-indices']) throw new Error('--row-indices required');
    const rowIndices = args['row-indices'].split(',').map(s => parseInt(s.trim(), 10));
    await deleteRows(docsClient, args['doc-id'], tableIndex, rowIndices);
    console.log(`Deleted ${rowIndices.length} row(s).`);
  } else if (args['set-cell-bullets']) {
    if (!args['row-index']) throw new Error('--row-index required');
    if (!args['col-index']) throw new Error('--col-index required');
    if (!args['items-file']) throw new Error('--items-file required');
    const items = JSON.parse(fs.readFileSync(args['items-file'], 'utf8'));
    await setCellBullets(docsClient, args['doc-id'], tableIndex, parseInt(args['row-index'], 10), parseInt(args['col-index'], 10), items);
    console.log(`Set ${items.length} bullet(s) in row ${args['row-index']}, col ${args['col-index']}.`);
  } else if (args['set-cell-link']) {
    if (!args['row-index']) throw new Error('--row-index required');
    if (!args['col-index']) throw new Error('--col-index required');
    if (!args['text']) throw new Error('--text required');
    if (!args['url']) throw new Error('--url required');
    await setCellLink(docsClient, args['doc-id'], tableIndex, parseInt(args['row-index'], 10), parseInt(args['col-index'], 10), args['text'], args['url']);
    console.log(`Set link "${args['text']}" in row ${args['row-index']}, col ${args['col-index']}.`);
  } else if (args['set-cell-text']) {
    if (!args['row-index']) throw new Error('--row-index required');
    if (!args['col-index']) throw new Error('--col-index required');
    if (!args['text']) throw new Error('--text required');
    await setCellText(docsClient, args['doc-id'], tableIndex, parseInt(args['row-index'], 10), parseInt(args['col-index'], 10), args['text'], !!args['bold']);
    console.log(`Set text "${args['text']}" in row ${args['row-index']}, col ${args['col-index']}.`);
  } else {
    throw new Error('Nothing to do — pass --append-rows, --delete-rows, --set-cell-bullets, --set-cell-link, or --set-cell-text');
  }
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
