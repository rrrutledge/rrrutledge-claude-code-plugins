// One-time interactive sign-in for the Google Forms API (forms.body scope). Runs the OAuth2
// authorization-code loopback flow: prints (and serves) a consent URL, captures the code on a local
// callback, exchanges it for tokens, and caches them to ~/.claude/google-forms/oauth-token.json.
//
// Run via browser-chauffeur, which navigates to the printed URL — the account owner completes the
// consent themselves (pick the intended account), since granting a new scope to an app is their call
// to make, not something driven on their behalf.
//
//   node google-forms-auth.js   -> start the flow on http://localhost:8712/callback

const http = require('http');
const { buildOAuthClient, writeTokens, SCOPES, REDIRECT_URI } = require('./google-forms-oauth');

(async () => {
  const client = buildOAuthClient();
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
    setTimeout(() => { server.close(); reject(new Error('Auth flow timed out after 15 min')); }, 900000);
  });

  const { tokens } = await client.getToken(code);
  if (!tokens.refresh_token) {
    throw new Error('No refresh_token returned. Revoke prior access at https://myaccount.google.com/permissions and re-run so Google re-issues one.');
  }
  writeTokens(tokens);
  console.log('Signed in — Forms API token cached. google-forms.js will run silently now.');
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
