# Phone mode

## Recommendation

Add a phone-mode switch to the drainer plugin: one flag file, one script that owns every mechanical step, and one short skill that flips it.
While it's on, any session that reaches a step only Russell's hands at the computer can do parks that step on a Trello card, clears its item, and closes.
Turning it off pulls every parked card's Start to now, so the poller launches fresh workers on them right away.
Workers keep launching as usual in phone mode, because most items finish without the computer and a worker that hits a gate parks it in a few turns.

## What counts as "needs the computer"

One definition, stated in the new skill and pointed to from every other file:
a step only Russell can do, physically at this machine.
That covers a browser sign-in, a CAPTCHA, an MFA code texted to his phone that the session can't read, an in-page action needing a human, and pressing a staged final button (a form's Submit) in the computer's browser.
A draft he can send from his phone (Gmail, Outlook, Slack) and a question he can answer in chat stay normal work.

## The switch

- **Flag file:** `~/.claude/phone-mode.json` holds `on_since`, an optional `until`, and the list of cards parked while it was on.
  No file means off.
  An `until` in the past reads as off.
- **Script:** `plugins/drainer/skills/drainer/scripts/phone_mode.py`, reusing the Trello adapter's way of locating `trello_utils.py`.
  - `on [--for 2h | --until "9 PM"]` writes the flag.
  - `off` runs the turn-off sweep below and deletes the flag.
  - `status` prints `on until <time Central>` or `off`, and runs the sweep once when it finds the flag expired.
  - `park --board <id> --name <text> --desc-file <path>` creates the card with the parked Start and records it in the flag; `park --card <id>` moves an existing card's Start the same way.
- **Flipping it from the phone:** a `/drainer:phone-mode` skill whose description triggers on "phone mode on", "phone mode off", "I'm on my phone for 2 hours", and "I'm back at my computer".
  Russell says it in any session he has open over Remote Control, and the skill runs one `phone_mode.py` call.
- **Expiry without anyone typing:** the poller runs `phone_mode.py status` at the start of each feed cycle, so an expired `until` triggers the sweep within one cycle.

## What changes while it's on

- **Gate checks the flag first.**
  `engine/browser-gate.md` opens with `phone_mode.py status`.
  When off, it parks in the open tab exactly as it does today.
  When on, the worker writes the card's name and description (the judgment part: what the work is, the gate URL, how far it got, the next step), runs `park`, and the item counts as tracked under worker-core §2d.
  The worker then clears the item, closes its tabs, and closes the session per "Close silently", with a digest entry so the evening digest shows what was parked.
  A Trello-sourced item moves its own card's Start with `park --card` instead of making a second card, per the pod's one-deliverable-one-card rule.
- **The board** is the one the work belongs to per `trello-boards.yaml`, Personal Follow-Up by default, chosen by the worker.
- **The parked Start** is the `until` time when one is set, and 8 AM Central the next morning when none is.
  The turn-off sweep pulls it forward when he's back sooner.
- **Worker-core** §2e and §3's "ask only his own hands can finish" point to the gate rule's phone branch, so a staged browser button parks the same way.
- **Non-drainer sessions** get the same branch in browser-chauffeur's **User Intervention** section, so an interactive pod session he's driving from his phone parks on a card instead of asking him to sign in.
- **General awareness** comes from a drainer-plugin `SessionStart` and `UserPromptSubmit` hook that adds one line ("Phone mode is on until 7:10 PM Central: park computer-only steps per `/drainer:phone-mode`") while the flag is on.
  The hook prints nothing when off.
  The gate rule's own `status` check is what makes long-running sessions behave correctly, since a parked worker sees no new prompt to trigger the hook.

## Turning it off

`off` (or expiry) sets Start to now on every card in the flag's parked list whose Start is still the one `park` set, leaving any card someone has since moved alone, then deletes the flag.
The poller's next Trello cycle dispatches a fresh worker on each card, and the card's description carries the gate URL and progress for that worker to resume from.
The worker buffer throttles them like any other startable cards.

## Rollout

One PR on this repo, a minor bump for the drainer and browser-chauffeur plugins:
`phone_mode.py` with tests, the `phone-mode` skill, the awareness hook, the gate-rule branch in `browser-gate.md`, the worker-core pointers, the poller's per-cycle `status` call, and the browser-chauffeur branch.
## Decision for you

Turning phone mode on leaves the workers already parked in open tabs (the "blocked" sessions in `claude agents`) where they are.
Should `on` also convert each of those to a card and close it, so the worker list is empty while you're away?
