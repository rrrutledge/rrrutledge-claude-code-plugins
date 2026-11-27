# drainer browser gate - report HELP_NEEDED, not a silent stall

Read this when browser-chauffeur work in this session hits a sign-in or other possible gate.
Read it too when a page staged for Russell's final press expires before he reaches it, or the item's record carries a `Timer site:` line (**A site that times out**, below).
`<skill>` means this drainer skill's root folder, the same absolute path your seed prompt gave you for `worker-core.md`.

Any browser-chauffeur work this session drives - resolving a pointer's real content (`engine/pointers.md`), doing the item's work (worker-core step 3), staging a draft via message-draft (worker-core step 4), or a provider's browser-driven CLEAR - can hit a gate browser-chauffeur's own contract already defines (see browser-chauffeur's SKILL.md, **User Intervention** and **Running in a subagent → The return contract**).

Before reporting, apply browser-chauffeur's **Email-delivered MFA codes** and **Readable mailboxes**.
Report as `HELP_NEEDED` only what remains, naming the step precisely in `reason` (including a fetch that still failed after its one retry), in this session's own open tab instead of following browser-chauffeur's `AskUserQuestion` step or waiting on a `HELP_NEEDED` result from a subagent you spawned (message-draft's teams mode runs one, per worker-core step 4).
Both of those assume someone is watching this session live to answer.
A drainer worker runs unattended, so nobody sees the prompt, the session parks on a question nobody will ever answer, and the item sits stuck with no signal Russell can find.
That's the stall this file closes.

Park the gate where Russell already looks, the worker's own open session and tab:

1. **Leave the browser tab open** on the gate page.
   Don't retry past it.
   If a subagent you spawned already returned `HELP_NEEDED` with a `findTab` locator, that locator is what re-finds the tab later, so record it rather than losing it.
2. **Record the gate on the item.**
   Edit `items/<id>.json` (Edit tool) and add a `helpNeeded` object: `reason` (login / CAPTCHA / MFA-to-phone / in-page action needing a human), `url`, the `findTab` predicate description that re-finds the tab, and `progress`, one line on how far the run got before the gate stopped it.
3. **Don't queue a digest entry.**
   Russell cycles through every worker's open tab and sees what each one needs, so a digest line would only repeat what he already sees.
   Leave `triage` as it is and skip `queue-add`.
   The poller's reconcile (`engine/poller-core.md`) leaves the item alone while this session stays alive.
4. **Leave the source item uncleared.**
   The task isn't done, and clearing it here would drop it the way worker-core §2d warns against.
5. **Keep this session open.**
   A staged gate is squarely the "waiting on an answer from him" case worker-core §6's session-closing guidance carves out: nothing else will reliably bring this item back to Russell besides him going to this exact session and clearing the gate.
   Closing the session now would also cost you the browser tab you just left open: a launched session like this one owns its tabs by its own PID (see browser-chauffeur's **Tying tab ownership to a session**), and the sweep reaps an owned tab the moment its owning session ends.
   End your turn here instead of closing up.

Russell resumes this the same way he'd continue any open session.
He finds this session waiting in his worker tabs, clears the gate, and tells it so.
From there, resume exactly like message-draft's own `HELP_NEEDED` flow describes: re-find the tab with the `findTab` predicate, re-orient with a fresh read to confirm you're past the gate (browser-chauffeur's **After the user resolves the blocker** covers a tab left on the next sign-in step), and continue the flow from where you stopped.
Then finish normally: complete the remaining work, draft any reply, clear the item per worker-core §6, and close up per §6's closing rules once your part and his are both done.

## A site that times out

A **timer site** is one whose sign-in or checkout expired before Russell reached the page staged for his final press.
Every sign-in along the way is a gate, parked as above.

1. **Mark the site the first time it expires.**
   Add the comment `Timer site: <hostname> - drainer engine/browser-gate.md, "A site that times out"` to the card tracking the work (trello provider, "Cache back to the card"), or write the same line to the provider record that tracks it.
   A later run of that work reads the line with the card or record, and starts at step 2 on its first stage.
2. **Build the batch on the next stage.**
   Re-stage up to the final button with one capture script in `.tmp/`, each run writing into its own new folder.
   On every page it takes a screenshot and writes that page's JSON file, a flat object of label to value: each field that will be submitted, and each amount, name, date, and term the page shows, leaving out per-session tokens, IDs, and clocks.
   Give each field its own label, adding the field name or row number where labels repeat.
   Keep secrets out of the screenshot and the JSON: mask passwords, security codes, and all but the last four digits of card and bank numbers.
   Save the batch, naming the pages in order and every file staged for upload:
   `python <skill>/scripts/submit_batch.py save --manifest <path> --page <page1.json> --page <page2.json> --file <upload>`
   Send every screenshot and every uploaded file to Russell in one `SendUserFile` call, and ask for his go-ahead on this submission.
3. **Wait for his live go-ahead.**
   It is the one he gives in this session, against the batch he has just seen, to submit this submission once the re-staged flow matches it.
   It covers the sign-in he does next, in that same sitting.
4. **Re-stage, check, then press once.**
   After he signs in, run the same capture script again, and once that run has captured every page, compare it with the batch:
   `python <skill>/scripts/submit_batch.py check --manifest <path> --page <page1.json> --page <page2.json> --file <upload>`
   Compare each new screenshot with the batch's too.
   When the check prints `MATCH` and the screens show the same thing, press the final button with a script that first re-reads the final page's values and clicks only when they equal the ones just checked.
   Any difference stops the submission: show Russell what changed, and go back to step 2 for a new batch and a new go-ahead.
   The go-ahead is spent on that one press, so read the confirmation page to learn the result before doing anything further.
