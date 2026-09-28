// Headless, deterministic self-heal for the personal `xoxc-` Slack user token slack.js authenticates
// with (SLACK_BOT_TOKEN / SLACK_COOKIE_D). That token pair periodically rotates (most often just the `d`
// session cookie, not the token itself — see docs/slack-token-refresh.md in personal-ai-pod for the
// manual procedure this automates) and slack.js then fails every call with `invalid_auth` until someone
// re-derives it from a logged-in browser session. This script does that re-derivation on its own: it
// connects to the persistent, already-logged-in browser (browser-chauffeur), opens Slack's own web
// client, and reads the SAME two values a human would otherwise have to sniff out of DevTools by hand.
//
// No AI is involved and none is needed — the extraction is a fixed, mechanical read of two values Slack's
// own client keeps in localStorage/cookies once a human has signed in there at least once. That "at least
// once" is the one thing this script cannot do for itself: if the persistent browser's OWN Slack session
// has fully logged out (not just the derived xoxc/cookie pair rotating, but the actual browser session),
// there is no session left to read a fresh token out of, and this reports `not_logged_in` rather than
// guessing or hanging.
//
// Driven by slack.js's own `refreshCreds()` (a plain `child_process` spawn — see its `call()`), so any
// command that hits invalid_auth self-heals uniformly, not just the drainer's own enumerate. Output is
// one JSON object on stdout, mirroring the roost-discord-read.js convention:
//   { "status": "ok", "token": "xoxc-...", "cookie": "..." }             — fresh creds extracted
//   { "status": "not_logged_in" }                                       — needs a human to sign in
// A genuinely unexpected failure (bad CDP connection, Slack's client markup changing shape) throws and
// exits 1, same as roost-discord-read.js, so the caller can tell "no session" from "something broke."
//
// Args:
//   --team=<id>   the Slack team id (e.g. T04PXKRM0) — same value as SLACK_TEAM_ID

const fs = require('fs');
const path = require('path');
const os = require('os');

const { chromium } = (() => {
  try { return require('playwright-core'); }
  catch { return require(path.join(os.homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'playwright-core')); }
})();

const { openTab, closeTab, screenshotOnFailure } = (() => {
  try { return require('browser-chauffeur-helpers'); }
  catch { return require(path.join(os.homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'browser-chauffeur-helpers')); }
})();

function argValue(flag) {
  const prefix = `--${flag}=`;
  const arg = process.argv.find(a => a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : undefined;
}

function readJson(p, fallback) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); }
  catch { return fallback; }
}

// The CDP port is discovered from browser-chauffeur's own state file, never hardcoded - the persistent
// browser is launched on whatever port was free, and that launch writes the live port here.
function cdpPort() {
  const state = readJson(path.join(os.homedir(), '.claude', 'browser-chauffeur', 'state.json'), {});
  return state.port || 9222;
}

// connectOverCDP auto-attaches to every open target to build its page tree; on a tab-heavy persistent
// profile that enumeration can hang indefinitely. Race it against a hard 30s timeout so a wedged browser
// fails fast with an actionable error instead of hanging forever - the hardened wrapper from
// browser-chauffeur's script-template.js.
const CONNECT_TIMEOUT_MS = 30000;

async function connectBrowser(port) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(
        `connectOverCDP did not complete within ${CONNECT_TIMEOUT_MS}ms on port ${port}. ` +
        `The persistent profile likely has too many open tabs or a wedged renderer.`)),
      CONNECT_TIMEOUT_MS);
  });
  try {
    return await Promise.race([chromium.connectOverCDP(`http://localhost:${port}`), timeout]);
  } finally {
    clearTimeout(timer);
  }
}

// Whether this browser profile is actually signed into the target Slack team, and the fresh xoxc token
// + `d` cookie if so. `localConfig_v2` is Slack's OWN persisted per-team client config - it exists, and
// carries a team's token, precisely when a human has signed into that team in this browser profile. That
// makes it a strictly better signal than scraping the rendered UI for a login form: it is the exact
// artifact we need to extract anyway, so "is it there" and "what do we extract" collapse into one read
// instead of two independent (and separately breakable) checks.
async function extractCreds(tab, teamId) {
  const token = await tab.evaluate((team) => {
    try {
      const parsed = JSON.parse(localStorage.getItem('localConfig_v2') || 'null');
      return parsed && parsed.teams && parsed.teams[team] && parsed.teams[team].token || null;
    } catch {
      return null;
    }
  }, teamId).catch(() => null);
  if (!token) return null;

  const cookies = await tab.context().cookies('https://slack.com');
  const dCookie = cookies.find(c => c.name === 'd' && c.domain.endsWith('slack.com'));
  if (!dCookie || !dCookie.value) return null;

  return { token, cookie: dCookie.value };
}

async function run() {
  const teamId = argValue('team');
  if (!teamId) {
    throw new Error('slack-token-refresh: --team=<id> is required');
  }

  const browser = await connectBrowser(cdpPort());
  const contexts = browser.contexts();
  const context = contexts.find(c => c.pages().some(p => p.url().startsWith('http'))) || contexts[0];
  const tab = await openTab(context, `https://app.slack.com/client/${teamId}`);

  try {
    // Give the SPA a moment to hydrate localStorage before reading it - a bare `domcontentloaded` can
    // race Slack's own client bootstrap, which is what actually populates localConfig_v2 for a session
    // that's merely resuming (not a fresh interactive login, which is instant).
    await tab.waitForLoadState('networkidle').catch(() => {});
    const creds = await extractCreds(tab, teamId);
    await closeTab(tab);
    await browser.close().catch(() => {});
    if (!creds) {
      console.log(JSON.stringify({ status: 'not_logged_in' }));
      return;
    }
    console.log(JSON.stringify({ status: 'ok', token: creds.token, cookie: creds.cookie }));
  } catch (e) {
    await screenshotOnFailure(context, 'slack-token-refresh-failed');
    await closeTab(tab);
    await browser.close().catch(() => {});
    throw e;
  }
}

run().catch(e => { console.error('ERROR:', e.message); process.exit(1); });
