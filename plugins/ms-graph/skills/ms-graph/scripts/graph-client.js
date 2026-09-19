// Shared Microsoft Graph client for a PERSONAL Microsoft account, built on the official
// libraries — @azure/msal-node (auth + token cache) and @microsoft/microsoft-graph-client
// (request builder). MSAL owns the refresh token internally and silently renews access
// tokens; there is no manual refresh-token handling here.
//
// Secrets come from env vars (never a file): GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET.
// The MSAL token cache persists to ~/.claude/ms-graph/token-cache.json (machine-local).
//
//   const { getGraphClient, getToken } = require('./graph-client');

const fs = require('fs');
const path = require('path');
const os = require('os');

const DEP_HOME = path.join(os.homedir(), '.claude', 'ms-graph');

const msal = require('@azure/msal-node');
const { Client } = require('@microsoft/microsoft-graph-client');

const CACHE_PATH = path.join(DEP_HOME, 'token-cache.json');
const AUTHORITY = 'https://login.microsoftonline.com/consumers'; // personal accounts only
const REDIRECT_URI = 'http://localhost:8080/callback';
// Resource scopes only — MSAL adds openid/profile/offline_access automatically.
const SCOPES = ['User.Read', 'Mail.ReadWrite', 'Mail.Send', 'Calendars.ReadWrite', 'MailboxSettings.ReadWrite', 'Contacts.ReadWrite', 'Files.Read'];

const cachePlugin = {
  beforeCacheAccess: async (ctx) => {
    try { ctx.tokenCache.deserialize(fs.readFileSync(CACHE_PATH, 'utf8')); } catch { /* first run */ }
  },
  afterCacheAccess: async (ctx) => {
    if (ctx.cacheHasChanged) {
      fs.mkdirSync(DEP_HOME, { recursive: true });
      fs.writeFileSync(CACHE_PATH, ctx.tokenCache.serialize());
    }
  },
};

function buildApp() {
  const clientId = process.env.GRAPH_CLIENT_ID;
  const clientSecret = process.env.GRAPH_CLIENT_SECRET;
  if (!clientId) throw new Error('GRAPH_CLIENT_ID env var not set');
  if (!clientSecret) throw new Error('GRAPH_CLIENT_SECRET env var not set');
  return new msal.ConfidentialClientApplication({
    auth: { clientId, clientSecret, authority: AUTHORITY },
    cache: { cachePlugin },
  });
}

// Returns a valid access token, silently refreshing via the cached account.
async function getToken() {
  const app = buildApp();
  const accounts = await app.getTokenCache().getAllAccounts();
  if (!accounts.length) {
    throw new Error('Not signed in. Run: node <ms-graph>/scripts/auth.js (via browser-chauffeur)');
  }
  const res = await app.acquireTokenSilent({ account: accounts[0], scopes: SCOPES });
  return res.accessToken;
}

// A Graph message's default id changes when the message moves between folders, so an id captured
// while a message sits in the Inbox stops resolving ("The specified object was not found in the
// store.") once something archives or moves it - which breaks any later fetch-by-id (--show,
// --get-attachments, --reply, --delete, --not-junk) on an already-moved message. Requesting
// immutable ids keeps a captured id valid across moves. The Prefer header is advisory: Graph honors
// it for Outlook items that carry an immutable id (mail messages, calendar events) and ignores it
// for resources that don't (drive, contacts), so applying it to every request is safe. Consistency
// is the whole point - the id type at capture must match the id type every consumer fetches with, so
// one header on the shared client guarantees it rather than leaving it to each call site.
const IMMUTABLE_ID_PREFER = 'IdType="ImmutableId"';

// Official Graph client; authProvider delegates to MSAL silent acquisition. The .api() wrapper
// stamps the immutable-id Prefer header onto every request the client builds; callers still chain
// .select()/.filter()/.version() as usual on the returned request builder.
async function getGraphClient() {
  const client = Client.init({
    authProvider: async (done) => {
      try { done(null, await getToken()); } catch (e) { done(e, null); }
    },
  });
  const buildRequest = client.api.bind(client);
  client.api = (path) => buildRequest(path).header('Prefer', IMMUTABLE_ID_PREFER);
  return client;
}

module.exports = { buildApp, getToken, getGraphClient, SCOPES, REDIRECT_URI, AUTHORITY, DEP_HOME };
