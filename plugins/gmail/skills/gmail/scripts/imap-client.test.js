// Tests for imap-client.js against a local fake IMAP server that breaks the connection in each of the ways
// withImap guards against. Run: node --test imap-client.test.js
const { test } = require('node:test');
const assert = require('node:assert');
const net = require('net');
const path = require('path');
const { spawnSync } = require('child_process');

const { withImap, LOGOUT_TIMEOUT_MS } = require('./imap-client');

// A plaintext IMAP server just capable enough for imapflow to log in and open INBOX. `onFetch` and
// `onLogout` decide what happens when those commands arrive; by default FETCH returns one message and
// LOGOUT is answered normally.
function fakeServer({ onFetch, onLogout } = {}) {
  const server = net.createServer(sock => {
    sock.on('error', () => {});
    sock.write('* OK [CAPABILITY IMAP4rev1] fake ready\r\n');
    let buf = '';
    sock.on('data', d => {
      buf += d.toString();
      let i;
      while ((i = buf.indexOf('\r\n')) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const [tag, rawCmd] = line.split(' ');
        const cmd = (rawCmd || '').toUpperCase();
        const reply = s => sock.write(s.replace(/TAG/g, tag));
        if (cmd === 'CAPABILITY') reply('* CAPABILITY IMAP4rev1\r\nTAG OK done\r\n');
        else if (cmd === 'LOGIN') reply('TAG OK [CAPABILITY IMAP4rev1] logged in\r\n');
        else if (cmd === 'LIST') reply('* LIST (\\Noselect) "/" ""\r\nTAG OK done\r\n');
        else if (cmd === 'SELECT' || cmd === 'EXAMINE') {
          reply('* FLAGS (\\Seen)\r\n* 1 EXISTS\r\n* OK [UIDVALIDITY 1] ok\r\n* OK [UIDNEXT 2] ok\r\nTAG OK [READ-WRITE] selected\r\n');
        } else if (cmd === 'FETCH' || cmd === 'UID') {
          if (onFetch) onFetch(sock, tag);
          else reply('* 1 FETCH (UID 1 FLAGS (\\Seen))\r\nTAG OK done\r\n');
        } else if (cmd === 'LOGOUT') {
          if (onLogout) onLogout(sock, tag);
          else { reply('* BYE bye\r\nTAG OK done\r\n'); sock.end(); }
        } else reply('TAG OK done\r\n');
      }
    });
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server)));
}

function opts(server, extra = {}) {
  return { host: '127.0.0.1', port: server.address().port, secure: false, auth: { user: 'u', pass: 'p' }, ...extra };
}

async function fetchAll(c) {
  const lock = await c.getMailboxLock('INBOX');
  try {
    const seen = [];
    for await (const m of c.fetch('1:*', { flags: true })) seen.push(m.uid);
    return seen;
  } finally { lock.release(); }
}

test('a healthy session returns the operation result', async () => {
  const server = await fakeServer();
  try {
    assert.deepStrictEqual(await withImap(opts(server), fetchAll), [1]);
  } finally { server.close(); }
});

test('a connection reset mid-fetch rejects with the reset, not an uncaught error event', async () => {
  const server = await fakeServer({ onFetch: sock => sock.resetAndDestroy() });
  try {
    await assert.rejects(withImap(opts(server), fetchAll), err => err.code === 'ECONNRESET');
  } finally { server.close(); }
});

test('a server that goes silent mid-fetch rejects once the socket timeout passes', async () => {
  const server = await fakeServer({ onFetch: () => {} });
  const started = Date.now();
  try {
    await assert.rejects(withImap(opts(server, { socketTimeout: 500 }), fetchAll), err => err.code === 'ETIMEOUT');
    assert.ok(Date.now() - started < LOGOUT_TIMEOUT_MS, 'fails within the socket timeout, not a logout wait');
  } finally { server.close(); }
});

test('an unanswered logout does not hold up a finished operation', async () => {
  const server = await fakeServer({ onLogout: () => {} });
  const started = Date.now();
  try {
    assert.deepStrictEqual(await withImap(opts(server), fetchAll), [1]);
    assert.ok(Date.now() - started < LOGOUT_TIMEOUT_MS + 2000, 'logout gives up after its budget');
  } finally { server.close(); }
});

function runChild(body) {
  const src = `const { exitIfStillRunningAfter } = require(${JSON.stringify(path.join(__dirname, 'imap-client'))});\n${body}`;
  return spawnSync(process.execPath, ['-e', src], { encoding: 'utf8', timeout: 20000 });
}

test('the run deadline ends a process that something is still holding open', () => {
  const r = runChild("exitIfStillRunningAfter(1000, 'IMAP operation'); setInterval(() => {}, 1000);");
  assert.strictEqual(r.status, 1);
  assert.match(r.stderr, /IMAP operation did not finish within 1s - aborting/);
});

test('the run deadline never keeps a finished process alive', () => {
  const started = Date.now();
  const r = runChild("exitIfStillRunningAfter(15000, 'IMAP operation');");
  assert.strictEqual(r.status, 0);
  assert.ok(Date.now() - started < 10000);
});
