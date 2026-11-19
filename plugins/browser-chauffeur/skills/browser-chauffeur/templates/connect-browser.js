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
// caller at SKILL.md Resilient Connection. The hung tab stays open for its owner
// to close, since it may be another live session's work.

const path = require('path');
const { CDPSession, httpJson, withTimeout } = require('./cdp-session');

const CONNECT_TIMEOUT_MS = 30000;
const PROBE_TIMEOUT_MS = 10000;
const CHAUFFEUR_PY = path.join(__dirname, 'chauffeur.py').replace(/\\/g, '/');

// The calling script's project copy first, as the script itself would resolve
// it, then the shared install.
function loadChromium() {
  const paths = [require.main && require.main.path, process.cwd()].filter(Boolean);
  try { return require(require.resolve('playwright-core', { paths })).chromium; }
  catch { return require(path.join(require('os').homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'playwright-core')).chromium; }
}

// A socket that can't be created (the ws library missing) throws out of here,
// so the caller reports the probe itself as failed.
async function probeTarget(target) {
  const session = new CDPSession(target.webSocketDebuggerUrl);
  try {
    await withTimeout(
      session.ready.then(() => session.send('Runtime.evaluate', { expression: '1', returnByValue: true })),
      PROBE_TIMEOUT_MS,
      'probe timed out',
    );
    return true;
  } catch (e) {
    // A probe that times out marks the tab hung; a refused or closing socket
    // counts as answering, since it tells nothing about the renderer.
    return e.message !== 'probe timed out';
  } finally {
    try { session.close(); } catch { /* already closed */ }
  }
}

// Every page and out-of-process iframe that fails to answer a trivial evaluate
// in time, split by whether it was open before the connect began. The stalled
// connect auto-attaches with waitForDebuggerOnStart, so a target created since
// can sit paused rather than hung; those are named apart and left out of the
// owner lookup.
async function findHungTargets(cdpPort, knownIds) {
  const targets = (await httpJson(`http://localhost:${cdpPort}/json`))
    .filter(t => (t.type === 'page' || t.type === 'iframe') && t.webSocketDebuggerUrl);
  const alive = await Promise.all(targets.map(probeTarget));
  const silent = targets.filter((_, i) => !alive[i]);
  const isOld = t => !knownIds || knownIds.has(t.id);
  return { hung: silent.filter(isOld), opened: silent.filter(t => !isOld(t)) };
}

const describe = ts => ts.map(t => `  - ${t.title || '(untitled)'} ${t.url} [${t.id}]`).join('\n');

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

  const found = await findHungTargets(cdpPort, await knownIds).catch(() => null);
  const head = `connectOverCDP did not complete within ${CONNECT_TIMEOUT_MS}ms on port ${cdpPort}.`;
  let finding;
  if (found == null) {
    finding = `The tabs could not be listed or probed.`;
  } else if (found.hung.length) {
    finding = `Unresponsive tab(s):\n${describe(found.hung)}\nFind each one's owning session with:\n` +
      `  python "${CHAUFFEUR_PY}" --hung-tabs ${found.hung.map(t => t.id).join(' ')}`;
  } else {
    finding = `Every tab open before the connect answered the probe; the stall may come from the tab count or a worker.`;
  }
  if (found && found.opened.length) {
    finding += `\nOpened during the connect and silent, possibly only paused by it:\n${describe(found.opened)}`;
  }
  throw new Error(`${head} ${finding}\nFollow SKILL.md Resilient Connection.`);
}

module.exports = { connectBrowser };
