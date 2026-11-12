# browser-chauffeur: one home per rule

The second of the three single-source refactors in [`skill-single-source.md`](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/blob/plan/skill-single-source/docs/plans/skill-single-source.md), section 4.
Line numbers below are `SKILL.md` on `main` at `073b8107` unless a file is named.

## Survey scope

The survey covered the skill folder (`SKILL.md`, `anti-patterns.md`, `script-quality-standards.md`, every file in `templates/`), the two plugin-root files the skill names or ships with (`HELPERS.md`, `README.md`), the global `CLAUDE.md`, and this repo's `CLAUDE.md`.
Every reviewer round gets that same list as SKILL_FILES.

The global `CLAUDE.md` overlaps the skill in one place: staging irreversible actions.
browser-chauffeur is a public plugin whose users don't load Russell's `CLAUDE.md`, so the skill keeps its own home for that rule and states it once.

## The shape afterward

| File | Holds |
|---|---|
| `SKILL.md` | The run flow (Phase 0-5) and every rule about what the session does during a run: tab lifecycle, escalation, recovery, verification, reading the page. |
| `script-quality-standards.md` | Every rule about what a script's code contains: waits, retries, selectors, actions vs reads, visibility checks, tab calls, connection, navigation, exit, verification output, helper calls. Phase 5 checks a script against this file and nothing else. |
| `anti-patterns.md` | Symptom-first entries for diagnosis. Each entry keeps its unique content (the symptom, the real example, the diagnostic code) and points at the rule's home for the fix instead of restating it. |
| `HELPERS.md` | The helper API reference: signatures, parameters, returns, and what each helper does inside. Rules about when to call a helper point at their home. |
| `README.md` | Install and trigger, agreeing with the skill's description. |

`SKILL.md`'s Key Rules splits by that line.
Selectors, strict mode, selector fallback, actions vs reads, and visibility-not-presence are script-code rules and move to `script-quality-standards.md`.
Verification, variant UIs, overlay detection, two-pass reveal, innerText vs screenshot, and DOM progress detection stay in `SKILL.md`, since the session applies them while driving.

The three summaries of the standards (`:577`, `:599`, `:635`) become one pointer in Phase 5.
Phase 4's list of anti-pattern symptoms (`:538`) becomes one line pointing at `anti-patterns.md`, which opens with its own one-line-per-entry index.

## Rule homes

Each row is a rule stated in two or more places today.
After the refactor it lives only in the home column, and every other location points there.

