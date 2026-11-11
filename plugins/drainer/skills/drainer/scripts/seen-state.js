// Drainer seen-state helper - fail-safe processed-id store and digest queue.
//
// State lives under the project's runtime_dir (e.g. ~/Dev/personal-ai-pod/.tmp/drainer/):
//   seen.json         { "<source>": { "<id>": { "triage" } }, "_acks": { "<conversation key>": {...} } }
//   digest-queue.json [ { "id", "source", "item": {...} }, ... ]
//
// seen.json answers one question - has this id been dispatched? - so an entry's presence is the whole
// signal and `triage` rides along as a human-readable label of how it was classified. Whether the work
// finished is read off the source object itself by the poller's reconcile, never tracked here.
//
// The reserved `_acks` key holds the acknowledgement ledger: one entry per conversation that got a
// 48-hour acknowledgement (status sent), was skipped in review (skipped), or was folded into another
// thread's ack to the same person (covered). Every key starting with `_` is reserved, and is never a
// source, so every per-source reader and writer here refuses one.
//
// Fail-safe: a missing or corrupt file reads as "nothing seen / empty queue" - never throws,
// so the worst case is reprocessing an item, never silently dropping one. Writes are atomic
// (write a temp file, then rename over the target).
//
// CLI (the prose poller calls these):
//   node seen-state.js seen        <runtimeDir> <source> <id>             -> prints yes|no
//   node seen-state.js record      <runtimeDir> <source> <id> <triage>    -> records (idempotent)
//   node seen-state.js queue-add   <runtimeDir> <source> <id> <json-file> -> append a captured fyi/junk item to the digest queue
//   node seen-state.js queue-list  <runtimeDir>                          -> prints the queue as JSON
//   node seen-state.js queue-clear <runtimeDir> <id>                     -> removes one item from the queue
//   node seen-state.js requeue     <runtimeDir> <source> <id>            -> drop the seen key so the item re-enumerates and re-dispatches a fresh worker session
//   node seen-state.js ack-list    <runtimeDir>                          -> prints the ack ledger as JSON
//   node seen-state.js ack-set     <runtimeDir> <key> <json-file>        -> writes one ledger entry (replacing any earlier one for that conversation)

const fs = require('fs');
const path = require('path');

const SEEN_FILE = 'seen.json';
const QUEUE_FILE = 'digest-queue.json';
const ACK_KEY = '_acks';

function assertSource(source) {
  if (!source || String(source).startsWith('_')) {
    throw new Error(`"${source}" is not a source: keys starting with "_" are reserved in seen.json`);
  }
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
}

function writeJsonAtomic(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
  // On Windows the rename fails while a reader has the target open; that read is over in milliseconds.
  for (let attempt = 0; ; attempt++) {
    try {
      fs.renameSync(tmp, file);
      return;
    } catch (e) {
      if (e.code !== 'EPERM' && e.code !== 'EBUSY' && e.code !== 'EACCES') throw e;
      if (attempt >= 20) throw e;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 25);
    }
  }
}

// Every write is a whole-file read-modify-write, and the drainer's feed cycle and dispatch loop run at
// the same time, so two writers could each read the old file and one would drop the other's change - a
// lost "dispatched" mark would open a second worker for the item. Each write therefore holds an exclusive
// lock file for its few milliseconds. A lock older than LOCK_STALE_MS belongs to a writer that died
// mid-write and is taken over, and a waiter outlasts that, so a dead writer's lock costs the next writer
// a wait, never a failed write.
const LOCK_FILE = 'seen-state.lock';
const LOCK_STALE_MS = 30000;
const LOCK_WAIT_MS = 45000;

function withLock(runtimeDir, fn) {
  fs.mkdirSync(runtimeDir, { recursive: true });
  const lock = path.join(runtimeDir, LOCK_FILE);
  const deadline = Date.now() + LOCK_WAIT_MS;
  let fd;
  for (;;) {
    try {
      fd = fs.openSync(lock, 'wx');
      break;
    } catch (e) {
      if (e.code !== 'EEXIST' && e.code !== 'EPERM') throw e;
      try {
        const stale = fs.statSync(lock).mtimeMs;
        if (Date.now() - stale > LOCK_STALE_MS) {
          // Another waiter may have taken the stale lock over between the two stats; its fresh lock has a
          // new mtime, so only the same stale file is removed.
          if (fs.statSync(lock).mtimeMs === stale) fs.rmSync(lock, { force: true });
          continue;
        }
      } catch { continue; }  // the holder just released it
      if (Date.now() > deadline) throw new Error(`seen-state is locked by another writer (${lock})`);
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 25);
    }
  }
  try {
    return fn();
  } finally {
    fs.closeSync(fd);
    fs.rmSync(lock, { force: true });
  }
}

function seenPath(runtimeDir) { return path.join(runtimeDir, SEEN_FILE); }
function queuePath(runtimeDir) { return path.join(runtimeDir, QUEUE_FILE); }

