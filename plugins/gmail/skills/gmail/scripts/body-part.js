// Pick and wrap the one body part of a message that carries its text, from an imapflow bodyStructure, so a
// bulk read over IMAP can download that part alone and leave every attachment on the server.
// Run the tests: node --test body-part.test.js

// The text part to read: the first text/plain that isn't an attachment, else the first such text/html, else
// null. Returns { part, type, charset, encoding }; a single-part message's body is part "1".
function pickTextPart(structure) {
  const found = { 'text/plain': null, 'text/html': null };
  const walk = (node) => {
    if (!node) return;
    if (node.childNodes && node.childNodes.length) { node.childNodes.forEach(walk); return; }
    const type = (node.type || '').toLowerCase();
    if (!(type in found) || found[type] || (node.disposition || '').toLowerCase() === 'attachment') return;
    found[type] = {
      part: node.part || '1',
      type,
      charset: (node.parameters && node.parameters.charset) || 'utf-8',
      encoding: (node.encoding || '7bit').toLowerCase(),
    };
  };
  walk(structure);
  return found['text/plain'] || found['text/html'];
}

// A downloaded part's raw bytes as a standalone MIME entity, so mailparser decodes its transfer encoding
// and charset exactly as it would inside the full message.
function partAsMime(picked, raw) {
  const head = `Content-Type: ${picked.type}; charset="${picked.charset}"\r\n`
    + `Content-Transfer-Encoding: ${picked.encoding}\r\n\r\n`;
  return Buffer.concat([Buffer.from(head), Buffer.from(raw || '')]);
}

module.exports = { pickTextPart, partAsMime };
