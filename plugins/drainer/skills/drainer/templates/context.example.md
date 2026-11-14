# drainer context - <USER>'s world (the shared brain for every drainer source)

> **This is a TEMPLATE.**
> Copy it to `context.md` in your `local_dir` and fill it in for this machine.
> It is the per-machine "brain" the worker reads at the start of every item.
> Keep it **small and durable** - stable facts about who the user is and where things live, not a growing pile of per-case notes.
> When the user has to tell you something you could have known, improve the *source* it should have come from (a system, a skill, the internal knowledge source) rather than appending here.

**This file is facts to reason with.**
The generic handling rules - how to classify an item and how to carry one out - live in the shipped engine (`engine/worker-core.md` §0 says which is which); this file holds only what's true about the user in particular.

## Who / role
- <Name>. <Role / what they do / the org they're in.>
- Focus areas: <the few things most of their inbound is about>.

## Systems the user acts in (where actions usually land)
- <System> (<how to access it - CLI / API / MCP>) - <what it's for>.
- <System> - <…>.
- <Tracker board> - where "waiting on someone else" follow-ups go (board id in `.claude/drainer.local.md`).
- <Internal knowledge source, if any> - the thing to ask first for org-internal "how does X work" questions before asking the user.
  (Omit if none.)

## Standing personal dispositions
- <A sender, list, or account and how the user wants its mail handled - e.g. "school newsletters: fyi with a summary">.
