// One-time interactive sign-in for all Gmail access (mail + filters). Runs the OAuth2 authorization-code
// loopback flow: prints (and serves) a consent URL, captures the code on a local callback, exchanges it
// for tokens, and caches them to ~/.claude/gmail/oauth-token.json. Run via browser-chauffeur, which
// navigates to the printed URL and approves consent (pick the intended Google account — for ISC that's
// the Workspace account russ@innersourcecommons.org).
//
//   node gmail-auth.js   -> start the flow on http://localhost:8710/callback
//
// Consents for the full scope set in gmail-oauth.js (gmail.modify + gmail.compose + gmail.settings.basic),
// so gmail.js and filters.js both run silently afterward. Adding scopes later means re-running this to
// re-consent — Google issues a token only for the scopes granted on the consent screen.

const http = require('http');
const { buildOAuthClient, writeTokens, SCOPES, REDIRECT_URI } = require('./gmail-oauth');

(async () => {
  const client = buildOAuthClient();
  // access_type=offline + prompt=consent forces Google to return a refresh_token, so filters.js can
  // run silently afterward instead of re-consenting each time.
  const authUrl = client.generateAuthUrl({ access_type: 'offline', prompt: 'consent', scope: SCOPES });
  console.log('AUTH_URL: ' + authUrl);

  const port = new URL(REDIRECT_URI).port;
  const code = await new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const u = new URL(req.url, REDIRECT_URI);
      if (u.pathname === '/callback') {
        const c = u.searchParams.get('code');
        res.writeHead(200, { 'Content-Type': 'text/html' });
        res.end('<h2>Signed in. You can close this tab.</h2>');
        server.close();
        if (c) resolve(c); else reject(new Error('No code in callback: ' + req.url));
      } else {
        res.writeHead(404); res.end();
      }
    });
    server.listen(port, () => console.log(`Listening on ${REDIRECT_URI}`));
    setTimeout(() => { server.close(); reject(new Error('Auth flow timed out after 5 min')); }, 300000);
  });

  const { tokens } = await client.getToken(code);
  if (!tokens.refresh_token) {
    throw new Error('No refresh_token returned. Revoke prior access at https://myaccount.google.com/permissions and re-run so Google re-issues one.');
  }
  writeTokens(tokens);
  console.log('Signed in — OAuth token cached. filters.js will run silently now.');
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
