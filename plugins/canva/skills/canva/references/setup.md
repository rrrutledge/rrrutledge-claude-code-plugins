# Canva - One-Time Setup (per machine)

0. **Enable MFA on the account that will own the app** - Canva gates Connect API / "Outside Canva" app setup behind MFA, and an authenticator app is the only method it offers.
   The MFA option itself only appears once the account has a password: an account that normally signs in by emailed code must first set one at `https://www.canva.com/login/reset`, then turn on the authenticator app under Settings > Login & security.
1. **Register an integration** at `https://www.canva.com/developers/apps` (verify this URL against the current dev portal - it moves) while signed in as the target Canva account.
   Create an app, add the redirect URI `http://127.0.0.1:8713/callback`, and request scopes `asset:read asset:write folder:read folder:write` - Canva auto-saves each change.
   This is an interactive step for the account owner (or done via browser-chauffeur against their already-authenticated session) - creating the app and clicking through the one consent screen.
   On a Canva Teams (non-Enterprise) plan, set the app's "Who can use your app?" to **Public** - Private is Enterprise-only and the choice is permanent once made.
   A Public app left in Draft still works for the owner without Canva's review.
2. **Save the Client ID and Client Secret** the instant they're generated, per the Secrets policy: leave the page open for the account owner to copy themselves rather than echoing the value in a terminal.
   Store them as `CANVA_CLIENT_ID` / `CANVA_CLIENT_SECRET` in the PowerShell profile (`$PROFILE`) - never in a repo file or `settings.json`.
3. **One-time sign-in** - `node scripts/canva-auth.js`, driven via **browser-chauffeur**: it prints an `AUTH_URL:` line, serves `http://127.0.0.1:8713/callback`, and on consent exchanges the code (PKCE) for tokens and caches them to `~/.claude/canva/oauth-token.json` (machine-local).
   The account owner completes the consent click themselves.
   After this, `canva.js` runs silently - the client auto-refreshes the access token (Canva refresh tokens are single-use; each refresh's new refresh token overwrites the cache immediately).

ISC's working app: "personal-ai-pod" (app ID `AAHOGJAl0pU`), owned by russ@innersourcecommons.org.
An earlier app under info@ (`AAHOGLcPtl8`) sits unused, because info@ has no MFA enabled.