| Rule | Stated today at | Home |
|---|---|---|
| Get a tab with `openTab`/`findTab` only | `:226`, `:245-268`, `script-quality:47`, `HELPERS.md` openTab/findTab, `script-template.js` header | `SKILL.md` Phase 1 "Getting a tab" |
| Close only tabs you created, with `closeTab` in `finally` | `:209`, `:377-394`, `script-quality:47`, `HELPERS.md` closeTab | `SKILL.md` Phase 3 "Tab cleanup" |
| When to leave a tab open | `:70`, `:390-394`, `:400`, `:410`, `:552` | `SKILL.md` Phase 3 "Tab cleanup" |
| Tab ownership by `CLAUDE_PID` and the three-layer sweep | `:59`, `:145`, `:147`, `:226`, `:388`, `:400`, `HELPERS.md` openTab | `SKILL.md` Phase 0 "Automatic tab sweep" |
| Click-spawned tabs are owned automatically | `:268`, `HELPERS.md` openTab | `HELPERS.md` openTab |
| `require` with the try/catch fallback | `:118-129`, `:247`, `:379`, `HELPERS.md` Usage, `script-template.js` | `SKILL.md` Prerequisite Check |
| What setup installs and how the shim resolves | `:114`, `HELPERS.md` Setup | `HELPERS.md` Setup |
| Connect with `connectBrowser()`, never bare `connectOverCDP` | `:228`, `script-quality:72`, `HELPERS.md` Usage | `script-quality-standards.md` Browser Connection |
| What to do when the connect times out | `:230`, `:536`, `script-template.js` error message | `SKILL.md` Resilient Connection |
| Scripts take `--cdp-port` and do no browser detection or login check | `:194`, `script-quality:72`, `script-template.js` header | `script-quality-standards.md` Browser Connection |
| Never kill the browser or reset the shared profile on your own | `:230`, `:402`, `:404`, `:536`, Profile Management | `SKILL.md` Phase 3 "Leave the browser running" |
| Detect login from the screenshot, not script output | `:178-186`, `HELPERS.md` Login detection | `SKILL.md` Phase 0 Step 2 (the why moves in; `HELPERS.md` keeps the mid-flow snippet) |
| No fixed delays | `:349`, `:577`, `:611`, `script-quality:5`, `:315` and `cdp-session.js` (both use one) | `script-quality-standards.md` Fixed Delays |
| Semantic selectors | `:577`, `:605`, `script-quality:66`, AP1, `script-template.js` | `script-quality-standards.md` Selectors |
| Strict mode on multi-match locators | `:607`, AP1 | `script-quality-standards.md` Selectors |
| Broaden to the HTML tag when role finds nothing | `:609`, AP5 | `script-quality-standards.md` Selectors |
| Locator API for actions, `evaluate` for reads | `:538`, `:599`, `:613`, AP2, AP8 | `script-quality-standards.md` Actions vs reads |
| Visibility, not presence | `:538`, `:599`, `:621`, AP4 | `script-quality-standards.md` Visibility checks |
| Script prints `Verification passed`/`FAILED` | `:518-522`, `:577`, `script-quality:49`, `script-template.js` | `script-quality-standards.md` Verification Code |
| Verify a mutation before reporting it | `:356`, `:585`, `:615`, `:625` | `SKILL.md` Key Rules "Verifying a change" (see decision 1) |
| Exit code 0 is not success; Phase 4 runs after every script | `:207-208`, `:212`, `:512` | `SKILL.md` Phase 4 intro |
| Diagnose from the screenshot, and Read it | `:528`, `:554`, `:571`, AP3 | `SKILL.md` Phase 4 Step 2.1 |
| Dismiss overlays after navigation | `:160`, `:359`, `:532`, `:587`, `:619`, `script-quality:97`, `script-template.js` | `SKILL.md` Common Patterns "Overlay Dismissal" for the behavior; `script-quality-standards.md` for the required call |
| Screenshot on failure | `:528`, `:571`, `:589`, `script-quality:98`, `HELPERS.md` | `SKILL.md` Common Patterns "Screenshot on Failure" |
| Check `page.frames()` when the body is short | `:271`, `:277-290`, `:535`, `script-quality:95`, `script-template.js` | `SKILL.md` Phase 1 "Iframe detection" |
| `frame.route()` does not exist | `:290`, `script-quality:96` | `SKILL.md` Phase 1 "Iframe detection" |
| Guaranteed process exit | `:577`, `script-quality:78`, `HELPERS.md` Usage comment | `script-quality-standards.md` Guaranteed Process Exit |
| The process guard and watchdog | `script-quality:90`, `HELPERS.md` Process guard | `HELPERS.md` Process guard |
| Scripts navigate to their own target | `script-quality:74`, `script-template.js` | `script-quality-standards.md` Navigation |
| Escalate to the user through a visible blocking prompt | `:183`, `:356`, `:446`, `:519`, `:531`, `:546`, `:68`, `:97` | `SKILL.md` "User Intervention" (see contradiction 6) |
| Fetch emailed MFA codes instead of escalating | `:72`, `:98`, `:455-474`, `:504`, `:519`, `:531` | `SKILL.md` "Email-delivered MFA codes" |
| Re-find the user's tab after they clear a gate | `:78`, `:498-506` | `SKILL.md` "After the user resolves the blocker" |
| Stage up to the final button and stop | `:67`, `:81-83`, `:96`, `:99` | `SKILL.md` "The return contract" |
| Commit the spec, not the script | `:43`, `:408`, `README.md` | `SKILL.md` "Two usage modes" |
| Other skills' scripts run under this skill's recovery loop | `:200-216`, `:569-571` | `SKILL.md` Phase 0.5 |

The spawn brief (`:92-100`) is the one copy-paste block that stays self-contained, since it is pasted into a subagent prompt.
It names the return contract's two results and points at the skill's sections for each rule rather than restating them.

## Contradictions

### Settled from a script, the platform, or the docs

1. **"Try the other browser" (`:451`, `:531`, `:546`, `:589`, `script-quality:72`, `script-template.js` header) vs the single persistent browser (`:135`).**
   `chauffeur.py` launches Edge when it is installed and Chrome only when Edge is absent, with no flag to pick one, so a session cannot reach the other browser.
   Every "try the other browser" and "Edge/Chrome fallback" mention goes.

2. **"Wait briefly (500ms-1s)" after a dropdown click (`:349`) vs the fixed-delay ban (`:611`).**
   Phase 5 already rewrites any fixed delay it finds, so this wait never survives validation.
   The step becomes a condition wait for a visible menu, dialog, or listbox, then the screenshot.