function loadSeen(runtimeDir) {
  const data = readJson(seenPath(runtimeDir), {});
  return (data && typeof data === 'object') ? data : {};
}

function loadQueue(runtimeDir) {
  const data = readJson(queuePath(runtimeDir), []);
  return Array.isArray(data) ? data : [];
}

function isSeen(runtimeDir, source, id) {
  assertSource(source);
  const seen = loadSeen(runtimeDir);
  return Boolean(seen[source] && seen[source][id]);
}

function record(runtimeDir, source, id, triage) {
  assertSource(source);
  withLock(runtimeDir, () => {
    const seen = loadSeen(runtimeDir);
    if (!seen[source]) seen[source] = {};
    // Idempotent: an id already recorded keeps the triage it was first dispatched under.
    if (!seen[source][id]) {
      seen[source][id] = { triage };
    }
    writeJsonAtomic(seenPath(runtimeDir), seen);
  });
}

function queueAdd(runtimeDir, source, id, item) {
  withLock(runtimeDir, () => {
    const queue = loadQueue(runtimeDir);
    if (!queue.some(e => e.id === id)) {
      queue.push({ id, source, item });
      writeJsonAtomic(queuePath(runtimeDir), queue);
    }
  });
}

function queueList(runtimeDir) {
  return loadQueue(runtimeDir);
}

function queueClear(runtimeDir, id) {
  withLock(runtimeDir, () => {
    const queue = loadQueue(runtimeDir).filter(e => e.id !== id);
    writeJsonAtomic(queuePath(runtimeDir), queue);
  });
}

// Recovery: an item the poller's reconcile found unfinished - its source object still unhandled with no
// live worker session on it - is re-dispatched by DELETING its seen key, so the next enumerate no longer
// drops it as already-seen. No retry cap is needed: an item converges the moment its worker clears the
// source, because a cleared source object is no longer unhandled and the reconcile stops selecting it.
// (A still-relevant source item re-enumerates and re-dispatches; one that's no longer present simply
// doesn't come back.)
function requeue(runtimeDir, source, id) {
  assertSource(source);
  withLock(runtimeDir, () => {
    const seen = loadSeen(runtimeDir);
    if (seen[source] && seen[source][id]) {
      delete seen[source][id];
      writeJsonAtomic(seenPath(runtimeDir), seen);
    }
  });
}

function ackList(runtimeDir) {
  const ledger = loadSeen(runtimeDir)[ACK_KEY];
  return (ledger && typeof ledger === 'object' && !Array.isArray(ledger)) ? ledger : {};
}

function ackSet(runtimeDir, key, entry) {
  if (!key) throw new Error('ack-set needs a conversation key');
  if (!entry || typeof entry !== 'object' || !entry.status) throw new Error('ack-set entry needs a status');
  withLock(runtimeDir, () => {
    const seen = loadSeen(runtimeDir);
    seen[ACK_KEY] = { ...ackList(runtimeDir), [key]: entry };
    writeJsonAtomic(seenPath(runtimeDir), seen);
  });
}

module.exports = {
  isSeen, record, queueAdd, queueList, queueClear, requeue, ackList, ackSet,
};

if (require.main === module) {
  const [cmd, ...rest] = process.argv.slice(2);
  try {
    switch (cmd) {
      case 'seen':
        console.log(isSeen(rest[0], rest[1], rest[2]) ? 'yes' : 'no');
        break;
      case 'record':
        record(rest[0], rest[1], rest[2], rest[3]);
        console.log(`recorded ${rest[2]} (${rest[3]})`);
        break;
      case 'queue-add': {
        const item = readJson(rest[3], null);
        if (item === null) throw new Error(`could not read item JSON file: ${rest[3]}`);
        queueAdd(rest[0], rest[1], rest[2], item);
        console.log(`queued ${rest[2]}`);
        break;
      }
      case 'queue-list':
        console.log(JSON.stringify(queueList(rest[0]), null, 2));
        break;
      case 'queue-clear':
        queueClear(rest[0], rest[1]);
        console.log(`dequeued ${rest[1]}`);
        break;
      case 'requeue':
        requeue(rest[0], rest[1], rest[2]);
        console.log(`requeued ${rest[2]}`);
        break;
      case 'ack-list':
        console.log(JSON.stringify(ackList(rest[0]), null, 2));
        break;
      case 'ack-set': {
        const entry = readJson(rest[2], null);
        if (entry === null) throw new Error(`could not read ledger entry JSON file: ${rest[2]}`);
        ackSet(rest[0], rest[1], entry);
        console.log(`ledger ${rest[1]} -> ${entry.status}`);
        break;
      }
      default:
        throw new Error('Usage: seen-state.js <seen|record|queue-add|queue-list|queue-clear|requeue|ack-list|ack-set> ...');
    }
  } catch (e) {
    console.error('Error:', e.message);
    process.exit(1);
  }
}
