# drainer browser gate - report HELP_NEEDED, not a silent stall

Read this when browser-chauffeur work in this session hits a gate only Russell can clear.
`<skill>` means this drainer skill's root folder, the same absolute path your seed prompt gave you for `worker-core.md`.

Any browser-chauffeur work this session drives - resolving a pointer's real content (`engine/pointers.md`), doing the item's work (worker-core step 3), staging a draft via message-draft (worker-core step 4), or a provider's browser-driven CLEAR - can hit a gate browser-chauffeur's own contract already defines (see browser-chauffeur's SKILL.md, **User Intervention** and **Running in a subagent → The return contract**).

Report that gate as `HELP_NEEDED` to the digest instead of following browser-chauffeur's `AskUserQuestion` step or waiting on a `HELP_NEEDED` result from a subagent you spawned (message-draft's teams/slack modes run one, per worker-core step 4).
Both of those assume someone is watching this session live to answer.
A drainer worker runs unattended, so nobody sees the prompt, the session parks on a question nobody will ever answer, and the item sits stuck with no signal Russell can find.
That's the stall this file closes.

Report the gate up to the one channel that reaches Russell without anyone watching live, the digest:

1. **Leave the browser tab open** on the gate page.
   Don't retry past it.
   If a subagent you spawned already returned `HELP_NEEDED` with a `findTab` locator, that locator is what re-finds the tab later, so record it rather than losing it.
2. **Record the gate on the item.**
   Edit `items/<id>.json` (Edit tool) and add a `helpNeeded` object: `reason` (login / CAPTCHA / MFA-to-phone / in-page action needing a human), `url`, the `findTab` predicate description that re-finds the tab, and `progress`, one line on how far the run got before the gate stopped it.
3. **Queue a digest entry.**
   Set `triage` to `"help-needed"` in `items/<id>.json` (the same after-the-fact re-tag worker-core §2c and §6a use for fyi and auto-handle), then run
   `node <skill>/scripts/seen-state.js queue-add <runtime_dir> <source> <id> <path to items/<id>.json>`
   so Russell actually sees it.
   The digest reads `triage: "help-needed"` as its own class (see `digest-core.md` §2a), distinct from fyi, junk, and auto-handled.
4. **Leave the source item uncleared.**
   The task isn't done, and clearing it here would drop it the way worker-core §2d warns against.
5. **Keep this session open.**
   A staged gate is squarely the "waiting on an answer from him" case worker-core §6's session-closing guidance carves out: nothing else will reliably bring this item back to Russell besides him going to this exact session and clearing the gate.
   Closing the session now would also cost you the browser tab you just left open: a launched session like this one owns its tabs by its own PID (see browser-chauffeur's **Tying tab ownership to a session**), and the sweep reaps an owned tab the moment its owning session ends.
   End your turn here instead of closing up.

Russell resumes this the same way he'd continue any open session.
The digest points him to this item and names its worker's session as the one waiting, so he opens this session in the Claude app or at [claude.ai/code](https://claude.ai/code) and tells it he's cleared the gate.
From there, resume exactly like message-draft's own `HELP_NEEDED` flow describes: re-find the tab with the `findTab` predicate, re-orient with a fresh read to confirm you're past the gate, and continue the flow from where you stopped.
Then finish normally: complete the remaining work, draft any reply, clear the item per worker-core §6, and close up per §6's closing rules once your part and his are both done.
