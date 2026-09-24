// One-time interactive sign-in for the Canva Connect API. Runs the OAuth2 Authorization Code +
// PKCE loopback flow: prints (and serves) a consent URL, captures the code + state on a local
// callback, exchanges it for tokens, and caches them to ~/.claude/canva/oauth-token.json.
//
// Run via browser-chauffeur, which navigates to the printed URL — the account owner completes the
// consent themselves (pick the intended Canva account, approve the requested scopes).
//
//   node canva-auth.js   -> start the flow on http://127.0.0.1:8713/callback

const http = require('http');
const crypto = require('crypto');
const { generatePkce, buildAuthUrl, exchangeCode, REDIRECT_URI } = require('./canva-oauth');

(async () => {
  const { verifier, challenge } = generatePkce();
  const state = crypto.randomBytes(16).toString('hex');
  const authUrl = buildAuthUrl(state, challenge);
  console.log('AUTH_URL: ' + authUrl);

  const { port } = new URL(REDIRECT_URI);
  const { code } = await new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const u = new URL(req.url, REDIRECT_URI);
      if (u.pathname === '/callback') {
        const c = u.searchParams.get('code');
        const returnedState = u.searchParams.get('state');
        res.writeHead(200, { 'Content-Type': 'text/html' });
        res.end('<h2>Signed in. You can close this tab.</h2>');
        server.close();
        if (!c) return reject(new Error('No code in callback: ' + req.url));
        if (returnedState !== state) return reject(new Error('State mismatch — possible CSRF, aborting'));
        resolve({ code: c });
      } else {
        res.writeHead(404); res.end();
      }
    });
    server.listen(port, () => console.log(`Listening on ${REDIRECT_URI}`));
    setTimeout(() => { server.close(); reject(new Error('Auth flow timed out after 15 min')); }, 900000);
  });

  const tokens = await exchangeCode(code, verifier);
  if (!tokens.refresh_token) {
    throw new Error('No refresh_token returned by Canva — re-run the flow.');
  }
  console.log('Signed in — Canva API token cached. canva.js will run silently now.');
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
