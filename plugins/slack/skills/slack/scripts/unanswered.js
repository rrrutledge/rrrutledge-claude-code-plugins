// The messages in a DM or group DM that Russell hasn't answered yet: everything another person posted
// after his last word in the conversation. His last word is his newest top-level message, or his
// reaction on someone's message, which answers that message and everything before it. Bot and app
// posts never count as unanswered, and neither do join/leave and other system messages.
//
// `messages` is a conversations.history page in any order; `me` is Russell's user id.
// Returns the unanswered messages oldest first.
function unanswered(messages, me) {
  const byTs = (a, b) => parseFloat(a.ts) - parseFloat(b.ts);
  const timeline = messages.filter(m => m.ts && (!m.subtype || m.subtype === 'thread_broadcast' || m.subtype === 'me_message'));
  const answered = timeline.filter(m => m.user === me
    || (m.reactions || []).some(r => (r.users || []).includes(me)));
  const lastWord = answered.length ? Math.max(...answered.map(m => parseFloat(m.ts))) : 0;
  return timeline
    .filter(m => parseFloat(m.ts) > lastWord)
    .filter(m => m.user && m.user !== me && !m.bot_id && !m.app_id && !m.bot_profile)
    .sort(byTs);
}

module.exports = { unanswered };
