// Pick the message a --reply should thread off. Run the tests: node --test threading.test.js

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

module.exports = { isReaction, pickLatest };
