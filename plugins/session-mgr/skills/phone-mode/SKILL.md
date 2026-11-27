---
name: phone-mode
description: Russell's switch for being away from the computer. Use when he says "phone mode on", "phone mode off", "I'm on my phone", or "I'm back at my computer", when a message or hook says phone mode is on and this session waits on him or is about to, and whenever a drainer worker reaches a browser gate. It says which steps phone mode parks, and parks that work on a Trello card for later.
---

# Phone mode

While phone mode is on, work waiting on a step phone mode parks goes on a Trello card starting an hour out.

Below, `phone_mode.py` stands for `python "${CLAUDE_PLUGIN_ROOT}/skills/phone-mode/scripts/phone_mode.py"`.

## Flip the switch

Run the command that matches what Russell says.

- **"Phone mode on" or "I'm on my phone":** run `phone_mode.py on` with a 600000 ms timeout, then tell Russell what it printed.
  When this session is itself waiting on a step phone mode parks, park it next.
- **"Phone mode off" or "I'm back at my computer":** run `phone_mode.py off` and tell him.
  Each parked card comes up within the hour it was given.

## What phone mode parks

This list is the one definition: phone mode parks a step on it, and every other step goes ahead as usual.

- **A browser sign-in for Russell:** a page asking him to sign in, in the browser this session drives.
  Work on a page that needs no sign-in, or is already signed in, goes ahead.

## Park the work

Phone mode being on is Russell's say-so to park and close, so run these steps without asking him.

1. **Check the switch** with `phone_mode.py status`.
   When it prints `off`, handle the step as usual.
2. **Write the card text** to a `.tmp/` file: what the work is and its source link, the parked step and its URL, how far it got, and the next step with everything that waits on it, an item CLEAR the parked step blocks included.
   Add what the session that finishes the card needs from this one: a resume instruction this session was handed, copied whole with absolute paths, and a `Recurs:` card's current Start, which its next occurrence counts from.
   Copy each file that session needs into the main checkout's `.tmp/`, which outlives this session's worktree, and name it in the text.
3. **Put it on the card.**
   Each deliverable has one card, so a session working from a Trello card, or whose work an open card already tracks, parks that card:

   `phone_mode.py park --card <card id> --desc-file <file>`

   Work with no card gets a new one on the board it belongs to per `trello-boards.yaml` (the `trello-outreach` skill's board registry), Personal Follow-Up by default:

   `phone_mode.py park --board <board id> --name "<card name>" --desc-file <file>`
4. **Close at once when the card holds everything left to do**, in every session, one Russell is talking to included.
   When something is left for him that the card doesn't hold, tell him what it is and stay open.
   - **A drainer worker** first clears its item, once the card covers all of the item's open work (worker-core §2d), then closes per worker-core "Close silently".
     On a Trello item, the park is the CLEAR, and a CLEAR the parked step blocks waits on the card.
   - **Any other session** finishes the way its brief or worker contract names, and with `session-mgr:close` when it names none.

   A session closes the browser tabs it opened (browser-chauffeur's `chauffeur.py --close-owned`) just before it closes.
