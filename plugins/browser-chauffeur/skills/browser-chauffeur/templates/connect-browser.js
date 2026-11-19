// connectBrowser - Playwright's connectOverCDP, bounded, and on a hang it names
// the tab that caused it.
//
// connectOverCDP auto-attaches to EVERY open target to build its page tree. On
// the shared persistent profile, one tab whose renderer is wedged (often another
// session's) makes that enumeration hang indefinitely, and Playwright's own
// `timeout` option bounds only the socket connect, not the enumeration. So the
// connect is raced against a hard timeout.
//
// When the timeout fires, each page target is probed over its own WebSocket with
// a trivial Runtime.evaluate; a tab that cannot answer within PROBE_TIMEOUT_MS is
// the wedged one. The window is wide because a sleeping (Edge) or discarded tab
// takes seconds to wake on its first attach, and a narrow one reports it as hung. The thrown error (code CDP_CONNECT_TIMEOUT) lists those tabs in
// `hungTargets` and in its message, and points the caller at openDirectTab
// (direct-tab.js), which drives a fresh tab of its own without enumerating the
// others. The hung tab is left open: it may be another live session's work.

const { CDPSession, httpJson, withTimeout } = require('./cdp-session');

const CONNECT_TIMEOUT_MS = 30000;
const PROBE_TIMEOUT_MS = 10000;

function loadChromium() {
  try { return require('playwright-core').chromium; }
  catch { return require(require('path').join(require('os').homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'playwright-core')).chromium; }
}

async function probeTarget(target) {
  let session;
  try {
    session = new CDPSession(target.webSocketDebuggerUrl);
    await withTimeout(
      session.ready.then(() => session.send('Runtime.evaluate', { expression: '1', returnByValue: true })),
      PROBE_TIMEOUT_MS,
      'probe timed out',
    );
    return true;
  } catch {
    return false;
  } finally {
    try { session && session.close(); } catch { /* already closed */ }
  }
}

// Every page target that fails to answer a trivial evaluate in time.
async function findHungTargets(cdpPort) {
  const targets = (await httpJson(`http://localhost:${cdpPort}/json`))
    .filter(t => t.type === 'page' && t.webSocketDebuggerUrl);
  const alive = await Promise.all(targets.map(probeTarget));
  return targets
    .filter((_, i) => !alive[i])
    .map(t => ({ id: t.id, title: t.title, url: t.url }));
}

async function connectBrowser(cdpPort = '9222') {
  const connecting = loadChromium().connectOverCDP(`http://localhost:${cdpPort}`);
  try {
    return await withTimeout(connecting, CONNECT_TIMEOUT_MS, 'connect timeout');
  } catch (e) {
    if (e.message !== 'connect timeout') throw e;
  }
  // A connect that completes after we gave up would hold the process open.
  connecting.then(b => b.close().catch(() => {}), () => {});

  const hungTargets = await findHungTargets(cdpPort).catch(() => []);
  const head = `connectOverCDP did not complete within ${CONNECT_TIMEOUT_MS}ms on port ${cdpPort}.`;
  const message = hungTargets.length
    ? `${head} Unresponsive tab(s), left open because they may be another session's work:\n` +
      hungTargets.map(t => `  - ${t.title || '(untitled)'} ${t.url} [${t.id}]`).join('\n') +
      `\nFollow SKILL.md Resilient Connection.`
    : `${head} Every tab answered a probe, so the profile likely has too many open tabs. Follow SKILL.md Resilient Connection.`;
  const err = new Error(message);
  err.code = 'CDP_CONNECT_TIMEOUT';
  err.hungTargets = hungTargets;
  throw err;
}

module.exports = { connectBrowser };
