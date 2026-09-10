// Shared OAuth2 client for all Gmail access. Both gmail.js (mail: read/label/draft/send via the Gmail
// REST API) and filters.js (settings: list/create/delete filters) authorize through this one client and
// one cached token, so a single sign-in covers the whole skill.
//
// Scopes (least-privilege union of what the two scripts need):
//   - gmail.modify   — read messages + threads, modify labels (archive = remove the INBOX label).
//   - gmail.compose  — create/update/delete drafts and send messages/drafts.
//   - gmail.settings.basic — list/create/delete filters (filters.js).
// This grants no permanent-delete: archive and draft/message removal go to All Mail / Trash, never a
// bypassing purge.
//
// Secrets come from env vars (never a file): GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET (from a
// Google Cloud OAuth "Desktop app" client). The OAuth token cache persists to
// ~/.claude/gmail/oauth-token.json (machine-local). The client auto-refreshes the access token from the
// cached refresh token, so both scripts run silently after the one-time sign-in via gmail-auth.js.
//
//   const { getAuthedClient } = require('./gmail-oauth');

const fs = require('fs');
const path = require('path');
const os = require('os');

const DEP_HOME = path.join(os.homedir(), '.claude', 'gmail');
module.paths.push(path.join(DEP_HOME, 'node_modules'));

const { OAuth2Client } = require('google-auth-library');

const TOKEN_PATH = path.join(DEP_HOME, 'oauth-token.json');
// A Desktop-app OAuth client allows any loopback redirect without pre-registering it. Fixed port keeps
// the local callback deterministic; distinct from ms-graph's 8080 so the two auth flows never collide.
const REDIRECT_URI = 'http://localhost:8710/callback';
const SCOPES = [
  'https://www.googleapis.com/auth/gmail.modify',
  'https://www.googleapis.com/auth/gmail.compose',
  'https://www.googleapis.com/auth/gmail.settings.basic',
];

function readTokens() {
  try { return JSON.parse(fs.readFileSync(TOKEN_PATH, 'utf8')); } catch { return null; }
}

function writeTokens(tokens) {
  fs.mkdirSync(DEP_HOME, { recursive: true });
  fs.writeFileSync(TOKEN_PATH, JSON.stringify(tokens, null, 2));
}

function buildOAuthClient() {
  const clientId = process.env.GMAIL_OAUTH_CLIENT_ID;
  const clientSecret = process.env.GMAIL_OAUTH_CLIENT_SECRET;
  if (!clientId) throw new Error('GMAIL_OAUTH_CLIENT_ID env var not set');
  if (!clientSecret) throw new Error('GMAIL_OAUTH_CLIENT_SECRET env var not set');
  const client = new OAuth2Client({ clientId, clientSecret, redirectUri: REDIRECT_URI });
  // On a silent refresh the client emits 'tokens' with a fresh access_token (and usually no
  // refresh_token — that only arrives on first consent). Merge so the refresh_token is never lost.
  client.on('tokens', (tokens) => {
    writeTokens({ ...(readTokens() || {}), ...tokens });
  });
  return client;
}

// An authorized client with cached credentials, ready to .request() the Gmail REST API.
function getAuthedClient() {
  const tokens = readTokens();
  if (!tokens || !tokens.refresh_token) {
    throw new Error('Not signed in for the OAuth settings path. Run: node <gmail>/scripts/gmail-auth.js (via browser-chauffeur)');
  }
  const client = buildOAuthClient();
  client.setCredentials(tokens);
  return client;
}

module.exports = { buildOAuthClient, getAuthedClient, readTokens, writeTokens, SCOPES, REDIRECT_URI, TOKEN_PATH, DEP_HOME };