3. **Retry loops banned (`script-quality:24`) vs `verifyAfterMutation`'s retries (`:585`).**
   `verify-after-mutation.js` re-checks a predicate after waiting for network idle and two animation frames; it never repeats the action.
   That is a condition wait, so the ban's home scopes it to repeating an action, and names re-checking a condition as a wait.
   `:585`'s "waits `settleMs`" becomes what the script does: waits up to `settleMs` for network idle.

4. **Click inside one `page.evaluate()` for virtualized grids (`:591`) vs actions through the locator API (`:613`, AP2).**
   The commit that added `:591` ([#606](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/606)) records the failure as locating in one script run and clicking in the next, where the grid had recycled the row.
   Locating and clicking in the same run fixes that with a locator click, so `:591` becomes "scroll with the grid's API and click its control with a locator, in the same run".

5. **Out-of-process iframes click with DOM `.click()` through `Runtime.evaluate` (`:324`, AP6) vs actions through the locator API.**
   The locator API reaches only the page target `connectOverCDP` attached to, so a `CDPSession` on the iframe target is the only route into it.
   The actions-vs-reads home states this as its one scoped exception.

6. **"You MUST use `AskUserQuestion`" (`:446`, `:183`, `:519`, `:531`, `:546`) vs the subagent's "never prompts the user itself" (`:64`).**
   The return contract already defines the subagent case.
   The User Intervention home states both: inline, prompt with `AskUserQuestion`; in a subagent, return `HELP_NEEDED` and the orchestrator prompts.

7. **"Do not reset the shared browser" (`:230`, `:536`) vs the connect-timeout error text "Reset the profile with `cleanup-browser.py --reset`" (`script-template.js:50`) and "When to reset: won't connect" (`:434`).**
   A reset kills every concurrent session's browser and logins, which the skill already makes the user's call.
   The error text points at re-running `chauffeur.py`, and "When to reset" becomes the list of reasons the user weighs when the session asks.

8. **A fixed 1500ms wait for execution contexts in the out-of-process iframe snippet (`:315`, `cdp-session.js:17`) vs the fixed-delay ban.**
   The ban's own exception covers a short poll with a deadline.
   Both become a ≤300ms poll until a context with the wanted origin appears.

9. **CSS class selectors banned (`script-quality:68`) vs AP7's fix `button:has(span.fui-Button__icon)`.**
   Fluent UI v9 publishes its `fui-*` static class names as public styling API (`buttonClassNames`), stable across deployments, unlike generated or app-specific classes.
   The Selectors home scopes the ban to generated and app-specific classes and names a component library's published class names as allowed.

10. **The sweep "closes a tracked tab only when its creating process is gone" (`:388`) vs ownership by the session's `CLAUDE_PID` (`:147`).**
    `tab-registry.js` and `chauffeur.py` key ownership on `CLAUDE_PID`; `:388` folds into a pointer at the sweep's home.

11. **`HELPERS.md`'s usage example calls `connectOverCDP` bare and takes `contexts()[0]` vs the hardened `connectBrowser()` rule.**
    The example points at `script-template.js` instead of carrying its own connect.

12. **`README.md` offers "MCP Playwright tools for quick public-site tasks" and "save it as a reusable script" vs the skill description's ban on Playwright MCP and "commit the spec, not the script" (`:43`).**
    The skill description is what loads at trigger time, so the README matches it.

The stale `teams-message` example at `:43` becomes `message-draft`, the skill that superseded it.

### For Russell to call

**Decision 1 - when a mutation counts as verified.**
`:625` requires a fresh page load before reporting any create, update, or delete as successful.
`:585` verifies in-page with `verifyAfterMutation`, whose own example (a deleted calendar event no longer shows) never reloads.
For a batch script that deletes fifty events, a reload per item is slow; with no reload, an optimistic UI update that never reached the server passes.

Recommendation: verify each item in-page with `verifyAfterMutation`, then do one fresh load at the end of the run and re-check the whole batch before reporting success.
A single-item mutation gets that same one fresh load.

## Refactor, once approved

1. Branch `browser-chauffeur-single-source` from `main`.
2. Move each rule to its home, replace every other mention with a pointer, and apply the settled contradictions and decision 1.
3. Grep each rule's keywords across SKILL_FILES and confirm one stating copy remains.
4. Bump `browser-chauffeur`'s minor version, since the verification and escalation behavior changes.
5. Run `writing-flow`: cold `writing-review` with SKILL_FILES, split by file if a round's diff gets large; mint; `gh pr create`; hand over the `/changes` link.
