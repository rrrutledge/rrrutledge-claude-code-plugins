// One Gmail IMAP session, opened, used, and torn down so a broken connection always ends the run instead
// of crashing it with a raw stack trace or leaving it running. Shared by every IMAP path in gmail.js
// (--list-inbox-imap, --show-imap, --auth-imap), which the drainer poller runs every cycle under its own
// 90-second bound - so each limit here sits well inside that bound, and the poller always gets a clean,
// one-line failure it can record and retry next cycle.
//
// Three failure modes this guards against:
//   - A connection reset mid-command (read ECONNRESET). imapflow reports it as an 'error' event on the
//     client; with no listener attached, Node treats that as an uncaught exception and the helper dies
//     with a multi-line stack instead of a normal error. The listener below turns it into an ordinary
//     rejection of the operation in progress, reported by its real cause rather than imapflow's generic
//     "Connection not available".
//   - A connection that goes silent without closing. imapflow's own inactivity timeout defaults to five
//     minutes, far past the poller's bound; SOCKET_TIMEOUT_MS shortens it so a dead link fails fast.
//   - A logout that can't complete. Logout is best-effort cleanup after the real work already succeeded
//     or failed, so it gets a short budget and the socket is closed regardless of how it went.

const path = require('path');
const os = require('os');

module.paths.push(path.join(os.homedir(), '.claude', 'gmail', 'node_modules'));

const { ImapFlow } = require('imapflow');

const CONNECT_TIMEOUT_MS = 30 * 1000;
const SOCKET_TIMEOUT_MS = 30 * 1000;
const LOGOUT_TIMEOUT_MS = 5 * 1000;

// Runs fn(client) against a connected, authenticated IMAP client and returns its result. `options` is
// passed straight to ImapFlow on top of the timeouts above, so the caller supplies host and auth (and a
// test can point it at a local fake server).
async function withImap(options, fn) {
  const c = new ImapFlow({
    connectionTimeout: CONNECT_TIMEOUT_MS,
    socketTimeout: SOCKET_TIMEOUT_MS,
    logger: false,
    ...options,
  });
  let transportError = null;
  c.on('error', err => { transportError = transportError || err; });
  try {
    await c.connect();
  } catch (e) {
    c.close();
    throw transportError || e;
  }
  try {
    return await fn(c);
  } catch (e) {
    throw transportError || e;
  } finally {
    let timer;
    const budget = new Promise(resolve => { timer = setTimeout(resolve, LOGOUT_TIMEOUT_MS); });
    await Promise.race([c.logout(), budget]).catch(() => {});
    clearTimeout(timer);
    c.close();
  }
}

// A last-resort bound on the whole run. The timer is unref'd, so it never keeps the process alive by
// itself - it only fires when something else is still holding the process open past `ms`, which is
// exactly the case where the run has stopped making progress. It then exits nonzero with a one-line
// reason, so the caller sees a normal failure rather than waiting on a process that will never finish.
function exitIfStillRunningAfter(ms, what) {
  setTimeout(() => {
    console.error(`Error: ${what} did not finish within ${Math.round(ms / 1000)}s - aborting.`);
    process.exit(1);
  }, ms).unref();
}

module.exports = { withImap, exitIfStillRunningAfter, CONNECT_TIMEOUT_MS, SOCKET_TIMEOUT_MS, LOGOUT_TIMEOUT_MS };
