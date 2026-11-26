# Phone mode

## Recommendation

Add a phone-mode switch to the session-mgr plugin, which every session loads, so it works the same in a drainer worker, a handoff, and a session Russell started by hand.
While it's on, a session that reaches a browser sign-in only Russell can do puts that work on a Trello card starting an hour out and closes.
The card is an ordinary card: when its Start arrives, the Trello drain picks it up like any other.
A card that comes up while phone mode is still on hits the same sign-in, and its worker parks it another hour out the same way.
Turning phone mode on also tells every session already open, so one sitting on a sign-in parks itself right away.

## What triggers a park

A browser page asking Russell to sign in, in the browser the session drives.
Browser work that needs no sign-in, or runs on a page already signed in, carries on as normal.

## The switch

The only state is whether phone mode is on, so it lives in one marker file, `~/.claude/phone-mode`: present means on.
A session reaching a sign-in reads it to decide between parking and waiting for Russell.
The switch has four parts.

- **One script does the mechanics:** `plugins/session-mgr/scripts/phone_mode.py`.
  - `on` creates the marker and notifies the open sessions (below).
  - `off` deletes the marker.
  - `status` prints `on` or `off`.
  - `park --board <id> --name <text> --desc-file <path>` creates a card with Start an hour from now; `park --card <id> --desc-file <path>` moves an existing card's Start an hour out and adds the description as a dated comment.
- **Flipping it from the phone:** a `/session-mgr:phone-mode` skill whose description triggers on "phone mode on", "phone mode off", "I'm on my phone", and "I'm back at my computer".
  Russell says it in any session he has open over Remote Control, and the skill runs one `phone_mode.py` call.
- **The park steps live in that skill,** so every kind of session follows the same ones.
- **Awareness in new prompts:** session-mgr's existing `SessionStart` hook, plus a `UserPromptSubmit` one, adds the line "Phone mode is on: park a browser sign-in per `/session-mgr:phone-mode`" while the marker is present.

## Reaching the sessions already open

`on` sends a note to every live session in `claude agents --json` except the one that ran it, using the same session-to-session delivery the drainer's poller uses to hand an item to a live worker.
The note says phone mode is on and points to `/session-mgr:phone-mode`.
A session waiting on Russell to sign in parks itself as soon as the note arrives.
Every other session keeps working and parks if it reaches a sign-in later.

## The park steps

A session that reaches a sign-in runs these steps.

1. Check `phone_mode.py status`; when off, ask Russell to sign in as usual.
2. Write the card's name and description: what the work is, the sign-in URL, how far it got, and the next step.
   The card goes on the board the work belongs to per `trello-boards.yaml`, Personal Follow-Up by default.
3. Run `park`.
   A session working from a Trello card parks that card with `park --card`, per the pod's one-deliverable-one-card rule.
4. Close the browser tabs.
   A drainer worker clears its item, since the card now tracks the work under worker-core §2d, and closes the session.
   A session Russell is talking to right now tells him what it parked and closes by the normal close rules.

The drainer's `engine/browser-gate.md` points to the skill for all four steps.

## Turning it off

After `off`, each parked card comes up within the hour it was given, and its worker finds phone mode off and asks Russell to sign in as normal.
The hour's wait is the whole cost of tracking nothing.

## Rollout

One PR on this repo, with minor bumps for session-mgr and the drainer:
`phone_mode.py` with tests, the `phone-mode` skill, the awareness hook, and the `browser-gate.md` pointer.
