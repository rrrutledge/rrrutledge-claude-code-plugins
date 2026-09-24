// OAuth 2.0 Authorization Code + PKCE client for the Canva Connect API — Canva requires PKCE on
// every integration, confidential or not (https://www.canva.dev/docs/connect/authentication/).
// This is its own Canva developer-portal app, not a shared client like the Google plugins' —
// register one at https://www.canva.com/developers/apps under the target Canva account.
//
// Secrets come from env vars (never a file): CANVA_CLIENT_ID, CANVA_CLIENT_SECRET. The token cache
// persists to ~/.claude/canva/oauth-token.json (machine-local) and is refreshed transparently —
// Canva refresh tokens are single-use, so every refresh response's new refresh_token overwrites the
// cache immediately, before the new access token is handed back to the caller.
//
//   const { getAccessToken } = require('./canva-oauth');

const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');

const DEP_HOME = path.join(os.homedir(), '.claude', 'canva');
const TOKEN_PATH = path.join(DEP_HOME, 'oauth-token.json');
// Distinct port from gmail's 8710, ms-graph's 8080, google-docs's 8711, and google-sheets's 8712.
const REDIRECT_URI = 'http://127.0.0.1:8713/callback';
const SCOPES = ['asset:read', 'asset:write', 'folder:read', 'folder:write'];
const AUTHORIZE_URL = 'https://www.canva.com/api/oauth/authorize';
const TOKEN_URL = 'https://api.canva.com/rest/v1/oauth/token';
// Refresh a bit before the real expiry so a slow request never straddles the boundary.
const EXPIRY_SAFETY_MARGIN_MS = 60 * 1000;

function readTokens() {
  try { return JSON.parse(fs.readFileSync(TOKEN_PATH, 'utf8')); } catch { return null; }
}

function writeTokens(tokens) {
  fs.mkdirSync(DEP_HOME, { recursive: true });
  fs.writeFileSync(TOKEN_PATH, JSON.stringify(tokens, null, 2));
}

function getClientCredentials() {
  const clientId = process.env.CANVA_CLIENT_ID;
  const clientSecret = process.env.CANVA_CLIENT_SECRET;
  if (!clientId) throw new Error('CANVA_CLIENT_ID env var not set');
  if (!clientSecret) throw new Error('CANVA_CLIENT_SECRET env var not set');
  return { clientId, clientSecret };
}

function basicAuthHeader() {
  const { clientId, clientSecret } = getClientCredentials();
  return 'Basic ' + Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
}

// PKCE per RFC 7636: a random verifier, and its SHA-256 digest (base64url, no padding) as the
// challenge sent up front — Canva only ever sees the verifier at token-exchange time.
function generatePkce() {
  const verifier = crypto.randomBytes(64).toString('base64url');
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
  return { verifier, challenge };
}

function buildAuthUrl(state, challenge) {
  const { clientId } = getClientCredentials();
  const params = new URLSearchParams({
    code_challenge: challenge,
    code_challenge_method: 's256',
    scope: SCOPES.join(' '),
    response_type: 'code',
    client_id: clientId,
    state,
    redirect_uri: REDIRECT_URI,
  });
  return `${AUTHORIZE_URL}?${params.toString()}`;
}

async function exchangeCode(code, verifier) {
  const res = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: {
      'Authorization': basicAuthHeader(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      code,
      code_verifier: verifier,
      redirect_uri: REDIRECT_URI,
    }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(`Token exchange failed (${res.status}): ${JSON.stringify(body)}`);
  writeTokens({ ...body, fetched_at: Date.now() });
  return body;
}

async function refreshTokens(refreshToken) {
  const res = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: {
      'Authorization': basicAuthHeader(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams({
      grant_type: 'refresh_token',
      refresh_token: refreshToken,
    }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(`Token refresh failed (${res.status}): ${JSON.stringify(body)}`);
  writeTokens({ ...body, fetched_at: Date.now() });
  return body;
}

async function getAccessToken() {
  const tokens = readTokens();
  if (!tokens || !tokens.refresh_token) {
    throw new Error('Not signed in to Canva. Run: node canva-auth.js (via browser-chauffeur)');
  }
  const expiresAt = tokens.fetched_at + tokens.expires_in * 1000;
  if (Date.now() < expiresAt - EXPIRY_SAFETY_MARGIN_MS) {
    return tokens.access_token;
  }
  const fresh = await refreshTokens(tokens.refresh_token);
  return fresh.access_token;
}

module.exports = {
  getAccessToken,
  generatePkce,
  buildAuthUrl,
  exchangeCode,
  readTokens,
  writeTokens,
  REDIRECT_URI,
  SCOPES,
  DEP_HOME,
  TOKEN_PATH,
};
