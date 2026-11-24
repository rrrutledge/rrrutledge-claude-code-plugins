// Thread helpers for --reply and --react. Run the tests: node --test threading.test.js

const REACTION_MIME = 'text/vnd.google.email-reaction+json';

// True when a Gmail API message payload carries Gmail's emoji-reaction part (a bare 👍/❤️ sent via the in-app reaction feature).
function isReaction(payload) {
  if (!payload) return false;
  if ((payload.mimeType || '').toLowerCase() === REACTION_MIME) return true;
  return (payload.parts || []).some(isReaction);
}

// The newest message in a thread (threads.get order is oldest first) that is neither a draft nor a reaction, or null.
function pickLatest(messages) {
  const candidates = (messages || []).filter(m => !(m.labelIds || []).includes('DRAFT') && !isReaction(m.payload));
  return candidates.reduce(
    (best, m) => (!best || Number(m.internalDate || 0) >= Number(best.internalDate || 0) ? m : best),
    null,
  );
}

// MailComposer folds a long message id onto a line of its own, leaving "In-Reply-To:" or "References:" empty
// on the header line. A reaction counts only when its In-Reply-To names the message it answers, so this
// pulls the first id back onto the header line, the way Gmail's own reactions are written, rather than
// trust every recipient's parser to unfold it. Takes and returns the built MIME as a Buffer.
function unfoldThreadHeaders(raw) {
  const s = raw.toString('utf8');
  const end = s.indexOf('\r\n\r\n');
  const head = s.slice(0, end).replace(/^(In-Reply-To|References):[ \t]*\r\n[ \t]+/gim, '$1: ');
  return Buffer.from(head + s.slice(end), 'utf8');
}

// True for exactly one emoji (one grapheme, pictographic), the only reaction Gmail accepts.
function isSingleEmoji(s) {
  const graphemes = [...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(String(s))];
  return graphemes.length === 1 && /\p{Extended_Pictographic}/u.test(s);
}

module.exports = { isReaction, pickLatest, unfoldThreadHeaders, isSingleEmoji };
