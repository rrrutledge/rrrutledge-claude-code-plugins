// Shared OAuth2 client for the Google Calendar API. Reuses the same Google Cloud OAuth "Desktop app"
// client the `gmail` plugin already registered (GMAIL_OAUTH_CLIENT_ID / _SECRET) - one OAuth client, many
// scopes; each token cache only holds the scopes it was consented for. This path requests the read-only
// calendar.readonly scope and keeps its own token cache (~/.claude/google-calendar/oauth-token.json),
// separate from gmail's and the other google-* plugins'.
//
// Prerequisite: the Google Calendar API must be enabled on the Cloud project the OAuth client belongs to -
// a one-time, per-project toggle, unrelated to any single token.
//
// Wrong-account guard. The token records the address it was authorized for (account_email);
// assertAccountEmail() re-checks it against the primary calendar's id (which is the signed-in address), so
// a read can never come from the wrong account's calendar.
//
//   const { getAuthedClient, assertAccountEmail } = require('./google-calendar-oauth');

const fs = require('fs');
const path = require('path');
const os = require('os');

const { OAuth2Client } = require('google-auth-library');

const DEP_HOME = path.join(os.homedir(), '.claude', 'google-calendar');
const TOKEN_PATH = path.join(DEP_HOME, 'oauth-token.json');
const PRIMARY_URL = 'https://www.googleapis.com/calendar/v3/calendars/primary';
// Distinct from gmail's 8710, google-docs' 8711, google-sheets/forms' 8712, and google-drive's 8713.
const REDIRECT_URI = 'http://localhost:8714/callback';
const SCOPES = ['https://www.googleapis.com/auth/calendar.readonly'];

function readTokens() {
  try { return JSON.parse(fs.readFileSync(TOKEN_PATH, 'utf8')); } catch { return null; }
}

function writeTokens(tokens) {
  fs.mkdirSync(DEP_HOME, { recursive: true });
  fs.writeFileSync(TOKEN_PATH, JSON.stringify(tokens, null, 2));
}

function signInCommand() {
  const t = readTokens();
  const expected = t && t.account_email;
  return `node <google-calendar>/scripts/google-calendar-auth.js --expect-email=${expected || '<address>'}` +
    ' (via browser-chauffeur; the account owner approves the consent screen)';
}

async function fetchAccountEmail(authedClient) {
  const res = await authedClient.request({ url: PRIMARY_URL, method: 'GET' });
  return (res.data.id || '').toLowerCase();
}

async function assertAccountEmail(authedClient) {
  const t = readTokens();
  const expected = t && t.account_email ? String(t.account_email).toLowerCase() : null;
  if (!expected) return;
  const live = await fetchAccountEmail(authedClient);
  if (live !== expected) {
    throw new Error(
      `Account guard tripped: this token was set up for ${expected}, but it now authorizes ${live || '(unknown)'}. ` +
      `Refusing to read the wrong calendar. To fix, run: ${signInCommand()}`
    );
  }
}

function buildOAuthClient() {
  const clientId = process.env.GMAIL_OAUTH_CLIENT_ID;
  const clientSecret = process.env.GMAIL_OAUTH_CLIENT_SECRET;
  if (!clientId) throw new Error('GMAIL_OAUTH_CLIENT_ID env var not set');
  if (!clientSecret) throw new Error('GMAIL_OAUTH_CLIENT_SECRET env var not set');
  const client = new OAuth2Client({ clientId, clientSecret, redirectUri: REDIRECT_URI });
  // A silent refresh emits 'tokens' with a fresh access_token and usually no refresh_token. Merge so the
  // refresh_token and the recorded account_email are never lost.
  client.on('tokens', (tokens) => {
    writeTokens({ ...(readTokens() || {}), ...tokens });
  });
  return client;
}

function getAuthedClient() {
  const tokens = readTokens();
  if (!tokens || !tokens.refresh_token) {
    throw new Error(`Not signed in for the Calendar API. Run: ${signInCommand()}`);
  }
  const client = buildOAuthClient();
  client.setCredentials(tokens);
  return client;
}

module.exports = {
  buildOAuthClient, getAuthedClient, writeTokens, assertAccountEmail, fetchAccountEmail, SCOPES, REDIRECT_URI,
};
