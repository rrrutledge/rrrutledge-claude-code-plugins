// One-time / ~90-day interactive sign-in for the personal Microsoft account.
// Runs the OAuth authorization-code flow: prints (and serves) a consent URL, captures the
// code on a local callback, and lets MSAL exchange + cache the tokens. Run via
// browser-chauffeur, which navigates to the printed URL and approves consent.
//
//   node auth.js            -> start the flow on http://localhost:8080/callback

const http = require('http');
const { buildApp, SCOPES, REDIRECT_URI } = require('./graph-client');

(async () => {
  const app = buildApp();
  const authUrl = await app.getAuthCodeUrl({
    scopes: SCOPES,
    redirectUri: REDIRECT_URI,
    prompt: 'select_account',
  });
  console.log('AUTH_URL: ' + authUrl);

  const code = await new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const u = new URL(req.url, 'http://localhost:8080');
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
    server.listen(8080, () => console.log('Listening on http://localhost:8080/callback'));
    setTimeout(() => { server.close(); reject(new Error('Auth flow timed out after 15 min')); }, 900000);
  });

  await app.acquireTokenByCode({ code, scopes: SCOPES, redirectUri: REDIRECT_URI });
  console.log('Signed in - MSAL token cache populated. Mail/calendar scripts will run silently now.');
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
