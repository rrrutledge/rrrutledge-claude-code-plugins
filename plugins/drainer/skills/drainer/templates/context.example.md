# drainer context - <USER>'s world (the shared brain for every drainer source)

> **This is a TEMPLATE.**
> Copy it to a local, gitignored `context.md` and fill it in for this machine.
> It is the per-machine "brain" the worker reads at the start of every item.
> Keep it **small and durable** - stable facts about who the user is and where things live, not a growing pile of per-case notes.
> When the user has to tell you something you could have known, improve the *source* it should have come from (a system, a skill, the internal knowledge source) rather than appending here.

## Who / role
- <Name>. <Role / what they do / the org they're in.>
- Focus areas: <the few things most of their inbound is about>.

## Systems the user acts in (where actions usually land)
- <System> (<how to access it - CLI / API / MCP>) - <what it's for>.
- <System> - <…>.
- <Tracker board> - where "waiting on someone else" follow-ups go (board id in `.claude/drainer.local.md`).
- <Internal knowledge source, if any> - the thing to ask first for org-internal "how does X work" questions before asking the user.
  (Omit if none.)

## Staging drafts - use the `message-draft` skill
The **`message-draft`** skill handles all draft mechanics (voice, composer targeting, never-send) for every source.
Just invoke it in the source's mode.

## How to decide the ACTION (process)
1. **Actions-first.**
   Before drafting any reply, ask: is there something to DO in a system?
2. **Situational check first.**
   Is it already handled?
   (PR merged? request done? they replied and the user already answered?)
   That changes the right response.
3. **Check the Drafts folder** before composing - the user often already has a draft going.
4. If you can identify a concrete action, **propose it** and do it on the user's OK (draft-only for anything outbound; never send/post without approval).
5. **Unknown org-internal mechanism?
   Ask the internal knowledge source first** (if `context.md` names one).
   Act on what it returns; fall back to asking the user only if it genuinely doesn't know.
6. If neither you nor the internal source can tell the intended action, ask the user ONE crisp question - then improve the source that should have answered it.
7. When confidence that a drafted reply is correct is low, ask the user directly before drafting.

## Preferences (the hard behavioral rules)
- **Create drafts immediately - don't wait for approval.**
  A draft is safe and fully reversible.
  NEVER click Send / press Enter - the user edits and sends.
  Only irreversible/outbound-to-others actions wait for explicit OK.
- **Learn from every send (voice loop).**
  After the user sends, diff the sent version against the draft and append a concrete lesson to the **document-authoring skill's Voice learning loop**.
- **Delete/archive freely - it's reversible, so do NOT ask first.**
  Narrate each with a one-line reason; clear only once the item is sent/handled/superseded.
- **Newest-first**, one at a time, conversational.
- **fyi / junk: batch into one digest**, summarize, then clear.
  Goal: queue to literal 0.
- Bias to keep only when genuinely unsure; otherwise summarize-and-clear.
