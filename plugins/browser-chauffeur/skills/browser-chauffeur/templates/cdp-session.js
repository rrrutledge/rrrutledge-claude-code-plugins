/**
 * CDPSession - reusable direct WebSocket connection to a CDP target.
 *
 * Use this when you need to talk directly to an out-of-process iframe (or any
 * CDP target) that Playwright's connectOverCDP cannot reach because site
 * isolation gives it a separate target with its own webSocketDebuggerUrl.
 *
 * Usage:
 *   const { CDPSession, httpJson, evalIn, waitForContext } = require('./cdp-session');
 *   const targets = await httpJson(`http://localhost:${cdpPort}/json`);
 *   const iframe  = targets.find(t => t.type === 'iframe' && t.url.includes('your-domain'));
 *   const session = new CDPSession(iframe.webSocketDebuggerUrl);
 *   await session.ready;
 *   const ctxs = [];
 *   session.on(m => { if (m.method === 'Runtime.executionContextCreated') ctxs.push(m.params.context); });
 *   await session.send('Runtime.enable');
 *   const ctxId = await waitForContext(ctxs, c => c.origin.includes('your-domain'));
 *   const title  = await evalIn(session, ctxId, 'document.title');
 *   session.close();
 *
 * The ws package is bundled with playwright-core - no extra npm install needed.
 */

const http = require('http');
const os = require('os');
const path = require('path');

// Loaded on first use, so requiring the helpers never depends on resolving
// playwright-core.
function loadWebSocket() {
  try {
    return require('playwright-core/lib/utilsBundle').ws;
  } catch {
    const pwPath = path.join(os.homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'playwright-core');
    return require(path.join(pwPath, 'lib', 'utilsBundle')).ws;
  }
}

// The browser's /json endpoint answers from the browser process, so it stays
// responsive while a renderer is wedged; the timeout still bounds a browser
// that is itself unresponsive.
function httpJson(url, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('error', reject);
      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300) return reject(new Error(`${url} answered ${res.statusCode}: ${data}`));
        try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
      });
    });
    req.on('timeout', () => req.destroy(new Error(`${url} did not answer within ${timeoutMs}ms`)));
    req.on('error', reject);
    // The idle timeout above misses a response that trickles in, so a total
    // deadline ends the request too.
    const deadline = setTimeout(() => req.destroy(new Error(`${url} did not finish within ${timeoutMs}ms`)), timeoutMs);
    req.on('close', () => clearTimeout(deadline));
  });
}

function withTimeout(promise, ms, message) {
  let timer;
  const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

class CDPSession {
  constructor(wsUrl) {
    const WebSocket = loadWebSocket();
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
    this.ready = new Promise((resolve, reject) => {
      this.ws.on('open', resolve);
      this.ws.on('error', reject);
    });
    this.ws.on('message', (msg) => {
      const m = JSON.parse(msg.toString());
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        if (m.error) reject(new Error(m.error.message));
        else resolve(m.result);
      } else if (m.method) {
        for (const l of this.listeners) l(m);
      }
    });
    // A closed socket answers nothing more, so every request still waiting
    // fails now rather than at its caller's timeout.
    this.ws.on('close', () => {
      for (const { reject } of this.pending.values()) reject(new Error('CDP socket closed'));
      this.pending.clear();
    });
  }

  on(fn) { this.listeners.push(fn); }

  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  close() { this.ws.close(); }
}

async function evalIn(session, contextId, expression) {
  const r = await session.send('Runtime.evaluate', { contextId, expression, returnByValue: true });
  if (r.exceptionDetails) {
    throw new Error('CDP eval error: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  }
  return r.result.value;
}

// Execution contexts arrive as Runtime.executionContextCreated events after
// Runtime.enable; a nested frame still loading reports its context later. Poll
// the collected list until one matches, so the wait ends as soon as it exists.
async function waitForContext(ctxs, predicate) {
  const deadline = Date.now() + 5000;
  for (;;) {
    const ctx = ctxs.find(predicate);
    if (ctx) return ctx.id;
    if (Date.now() >= deadline) throw new Error('No execution context matched within 5000ms');
    await new Promise(r => setTimeout(r, 200));
  }
}

module.exports = { CDPSession, httpJson, evalIn, waitForContext, withTimeout };
