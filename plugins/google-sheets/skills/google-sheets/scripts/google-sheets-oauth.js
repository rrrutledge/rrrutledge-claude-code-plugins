// Shared OAuth2 client for the Google Sheets API. Reuses the same Google Cloud OAuth "Desktop app"
// client the `gmail` plugin's filter path already registered (GMAIL_OAUTH_CLIENT_ID/SECRET) — one
// OAuth client, many scopes; each token cache only holds the scopes it was consented for. This path
// requests https://www.googleapis.com/auth/spreadsheets (read/write Sheets values), which the gmail
// client's cached token was never asked for, so it keeps its own one-time sign-in and its own cache
// file (~/.claude/google-sheets/oauth-token.json), separate from ~/.claude/gmail/oauth-token.json.
//
// Prerequisite: the Google Sheets API must be enabled on the same Cloud project the client belongs
// to (console.cloud.google.com/apis/library/sheets.googleapis.com) — a one-time, per-project toggle,
// unrelated to any single OAuth token.
//
// Secrets come from env vars (never a file): GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET (same
// ones the gmail plugin uses). The token cache persists to ~/.claude/google-sheets/oauth-token.json
// (machine-local). The client auto-refreshes the access token from the cached refresh token, so
// google-sheets.js runs silently after the one-time sign-in via google-sheets-auth.js.
//
//   const { getAuthedClient } = require('./google-sheets-oauth');

const fs = require('fs');
const path = require('path');
const os = require('os');

const DEP_HOME = path.join(os.homedir(), '.claude', 'google-sheets');

const { OAuth2Client } = require('google-auth-library');

const TOKEN_PATH = path.join(DEP_HOME, 'oauth-token.json');
// Distinct port from gmail's 8710, ms-graph's 8080, and google-docs's 8711 so the flows never
// collide if run back to back.
const REDIRECT_URI = 'http://localhost:8712/callback';
const SCOPES = ['https://www.googleapis.com/auth/spreadsheets'];

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

function getAuthedClient() {
  const tokens = readTokens();
  if (!tokens || !tokens.refresh_token) {
    throw new Error('Not signed in for the Sheets API. Run: node <this dir>/google-sheets-auth.js (via browser-chauffeur)');
  }
  const client = buildOAuthClient();
  client.setCredentials(tokens);
  return client;
}

module.exports = { buildOAuthClient, getAuthedClient, readTokens, writeTokens, SCOPES, REDIRECT_URI, TOKEN_PATH, DEP_HOME };
