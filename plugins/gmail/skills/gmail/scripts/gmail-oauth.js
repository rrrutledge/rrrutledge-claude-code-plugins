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
// Google Cloud OAuth "Desktop app" client). The client auto-refreshes the access token from the cached
// refresh token, so every script runs silently after the one-time sign-in.
//
// Multiple accounts. By default the skill serves one account, cached at ~/.claude/gmail/oauth-token.json.
// Pass --account=<name> (or set GMAIL_ACCOUNT=<name>) to serve a second mailbox alongside the first: the
// token then caches to ~/.claude/gmail/oauth-token-<name>.json, so signing in as one account never
// overwrites another's token. No flag = the default single-file behavior. A named account can also carry
// its own OAuth client via GMAIL_OAUTH_CLIENT_ID_<NAME> / _SECRET (see buildOAuthClient) — needed when the
// default client can't consent that account, e.g. a personal mailbox and a default client that is Internal
// to one Workspace org.
//
// Wrong-mailbox guard. A token minted with an --expect-email records the address it was authorized for
// (account_email) in its token file. assertAccountEmail() then asserts, on every run, that the mailbox
// the token actually authorizes still matches that recorded address — so an operation can never land on
// the wrong account, whatever name (or none) was passed. A legacy token with no account_email recorded
// skips the check, so the default account still works until it's re-signed with --expect-email.
//
//   const { getAuthedClient } = require('./gmail-oauth');

const fs = require('fs');
const path = require('path');
const os = require('os');

const DEP_HOME = path.join(os.homedir(), '.claude', 'gmail');
module.paths.push(path.join(DEP_HOME, 'node_modules'));

const { OAuth2Client } = require('google-auth-library');

// Which account this invocation serves. --account=<name> on the command line wins; GMAIL_ACCOUNT is the
// fallback; neither set means the default (single-file) account. The name is a filename fragment, so it's
// held to a strict slug — a bad name should fail loudly here, never silently point at a surprise path.
function resolveAccount() {
  let name = null;
  for (const a of process.argv.slice(2)) {
    const m = a.match(/^--account=(.*)$/);
    if (m) { name = m[1]; break; }
  }
  if (!name && process.env.GMAIL_ACCOUNT) name = process.env.GMAIL_ACCOUNT;
  if (!name) return null;
  if (!/^[a-z0-9][a-z0-9-]*$/.test(name)) {
    throw new Error(`Invalid --account name "${name}": use lowercase letters, digits, and hyphens (e.g. --account=christina).`);
  }
  return name;
}
const ACCOUNT_NAME = resolveAccount();

// Per-account token file: the default account keeps the original oauth-token.json (so existing setups are
// untouched); a named account gets its own oauth-token-<name>.json beside it.
const TOKEN_PATH = path.join(DEP_HOME, ACCOUNT_NAME ? `oauth-token-${ACCOUNT_NAME}.json` : 'oauth-token.json');
const PROFILE_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/profile';
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

// The address this token was authorized for at sign-in, if it was recorded (see gmail-auth.js). null for a
// legacy token minted before the wrong-mailbox guard existed — such a token skips the guard entirely.
function readAccountEmail() {
  const t = readTokens();
  return t && t.account_email ? String(t.account_email).toLowerCase() : null;
}

// The wrong-mailbox guard: refuse to operate when the mailbox the token authorizes differs from the one it
// was set up for. Cheap — one profile GET — and a no-op for a legacy token with no recorded address, so it
// never disturbs an account that predates this. `authedClient` is the raw OAuth2Client (both gmail.js and
// filters.js hold one), so this works whatever REST wrapper the caller layers on top.
async function assertAccountEmail(authedClient) {
  const expected = readAccountEmail();
  if (!expected) return;
  const res = await authedClient.request({ url: PROFILE_URL, method: 'GET' });
  const live = (res.data.emailAddress || '').toLowerCase();
  if (live !== expected) {
    throw new Error(
      `Account guard tripped: this token (account "${ACCOUNT_NAME || 'default'}") was set up for ${expected}, ` +
      `but it now authorizes ${live || '(unknown)'}. Refusing to operate on the wrong mailbox. ` +
      `Re-run gmail-auth.js${ACCOUNT_NAME ? ` --account=${ACCOUNT_NAME}` : ''} --expect-email=${expected} to fix.`
    );
  }
}

// A named account can point at its own OAuth client via GMAIL_OAUTH_CLIENT_ID_<NAME> /
// GMAIL_OAUTH_CLIENT_SECRET_<NAME>, falling back to the shared GMAIL_OAUTH_CLIENT_ID / _SECRET. This lets
// an account the default client can't serve — e.g. a personal mailbox that the default Internal Workspace
// client refuses — sign in through its own client, and its token then refreshes silently against that same
// client. <NAME> is the account name uppercased with hyphens turned to underscores.
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
  // On a silent refresh the client emits 'tokens' with a fresh access_token (and usually no
  // refresh_token — that only arrives on first consent). Merge so the refresh_token (and the recorded
  // account_email) are never lost.
  client.on('tokens', (tokens) => {
    writeTokens({ ...(readTokens() || {}), ...tokens });
  });
  return client;
}

// An authorized client with cached credentials, ready to .request() the Gmail REST API.
function getAuthedClient() {
  const tokens = readTokens();
  if (!tokens || !tokens.refresh_token) {
    const which = ACCOUNT_NAME ? ` --account=${ACCOUNT_NAME}` : '';
    throw new Error(`Not signed in (account "${ACCOUNT_NAME || 'default'}"). Run: node <gmail>/scripts/gmail-auth.js${which} (via browser-chauffeur)`);
  }
  const client = buildOAuthClient();
  client.setCredentials(tokens);
  return client;
}

module.exports = {
  buildOAuthClient, getAuthedClient, readTokens, writeTokens, readAccountEmail, assertAccountEmail,
  SCOPES, REDIRECT_URI, TOKEN_PATH, DEP_HOME, ACCOUNT_NAME, PROFILE_URL,
};
