// Converts a small Markdown subset into one insertText plus the Docs API requests that style it.
// Supported: # to ###### headings, "-"/"*" bullets, "1." numbered items, paragraphs (consecutive
// lines join with a space, blank lines separate), and inline **bold**, *italic*, [label](url).
// Everything is inserted at index 1 of an emptied body, so every offset below is relative to 1.

const BODY_START = 1;

function parseInline(src) {
  let text = '';
  const spans = [];
  const re = /\*\*([^*]+)\*\*|\*([^*]+)\*|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  let m;
  while ((m = re.exec(src))) {
    text += src.slice(last, m.index);
    const start = text.length;
    const label = m[1] ?? m[2] ?? m[3];
    text += label;
    const range = [start, text.length];
    if (m[1] !== undefined) spans.push({ range, style: { bold: true }, fields: 'bold' });
    else if (m[2] !== undefined) spans.push({ range, style: { italic: true }, fields: 'italic' });
    else spans.push({ range, style: { link: { url: m[4] } }, fields: 'link' });
    last = m.index + m[0].length;
  }
  text += src.slice(last);
  return { text, spans };
}

function parseBlocks(markdown) {
  const blocks = [];
  let para = null;
  const flush = () => { if (para) { blocks.push({ kind: 'p', src: para.join(' ') }); para = null; } };
  for (const raw of markdown.replace(/\r\n/g, '\n').split('\n')) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { flush(); continue; }
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      flush();
      blocks.push({ kind: 'h', level: m[1].length, src: m[2] });
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      flush();
      blocks.push({ kind: 'ul', src: m[1] });
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flush();
      blocks.push({ kind: 'ol', src: m[1] });
    } else {
      (para = para || []).push(line.trim());
    }
  }
  flush();
  return blocks;
}

function markdownToRequests(markdown) {
  const blocks = parseBlocks(markdown);
  const parts = [];
  const styleRequests = [];
  // Applied after every paragraph style: setting a named style resets the paragraph's bold/italic.
  const spanRequests = [];
  const bulletRuns = [];
  let offset = BODY_START;

  for (const block of blocks) {
    const { text, spans } = parseInline(block.src);
    const start = offset;
    const end = start + text.length;
    for (const s of spans) {
      spanRequests.push({
        updateTextStyle: {
          range: { startIndex: start + s.range[0], endIndex: start + s.range[1] },
          textStyle: s.style,
          fields: s.fields,
        },
      });
    }
    const named = block.kind === 'h' ? `HEADING_${block.level}` : 'NORMAL_TEXT';
    styleRequests.push({
      updateParagraphStyle: {
        range: { startIndex: start, endIndex: end + 1 },
        paragraphStyle: { namedStyleType: named },
        fields: 'namedStyleType',
      },
    });
    if (block.kind === 'ul' || block.kind === 'ol') {
      const run = bulletRuns[bulletRuns.length - 1];
      if (run && run.kind === block.kind && run.end === start) run.end = end + 1;
      else bulletRuns.push({ kind: block.kind, start, end: end + 1 });
    }
    parts.push(text);
    offset = end + 1;
  }

  const fullText = parts.join('\n');
  if (!fullText) return { text: '', requests: [] };
  const endIndex = BODY_START + fullText.length;

  const requests = [
    { insertText: { location: { index: BODY_START }, text: fullText } },
    {
      updateTextStyle: {
        range: { startIndex: BODY_START, endIndex: endIndex + 1 },
        textStyle: {},
        fields: 'bold,italic,link',
      },
    },
    { deleteParagraphBullets: { range: { startIndex: BODY_START, endIndex: endIndex + 1 } } },
    ...styleRequests,
    ...bulletRuns.map(r => ({
      createParagraphBullets: {
        range: { startIndex: r.start, endIndex: Math.min(r.end, endIndex + 1) },
        bulletPreset: r.kind === 'ul' ? 'BULLET_DISC_CIRCLE_SQUARE' : 'NUMBERED_DECIMAL_ALPHA_ROMAN',
      },
    })),
    ...spanRequests,
  ];
  return { text: fullText, requests };
}

module.exports = { markdownToRequests };
