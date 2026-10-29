---
skill: trello
description: The Trello API client - use it for ANY Trello read or write (cards, comments, labels, checklists, lists, blocking) through trello_utils.py. Never call the Trello REST API directly. The outreach funnel, board registry, and when-to-open-a-card policy are the separate trello-outreach skill.
instructions: |-
  ## Trello client

  This is the Trello API client for any Trello board. Every Trello read and every Trello write goes
  through `trello_utils.py` here: cards, comments, labels, checklists, lists, and the blocking model.
  It owns the mechanics - credentials, the base URL, a bounded request timeout, and read-after-write
  verification - so a caller never handles any of that itself.

  Reach for `trello_utils` from a `.tmp/` Python script (or the drainer's adapter, which imports it):
  call `get_trello_session()` once, then a typed wrapper, or `trello_request()` for anything without a
  wrapper yet.

  **Talk to Trello only through `trello_utils`, never the REST API directly.** No `curl`, `fetch`, or
  `WebFetch` to `api.trello.com` - the credentials sitting in the environment make a raw call tempting,
  and it skips the shared auth, timeout, and verification the wrapper guarantees. A raw write to
  `api.trello.com` is blocked by the safe-compounds hook, which points back here; a raw read defeats the
  same plumbing even where it isn't blocked. When an operation has no wrapper, `trello_request()` is the
  right entry point - it still routes through the shared session, base URL, and timeout.

  Board configuration - board names, IDs, purposes, per-board lists and labels, template-card notes - and
  the policy for **when and why** to open or advance a card live in the **`trello-outreach`** skill and
  the project's `trello-boards.yaml` registry. This skill is purely the mechanics layer.

  ---

  ## Scripts

  All scripts are in the `scripts/` directory next to this SKILL.md file.

  ### trello_utils.py

  The shared Trello client library. Import it from your `.tmp/` scripts:

  ```python
  sys.stdout.reconfigure(encoding='utf-8')  # required on Windows for Unicode output
  sys.path.insert(0, '<path-to-skill>/scripts')
  from trello_utils import (
      get_trello_session, trello_request,
      get_board_lists, get_board_cards, get_board_labels,
      create_card, update_card, delete_card, retire_card, add_comment,
      create_label, add_label_to_card, get_or_create_list,
      find_card_by_name, create_card_from_template,
  )
  ```

  `get_trello_session()` reads TRELLO_API_KEY and TRELLO_TOKEN from the environment or Windows Credential
  Manager - no credential handling needed in the script.

  `add_comment(card_id, text, session)` posts a card comment - the dated status comment every advance or
  nudge writes. Anything without a typed wrapper (checklists, and any endpoint added later) goes through
  `trello_request(method, path, session, params=?, body=?)` directly, which still carries the shared
  session, base URL, and timeout.

  ### update-trello-from-transcript.py

  Batch-update cards based on meeting transcript follow-ups. See the script's docstring for the full
  config JSON format.

  ```bash
  python <path-to-skill>/scripts/update-trello-from-transcript.py config.json
  ```

  ---

  ## Blocked cards

  Creating or editing a card that's blocked on another card finishing: apply the ⛔ Blocked label and add
  a `Blocked-by: <upstream-shortlink>` line to the description, together, in the same operation. The
  startable-task dependency model (push-unblock via `cascade_unblock`, cross-board pull-unblock via
  `sweep_unblock`) lives in `trello_utils.py` - see the module's own docstrings for how a blocked card is
  freed once its blockers finish.

  ---

  ## Ending a card - move it, never archive it

  A card's work ends with a move to a terminal list.

  - **When the work is done** - move the card to **Finished**.
    Done means the card's outcome is reached: every step the card exists for is complete, with nothing left for anyone to do or decide.
    A milestone short of that (an application submitted, a draft sent) leaves the card open.
    A card waiting on a third party (an agency decision, a reply, a follow-up document) or on another session stays in an active list with its Start set to when to check, so the drainer surfaces it again.
  - **When you take up work on a card sitting in a terminal list** - move it back to an active list first, so the drainer sees it as live work again.
  - **When the work won't finish on this card** - call `retire_card(card_id, reason, session)`.
    It comments the reason on the card, so name where the work went and link to it, then moves the card to **Abandoned**.

  Archiving an unfinished card hides its task.
  The drainer reads only open cards, the board stops showing it, and an archived card counts as a finished blocker, so anything waiting on it gets unblocked.
  `trello_utils` refuses to archive or delete any card outside a terminal list (`TERMINAL_LIST_SUBSTRS` in `trello_utils.py` names them) and raises `ArchiveRefused`.
  The guard sits inside `trello_request`, so a call made directly through `trello_request` is refused too.
  Archiving a card that already sits in a terminal list goes through as usual.

  ---

  ## Guarding a new card against the drainer

  The drainer's Trello provider treats any card with no Start date as immediately **startable now** -
  by design, the drainer is "hungry to start." A card created with no Start is therefore eligible for
  pickup on the very next drain cycle, which can race a session that's still actively working the same
  task it just created the card for.

  **Give the card a real Start date at creation, every time** - Start is the one date the drainer reads,
  the "work-on-it / go-live" day, for outreach follow-ups and task cards alike:

  - **If the true next-touch date is already known** (a stated deadline, a fixed cadence), use it
    directly.
  - **Otherwise - the common case, since a card is usually created before its first outbound message
    even goes out - pad the Start out beyond any plausible single working session.** A week is a safe
    margin: a near-term Start (e.g. "tomorrow") can still get raced if the creating session runs
    long - left open over a weekend, two-plus days can pass with the session still mid-task and the
    card already past its guard date. The padded Start isn't a business date; it exists purely to keep
    the drainer off the card while it's still being worked.

  **Correct the date the moment the sitting actually ends** - the message sends, the task is handed
  off, whatever "done for now" means for this card - to the real next-touch date per the nudge cadence,
  exactly as CLEAR already does. A padded date left uncorrected is a safe failure (the card just
  surfaces a week later than it ideally would), not a silent drop, but don't rely on that - correct it
  as soon as the guard is no longer needed.

  ---

  ## Start vs Due

  Trello cards carry two distinct native date fields - don't confuse them:

  - **`start`** is the one date the drainer's queue reads (see the drainer's `trello-provider.md` for the
    full model) - it's the "work-on-it / resurface" day. Every reschedule (a nudge, a hold-back, an
    advance, a Russell-requested date change) means setting `start`.
  - **`due`** is a separate, purely informational deadline. Nothing in this system reads it to decide when
    a card resurfaces. Set it alongside `start` when a card genuinely has an external deadline worth
    displaying, but setting `due` alone and expecting the card to nudge, hold back, or resurface on that
    date does nothing - the queue never looks at it.

  ```python
  update_card(card_id, {'start': '2026-08-28'}, session)  # reschedules when it resurfaces
  update_card(card_id, {'due': '2026-08-28'}, session)     # cosmetic only - does not reschedule anything
  ```

  ---

  ## Checklists

  Checklists have no typed wrapper - use `trello_request` directly:

  ```python
  checklist = trello_request('POST', '/checklists', session, body={'idCard': card_id, 'name': 'Actions'})
  trello_request('POST', f'/checklists/{checklist["id"]}/checkItems', session, body={'name': 'Contact identified', 'pos': 'bottom'})
  ```

  ---

  ## Card templates

  Some boards keep **template cards** - real cards flagged `isTemplate: true` that hold a reusable
  description and set of checklists. The template *definition lives on the board itself*, not in code: to
  change the process, edit the template card in Trello. This skill only knows how to *apply* a template.

  Which boards have templates, the template card names, and when to apply each one are board-specific -
  read `trello-boards.yaml`.

  To create a new card from a template, copying its checklists:

  ```python
  tpl = find_card_by_name(board_id, 'TEMPLATE: Sponsorship Renewal', session)
  card = create_card_from_template(list_id, tpl['id'], 'Acme Sponsorship Renewal 2027', session)
  # keep defaults to 'checklists'; pass keep='checklists,labels,attachments' for more
  ```

  Template cards are returned by `get_board_cards` like any other card (flagged `isTemplate`), so filter
  them out when iterating real cards:
  `[c for c in get_board_cards(...) if not c.get('isTemplate')]`.

  A template card still renders as a normal (badged) card in whatever list it lives in - it is **not**
  hidden from the board. To keep templates out of an active funnel, park them in a dedicated list (e.g. a
  "Templates" list). The list they sit in doesn't matter to `find_card_by_name` - it matches by name.

  ---

  ## Verification output

  End every script with a printed summary and exactly one of:
  - `Verification passed ✅`
  - `Verification FAILED - N errors`

  Exit code 0 does not mean success - trust the printed output.

  ---

  ## Verifying mutations (read-after-write)

  Trello's bulk `GET /boards/{id}/cards` endpoint can return STALE `idLabels` (and other card fields) for
  some time after a mutation - observed in practice as recently-added labels not appearing in bulk reads
  even when correctly applied. The per-card endpoint `GET /cards/{id}` is authoritative and returns fresh
  data immediately.

  After any mutation (label add/remove, comment, card move, etc.), verify with a per-card GET - never
  re-fetch bulk board cards to check whether a mutation took effect:

  ```python
  trello_request('POST', f"/cards/{card_id}/idLabels", session, body={'value': label_id})
  for attempt in range(3):
      time.sleep(0.5)
      check = trello_request('GET', f"/cards/{card_id}", session, params={'fields': 'idLabels'})
      if label_id in check.get('idLabels', []):
          break
  ```

  This pattern also doubles as a free retry - Trello mutations occasionally fail silently (no exception
  raised) and a re-POST resolves it.

  ---

  ## Label colors

  `green`, `yellow`, `orange`, `red`, `purple`, `blue`, `sky`, `lime`, `pink`, `black`
---
