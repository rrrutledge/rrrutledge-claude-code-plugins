---
name: phone-mode
description: Russell's switch for being away from the computer. Use when he says "phone mode on", "phone mode off", "I'm on my phone", or "I'm back at my computer", when a message or hook says phone mode is on and this session waits on or reaches a browser sign-in only he can do, and whenever a drainer worker reaches such a sign-in. It parks that work on a Trello card for later.
---

# Phone mode

While phone mode is on, work that needs a browser sign-in from Russell goes on a Trello card starting an hour out, and the session closes.
The card is an ordinary card, so the Trello drain picks it up when its Start arrives.

Below, `phone_mode.py` stands for `python "${CLAUDE_PLUGIN_ROOT}/skills/phone-mode/scripts/phone_mode.py"`.

## Flip the switch

- **"Phone mode on" or "I'm on my phone":** run `phone_mode.py on` with a 600000 ms timeout, then tell Russell what it printed.
- **"Phone mode off" or "I'm back at my computer":** run `phone_mode.py off` and tell him.
  Each parked card comes up within the hour it was given.

## Park a sign-in

A park starts at a page asking Russell to sign in, in the browser this session drives.
Browser work that needs no sign-in, or runs on a page already signed in, carries on.
Phone mode being on is Russell's say-so to park, so run these steps without asking him.

1. **Check the switch** with `phone_mode.py status`.
   When it prints `off`, handle the sign-in as usual.
2. **Write the card text** to a `.tmp/` file: what the work is and its source link, the sign-in URL, how far it got, the next step, and any resume command this session was handed to run when done.
3. **Park the work.**
   A session working from a Trello card parks that card, and so does a session whose work another card already tracks, per the pod `CLAUDE.md` rule "One deliverable, one card":

   `phone_mode.py park --card <card id> --desc-file <file>`

   Work with no card gets a new one on the board it belongs to per `trello-boards.yaml` (the `trello-outreach` skill's board registry), Personal Follow-Up by default:

   `phone_mode.py park --board <board id> --name "<card name>" --desc-file <file>`

   When the script asks for a list, add `--list "<list name>"`.
4. **Close up.**
   Close the browser tabs this session opened (browser-chauffeur's `chauffeur.py --close-owned`), then:
   - **A drainer worker** clears its item once the card covers all of the item's open work (worker-core §2d), then closes per worker-core "Close silently".
     On a Trello item, the park is the CLEAR.
   - **A session Russell is talking to right now** tells him what it parked and closes by the normal close rules.
   - **Any other session** closes with `session-mgr:close`.
