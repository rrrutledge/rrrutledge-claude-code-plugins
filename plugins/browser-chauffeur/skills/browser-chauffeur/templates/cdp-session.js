/**
 * CDPSession - reusable direct WebSocket connection to a CDP target.
 *
 * Use this when you need to talk directly to an out-of-process iframe (or any
 * CDP target) that Playwright's connectOverCDP cannot reach because site
 * isolation gives it a separate target with its own webSocketDebuggerUrl.
 *
 * Usage:
 *   const { CDPSession, httpJson, evalIn } = require('./cdp-session');
 *   const targets = await httpJson(`http://localhost:${cdpPort}/json`);
 *   const iframe  = targets.find(t => t.type === 'iframe' && t.url.includes('your-domain'));
 *   const session = new CDPSession(iframe.webSocketDebuggerUrl);
 *   await session.ready;
 *   const ctxs = [];
 *   session.on(m => { if (m.method === 'Runtime.executionContextCreated') ctxs.push(m.params.context); });
 *   await session.send('Runtime.enable');
 *   await new Promise(r => setTimeout(r, 1500)); // let existing contexts arrive
 *   const ctxId = ctxs.find(c => c.origin.includes('your-domain'))?.id;
 *   const title  = await evalIn(session, ctxId, 'document.title');
 *   session.close();
 *
 * The ws package is bundled with playwright-core - no extra npm install needed.
 */

const http = require('http');
const os = require('os');
const path = require('path');

let WebSocket;
try {
  ({ ws: WebSocket } = require('playwright-core/lib/utilsBundle'));
} catch {
  const pwPath = path.join(os.homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'playwright-core');
  ({ ws: WebSocket } = require(path.join(pwPath, 'lib', 'utilsBundle')));
}

function httpJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

class CDPSession {
  constructor(wsUrl) {
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

module.exports = { CDPSession, httpJson, evalIn };
