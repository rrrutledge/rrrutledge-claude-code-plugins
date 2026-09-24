// One-time interactive sign-in for all Gmail access (mail + filters). Runs the OAuth2 authorization-code
// loopback flow: prints (and serves) a consent URL, captures the code on a local callback, exchanges it
// for tokens, and caches them. Run via browser-chauffeur, which navigates to the printed URL and approves
// consent (pick the intended Google account — for the default account that's the ISC Workspace account
// russ@innersourcecommons.org).
//
//   node gmail-auth.js                    -> sign in the default account (~/.claude/gmail/oauth-token.json)
//   node gmail-auth.js --account=<name> --expect-email=<address>
//                                         -> sign in another mailbox (oauth-token-<name>.json)
//
// --account=<name> selects a per-account token file so a second mailbox can be added without overwriting
// the first (see gmail-oauth.js). --expect-email=<addr> is the wrong-mailbox guard: after consent the flow
// reads the signed-in profile and refuses to save the token unless it matches, so an account name can
// never be wired to the wrong mailbox. The confirmed address is recorded in the token file (account_email)
// and re-checked on every later run. Passing --expect-email is strongly recommended for any named account.
//
// Consents for the full scope set in gmail-oauth.js (gmail.modify + gmail.compose + gmail.settings.basic),
// so gmail.js and filters.js both run silently afterward. Adding scopes later means re-running this to
// re-consent — Google issues a token only for the scopes granted on the consent screen.

const http = require('http');
const { buildOAuthClient, writeTokens, SCOPES, REDIRECT_URI, PROFILE_URL, ACCOUNT_NAME } = require('./gmail-oauth');

const expectEmail = (() => {
  for (const a of process.argv.slice(2)) {
    const m = a.match(/^--expect-email=(.*)$/);
    if (m) return m[1].toLowerCase();
  }
  return null;
})();

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

  // Read the mailbox this consent actually authorized. It both feeds the recorded account_email and,
  // when --expect-email was given, gates the save so a name is never wired to the wrong mailbox.
  client.setCredentials(tokens);
  const prof = await client.request({ url: PROFILE_URL, method: 'GET' });
  const signedInAs = (prof.data.emailAddress || '').toLowerCase();
  if (expectEmail && signedInAs !== expectEmail) {
    throw new Error(`Consent was for ${signedInAs || '(unknown)'}, but --expect-email=${expectEmail}. Not saving the token — sign in as the intended account and re-run.`);
  }

  writeTokens({ ...tokens, account_email: signedInAs });
  console.log(`Signed in as ${signedInAs} (account "${ACCOUNT_NAME || 'default'}") — OAuth token cached. gmail.js and filters.js will run silently now.`);
})().catch(e => { console.error('Error:', e.message); process.exit(1); });
