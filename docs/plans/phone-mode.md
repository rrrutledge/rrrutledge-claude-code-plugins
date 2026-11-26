# Phone mode

## Recommendation

Add a phone-mode switch to the drainer plugin: one flag file, one script that owns every mechanical step, and one short skill that flips it.
While it's on, a session that reaches a browser sign-in only Russell can do parks that work on a Trello card starting an hour out, clears its item, and closes.
Turning it on also tells every session already open, so one sitting on a sign-in parks itself right away and the rest know for later.
Turning it off pulls every parked card's Start to now, so the poller launches fresh workers on them right away.
Workers keep launching as usual in phone mode, because most items finish without a sign-in and a worker that reaches one parks it in a few turns.

## What triggers a park

A browser page asking Russell to sign in, in the browser the session drives.
Browser work that needs no sign-in, or runs on a page already signed in, carries on as normal.

## The switch

Phone mode has three parts.

- **Flag file holds the state:** `~/.claude/phone-mode.json` holds `on_since` and the list of cards parked while it was on.
  No file means off.
- **One script does the mechanics:** `plugins/drainer/skills/drainer/scripts/phone_mode.py`, reusing the Trello adapter's way of locating `trello_utils.py`.
  - `on` writes the flag and notifies the open sessions (below).
  - `off` runs the turn-off sweep (below) and deletes the flag.
  - `status` prints `on` or `off`.
    While on, it also moves any parked card whose Start has arrived another hour out and records the new Start in the flag, so no worker launches just to hit the same sign-in again.
  - `park --board <id> --name <text> --desc-file <path>` creates the card with Start an hour from now and records it in the flag; `park --card <id>` moves an existing card's Start the same way.
- **Flipping it from the phone:** a `/drainer:phone-mode` skill whose description triggers on "phone mode on", "phone mode off", "I'm on my phone", and "I'm back at my computer".
  Russell says it in any session he has open over Remote Control, and the skill runs one `phone_mode.py` call.
  The skill also carries the park steps for any session, drainer worker or not, that reaches a sign-in.

## Reaching the sessions already open

`on` sends a note to every live session in `claude agents --json` except the one that ran it, using the poller's verified session-note delivery (`session_address` and the hand-over it feeds in `run-poller.py`).
The note says phone mode is on and points to `/drainer:phone-mode`.
A session waiting on Russell to sign in parks itself as soon as the note arrives.
Every other session reads it, keeps working, and parks if it reaches a sign-in later.

## What a park does

A park runs through these pieces.

- **The drainer gate checks the flag first.**
  `engine/browser-gate.md` (the drainer worker's rule for a page only Russell can get past) opens with `phone_mode.py status`.
  When off, it leaves the page open and waits for Russell, as it does today.
  When on, it follows the phone-mode skill's park steps.
- **The session writes and files the card.**
  The session writes the card's name and description: what the work is, the sign-in URL, how far it got, and the next step.
  It runs `park`, and the card counts as the item's follow-up card under worker-core §2d, so the session clears the item, closes its tabs, and closes itself with no digest entry.
  A Trello-sourced item moves its own card's Start with `park --card`, per the pod's one-deliverable-one-card rule.
- **The board the work belongs to:** per `trello-boards.yaml`, Personal Follow-Up by default, chosen by the session.
- **A parked card arriving while still on:** the poller runs `phone_mode.py status` at the start of each feed cycle.
- **Awareness in new prompts:** a drainer-plugin `SessionStart` and `UserPromptSubmit` hook adds one line ("Phone mode is on: park a browser sign-in per `/drainer:phone-mode`") while the flag is on.

## Turning it off

`off` sets Start to now on every card in the flag's parked list whose Start still matches the one the flag recorded, then deletes the flag.
A card someone has since moved keeps its date.
The poller's next Trello cycle dispatches a fresh worker on each card, and the card's description carries the sign-in URL and progress for that worker to resume from.
The worker buffer throttles them like any other startable cards.

## Rollout

One PR on this repo, a minor bump for the drainer plugin:
`phone_mode.py` with tests, the `phone-mode` skill, the awareness hook, the gate rule's flag check, the poller's per-cycle `status` call, and a worker-core §2e pointer to the skill.
