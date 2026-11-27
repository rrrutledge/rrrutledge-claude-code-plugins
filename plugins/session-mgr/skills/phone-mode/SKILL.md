---
name: phone-mode
description: Russell's switch for being away from the computer. Use when he says "phone mode on", "phone mode off", "I'm on my phone", or "I'm back at my computer", when a message or hook says phone mode is on and this session waits on or reaches a browser sign-in only he can do, and whenever a drainer worker reaches such a sign-in. It parks that work on a Trello card for later.
---

# Phone mode

While phone mode is on, work that needs a browser sign-in from Russell goes on a Trello card starting an hour out, and the session closes.

Below, `phone_mode.py` stands for `python "${CLAUDE_PLUGIN_ROOT}/skills/phone-mode/scripts/phone_mode.py"`.

## Flip the switch

Run the command that matches what Russell says.

- **"Phone mode on" or "I'm on my phone":** run `phone_mode.py on` with a 600000 ms timeout, then tell Russell what it printed.
  When this session is itself waiting on a sign-in, park it next.
- **"Phone mode off" or "I'm back at my computer":** run `phone_mode.py off` and tell him.
  Each parked card comes up within the hour it was given.

## Park a sign-in

A park starts at a page asking Russell to sign in, in the browser this session drives.
Browser work that needs no sign-in, or runs on a page already signed in, carries on.
Phone mode being on is Russell's say-so to park and close, so run these steps without asking him.

1. **Check the switch** with `phone_mode.py status`.
   When it prints `off`, handle the sign-in as usual.
2. **Write the card text** to a `.tmp/` file: what the work is and its source link, the sign-in URL, how far it got, and every step the session still has open, an item CLEAR the sign-in blocks included.
   Add what the session that finishes the card needs from this one: a resume instruction this session was handed, copied whole with absolute paths, and a `Recurs:` card's current Start, which its next occurrence counts from.
   Copy each file that session needs into the main checkout's `.tmp/`, which outlives this session's worktree, and name it in the text.
3. **Park the work.**
   Each deliverable has one card, so a session working from a Trello card, or whose work an open card already tracks, parks that card:

   `phone_mode.py park --card <card id> --desc-file <file>`

   Work with no card gets a new one on the board it belongs to per `trello-boards.yaml` (the `trello-outreach` skill's board registry), Personal Follow-Up by default:

   `phone_mode.py park --board <board id> --name "<card name>" --desc-file <file>`
4. **Close as soon as the park prints its card link**, in every session, one Russell is talking to included.   - **A drainer worker** first clears its item, once the card covers all of the item's open work (worker-core §2d), then closes per worker-core "Close silently".
     On a Trello item, the park is the CLEAR, and a CLEAR the sign-in blocks waits on the card.
   - **Any other session** finishes the way its brief or worker contract names, and with `session-mgr:close` when it names none.

   A session closes the browser tabs it opened (browser-chauffeur's `chauffeur.py --close-owned`) just before it closes.
