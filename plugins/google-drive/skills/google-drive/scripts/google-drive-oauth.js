// Shared OAuth2 client for the Google Drive and Slides APIs. Reuses the same Google Cloud OAuth "Desktop
// app" clients the `gmail` plugin already registered - one OAuth client, many scopes; each token cache only
// holds the scopes it was consented for. This path requests `drive` plus `presentations` (SKILL.md explains
// the scope choice) and keeps its own token cache under ~/.claude/google-drive/, separate from gmail's and
// google-docs'.
//
// Prerequisite: the Google Drive API and Google Slides API must both be enabled on the Cloud project each
// OAuth client belongs to - a one-time, per-project toggle, unrelated to any single token.
//
// Multiple accounts, the same model as gmail-oauth.js. No flag = the default account, cached at
// oauth-token.json and signed in through the shared GMAIL_OAUTH_CLIENT_ID / _SECRET. --account=<name> (or
// GOOGLE_DRIVE_ACCOUNT=<name>) serves another account from oauth-token-<name>.json, through its own
// GMAIL_OAUTH_CLIENT_ID_<NAME> / _SECRET_<NAME> when set - needed when the default client is Internal to
// one Workspace org and refuses a consumer @gmail.com account - falling back to the shared pair.
//
// Wrong-account guard. Every token records the address it was authorized for (account_email); assertAccountEmail() re-checks it on every run against Drive's about.get, so an
// operation can never land in the wrong Drive whatever account name was passed.
//
//   const { getAuthedClient, assertAccountEmail } = require('./google-drive-oauth');

const fs = require('fs');
const path = require('path');
const os = require('os');

const { OAuth2Client } = require('google-auth-library');

const DEP_HOME = path.join(os.homedir(), '.claude', 'google-drive');

// The name is a filename fragment, so it's held to a strict slug - a bad name fails loudly here rather
// than silently pointing at a surprise path.
function resolveAccount() {
  let name = null;
  for (const a of process.argv.slice(2)) {
    const m = a.match(/^--account=(.*)$/);
    if (m) { name = m[1]; break; }
  }
  if (!name && process.env.GOOGLE_DRIVE_ACCOUNT) name = process.env.GOOGLE_DRIVE_ACCOUNT;
  if (!name) return null;
  if (!/^[a-z0-9][a-z0-9-]*$/.test(name)) {
    throw new Error(`Invalid --account name "${name}": use lowercase letters, digits, and hyphens (e.g. --account=personal).`);
  }
  return name;
}
const ACCOUNT_NAME = resolveAccount();

const TOKEN_PATH = path.join(DEP_HOME, ACCOUNT_NAME ? `oauth-token-${ACCOUNT_NAME}.json` : 'oauth-token.json');
const ABOUT_URL = 'https://www.googleapis.com/drive/v3/about?fields=user(emailAddress)';
// Distinct from gmail's 8710, google-docs' 8711, and google-sheets/forms' 8712 so flows never collide.
const REDIRECT_URI = 'http://localhost:8713/callback';
const SCOPES = [
  'https://www.googleapis.com/auth/drive',
  'https://www.googleapis.com/auth/presentations',
];

function readTokens() {
  try { return JSON.parse(fs.readFileSync(TOKEN_PATH, 'utf8')); } catch { return null; }
}

function writeTokens(tokens) {
  fs.mkdirSync(DEP_HOME, { recursive: true });
  fs.writeFileSync(TOKEN_PATH, JSON.stringify(tokens, null, 2));
}

function readAccountEmail() {
  const t = readTokens();
  return t && t.account_email ? String(t.account_email).toLowerCase() : null;
}

function signInCommand() {
  const expected = readAccountEmail();
  return `node <google-drive>/scripts/google-drive-auth.js${ACCOUNT_NAME ? ` --account=${ACCOUNT_NAME}` : ''}` +
    `${expected ? ` --expect-email=${expected}` : ''} (via browser-chauffeur; the account owner approves the consent screen)`;
}

async function fetchAccountEmail(authedClient) {
  const res = await authedClient.request({ url: ABOUT_URL, method: 'GET' });
  return ((res.data.user && res.data.user.emailAddress) || '').toLowerCase();
}

async function assertAccountEmail(authedClient) {
  const expected = readAccountEmail();
  if (!expected) return;
  const live = await fetchAccountEmail(authedClient);
  if (live !== expected) {
    throw new Error(
      `Account guard tripped: this token (account "${ACCOUNT_NAME || 'default'}") was set up for ${expected}, ` +
      `but it now authorizes ${live || '(unknown)'}. Refusing to operate on the wrong Drive. To fix, run: ${signInCommand()}`
    );
  }
}

// <NAME> is the account name uppercased with hyphens turned to underscores.
function accountEnv(base) {
  if (ACCOUNT_NAME) {
    const scoped = process.env[`${base}_${ACCOUNT_NAME.toUpperCase().replace(/-/g, '_')}`];
    if (scoped) return scoped;
  }
  return process.env[base];
}

function buildOAuthClient() {
  const clientId = accountEnv('GMAIL_OAUTH_CLIENT_ID');
  const clientSecret = accountEnv('GMAIL_OAUTH_CLIENT_SECRET');
  const suffix = ACCOUNT_NAME ? ` (or GMAIL_OAUTH_CLIENT_ID_${ACCOUNT_NAME.toUpperCase().replace(/-/g, '_')})` : '';
  if (!clientId) throw new Error(`GMAIL_OAUTH_CLIENT_ID${suffix} env var not set`);
  if (!clientSecret) throw new Error(`GMAIL_OAUTH_CLIENT_SECRET${suffix} env var not set`);
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
    throw new Error(`Not signed in for Drive/Slides (account "${ACCOUNT_NAME || 'default'}"). Run: ${signInCommand()}`);
  }
  const client = buildOAuthClient();
  client.setCredentials(tokens);
  return client;
}

module.exports = {
  buildOAuthClient, getAuthedClient, readTokens, writeTokens, readAccountEmail, assertAccountEmail,
  fetchAccountEmail, signInCommand, SCOPES, REDIRECT_URI, TOKEN_PATH, DEP_HOME, ACCOUNT_NAME,
};
