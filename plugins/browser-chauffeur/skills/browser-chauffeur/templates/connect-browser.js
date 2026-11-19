// connectBrowser - Playwright's connectOverCDP, bounded, and on a hang it names
// the tab that caused it.
//
// Races connectOverCDP against a hard timeout (the reason is in SKILL.md
// Resilient Connection).
//
// When the timeout fires, each page and iframe target is probed over its own
// WebSocket with a trivial Runtime.evaluate; a tab that cannot answer within
// PROBE_TIMEOUT_MS is the wedged one. The window is wide because a sleeping
// (Edge) or discarded tab takes seconds to wake on its first attach, and a
// narrow one reports it as hung. The thrown error lists those tabs along with
// the chauffeur.py command that finds each tab's owning session, and points the
// caller at SKILL.md Resilient Connection. The hung tab is left open: it may be
// another live session's work, and only its owner closes it.

const path = require('path');
const { CDPSession, httpJson, withTimeout } = require('./cdp-session');

const CONNECT_TIMEOUT_MS = 30000;
const PROBE_TIMEOUT_MS = 10000;
const CHAUFFEUR_PY = path.join(__dirname, 'chauffeur.py').replace(/\\/g, '/');

function loadChromium() {
  try { return require('playwright-core').chromium; }
  catch { return require(path.join(require('os').homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'playwright-core')).chromium; }
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
  } catch (e) {
    // Only silence marks a tab as hung; a refused or closing socket says
    // nothing about the renderer.
    return e.message !== 'probe timed out';
  } finally {
    try { session && session.close(); } catch { /* already closed */ }
  }
}

// Every page and out-of-process iframe that fails to answer a trivial evaluate
// in time. Only targets open before the connect began are probed: the stalled
// connect auto-attaches with waitForDebuggerOnStart, so a target created since
// can sit paused and would read as hung.
async function findHungTargets(cdpPort, knownIds) {
  const targets = (await httpJson(`http://localhost:${cdpPort}/json`))
    .filter(t => (t.type === 'page' || t.type === 'iframe') && t.webSocketDebuggerUrl)
    .filter(t => !knownIds || knownIds.has(t.id));
  const alive = await Promise.all(targets.map(probeTarget));
  return targets
    .filter((_, i) => !alive[i])
    .map(t => ({ id: t.id, title: t.title, url: t.url }));
}

async function connectBrowser(cdpPort = '9222') {
  const knownIds = httpJson(`http://localhost:${cdpPort}/json`)
    .then(ts => new Set(ts.map(t => t.id)), () => null);
  const connecting = loadChromium().connectOverCDP(`http://localhost:${cdpPort}`);
  try {
    return await withTimeout(connecting, CONNECT_TIMEOUT_MS, 'connect timeout');
  } catch (e) {
    if (e.message !== 'connect timeout') throw e;
  }
  // A connect that completes after we gave up would hold the process open.
  connecting.then(b => b.close().catch(() => {}), () => {});

  const hungTargets = await findHungTargets(cdpPort, await knownIds).catch(() => null);
  const head = `connectOverCDP did not complete within ${CONNECT_TIMEOUT_MS}ms on port ${cdpPort}.`;
  let finding;
  if (hungTargets == null) {
    finding = `The browser's /json endpoint did not answer, so no tab could be probed.`;
  } else if (hungTargets.length) {
    finding = `Unresponsive tab(s):\n` +
      hungTargets.map(t => `  - ${t.title || '(untitled)'} ${t.url} [${t.id}]`).join('\n') +
      `\nFind each one's owning session with:\n` +
      `  python "${CHAUFFEUR_PY}" --hung-tabs ${hungTargets.map(t => t.id).join(' ')}`;
  } else {
    finding = `Every tab answered the probe; the stall may come from the tab count or a worker.`;
  }
  throw new Error(`${head} ${finding}\nFollow SKILL.md Resilient Connection.`);
}

module.exports = { connectBrowser };
