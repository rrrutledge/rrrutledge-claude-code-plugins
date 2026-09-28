// One-time interactive sign-in for the Drive + Slides APIs. Runs the OAuth2 authorization-code loopback
// flow: prints (and serves) a consent URL, captures the code on a local callback, exchanges it for tokens,
// and caches them under ~/.claude/google-drive/.
//
// Run via browser-chauffeur, which navigates to the printed URL - the account owner completes the consent
// themselves (pick the intended account), since granting a new scope to an app is their call.
//
//   node google-drive-auth.js --expect-email=<address>                  -> the default account
//   node google-drive-auth.js --account=<name> --expect-email=<address> -> a named account
//
// --expect-email is the wrong-account guard: after consent the flow reads the signed-in Drive user and
// refuses to save the token unless it matches, then records the address for every later run to re-check.

const http = require('http');
const { buildOAuthClient, writeTokens, fetchAccountEmail, SCOPES, REDIRECT_URI, ACCOUNT_NAME } = require('./google-drive-oauth');

const expectEmail = (() => {
  for (const a of process.argv.slice(2)) {
    const m = a.match(/^--expect-email=(.*)$/);
    if (m) return m[1].toLowerCase();
  }
  return null;
})();

(async () => {
  const client = buildOAuthClient();
  // access_type=offline + prompt=consent forces Google to return a refresh_token.
  const authUrl = client.generateAuthUrl({ access_type: 'offline', prompt: 'consent', scope: SCOPES, login_hint: expectEmail || undefined });
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

  client.setCredentials(tokens);
  const signedInAs = await fetchAccountEmail(client);
  if (expectEmail && signedInAs !== expectEmail) {
    throw new Error(`Consent was for ${signedInAs || '(unknown)'}, but --expect-email=${expectEmail}. Not saving the token - sign in as the intended account and re-run.`);
  }

  writeTokens({ ...tokens, account_email: signedInAs });
  console.log(`Signed in as ${signedInAs} (account "${ACCOUNT_NAME || 'default'}") - Drive + Slides token cached. google-drive.js will run silently now.`);
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
