// The messages in a DM or group DM that Russell hasn't answered yet: everything another person posted
// after his last word in the conversation, plus anything of theirs still unread. His last word is his
// newest top-level message, his reply in a message's thread, or his reaction on a message; each answers
// that message and everything before it. A message still past his read cursor stays unanswered even
// after his last word, because a holding acknowledgement sent with --keep-unread leaves the question
// itself unread. Bot, app, and Slackbot posts never count as unanswered, and neither do join/leave and
// other system messages.
//
// `messages` is a conversations.history page in any order; `me` is Russell's user id; `lastRead` is
// his read cursor on the conversation ('' or a zero ts when Slack reports none).
// Returns the unanswered messages oldest first.
const PERSON_SUBTYPES = new Set(['thread_broadcast', 'me_message', 'file_share']);

function unanswered(messages, me, lastRead) {
  const timeline = messages.filter(m => !m.subtype || PERSON_SUBTYPES.has(m.subtype));
  const answered = timeline.filter(m => m.user === me
    || (m.reply_users || []).includes(me)
    || (m.reactions || []).some(r => (r.users || []).includes(me)));
  const lastWord = answered.length ? Math.max(...answered.map(m => parseFloat(m.ts))) : 0;
  const read = parseFloat(lastRead || '0');
  const cutoff = read > 0 ? Math.min(lastWord, read) : lastWord;
  return timeline
    .filter(m => parseFloat(m.ts) > cutoff)
    .filter(m => m.user && m.user !== me && m.user !== 'USLACKBOT' && !m.bot_id && !m.app_id && !m.bot_profile)
    .sort((a, b) => parseFloat(a.ts) - parseFloat(b.ts));
}

module.exports = { unanswered };
