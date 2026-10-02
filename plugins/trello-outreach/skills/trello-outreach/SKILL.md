---
skill: trello-outreach
description: The outreach-funnel process layer over Trello - when and why to open, advance, dedupe, and route cards across the outreach boards, plus the board registry and initiative model. The Trello API mechanics (card/comment/label/checklist CRUD, the client library) are the separate `trello` skill.
instructions: |-
  ## Trello outreach

  The process layer for running outreach on Trello boards: when and why to open a card, how to keep one
  card per entity, which board a card belongs on, and the initiative model behind the funnel. The Trello
  mechanics - card, comment, label, checklist, and list operations, the client library, and the API
  gotchas - are the **`trello`** skill. This skill decides *what* to do; `trello` carries it out through
  `trello_utils.py`. Every Trello read or write still goes through `trello`, never the REST API directly.

  Board configuration - board names, IDs, purposes, per-board lists and labels, and template-card notes -
  lives in the project's `trello-boards.yaml` registry, the single source of truth shared with the
  drainer's trello provider. Read it first.

  ---

  ## Before creating a card - search the boards first

  When the thing to track is a **named entity** - a sponsor, a company, a contact, a target role - it
  very likely already has a card somewhere in the funnel; that's what the outreach boards are. So before
  creating one, **search every board in `trello-boards.yaml` for an existing card for that entity, and
  update that card instead.** Creating is the fallback once the search comes up empty, never the first
  move. (This applies to any card keyed to a person or organization; a genuinely new one-off admin task
  with no entity behind it can skip straight to creating.)

  ```python
  # trello_utils comes from the `trello` skill's scripts/ directory
  from trello_utils import get_trello_session, get_board_cards
  s = get_trello_session()
  BOARDS = {  # from trello-boards.yaml - every board, so the search is complete
      'Summit Sponsorship Outreach': 'mvL6EGtH',
      'Executive Director Outreach': 'hpvdRw3G',
      # ...the rest of the registry...
  }
  entity = 'Contoso'
  hits = [(b, c) for b, bid in BOARDS.items()
          for c in get_board_cards(bid, s)
          if entity.lower() in (c.get('name', '') + ' ' + c.get('desc', '')).lower()]
  ```

  - **A card exists → update it, don't duplicate.** Fold the new development into the existing card:
    advance/reschedule it, post a dated comment recording what happened (via `add_comment`), and set the
    next Start date. The existing card is the source of truth and carries the entity's whole history - keep
    it there.
  - **Nothing matches → create on the entity's OWN board.** A sponsor/company or its contact belongs on
    that campaign's outreach board (match by the registry `purpose`/`initiative`), not as a generic
    reminder on a catch-all board. Put the card where that entity's funnel lives so it isn't orphaned
    from its history.

  **A freshly-created card with no Start is eligible for drainer pickup on the very next cycle** -
  see the `trello` skill's "Guarding a new card against the drainer" for setting a real Start date at
  creation (padded out when the true follow-up date isn't known yet) so a session still working the
  card isn't raced by a worker.

  ---

  ## Two card types on the Job Search Outreach board

  The Job Search Outreach board (`phRXnOvf`) holds two kinds of card, and keeping them separate is what
  keeps each follow-up reliably scheduled - a card carries one Start date, so it tracks exactly one clock.

  - **Application card** - one target role. It carries no future Start: it stays startable-now until the
    application is in, is finished in a single session, then moves to Finished / Abandoned and rests. Every
    wait a role seems to need - a referrer's name, a recruiter who has gone quiet - is really a wait on a
    person, so that clock lives on the person's card, never the role card. Created by the job-board poller
    (always in `Identified`, always wearing a `P1`/`P2`/`P3` fit label) or by the apply-for-job flow. It
    never wears the `👤 Contact` label.
  - **Person follow-up card** - one network contact. Its Start tracks the follow-up cadence with that one
    person (the nudge cadence in the CLEAR guidance). One card per contact, reused across every role they
    help with - a referrer who spans four applications is one card, not four, the same way the referrers
    list compounds. It wears the **`👤 Contact`** label and never a `P1`/`P2`/`P3` label.

  **Outreach for a role lives on the contact's person card, linked to the application card.** When a
  warm intro for a role goes to a contact, open or update that contact's person card (search first, per
  above), set its Start to the follow-up cadence, and link the two by URL - the application card names the
  contacts helping it, the person card names the role(s) it's helping. Bundling several contacts' threads
  onto one application card is what drops follow-ups: four contacts are four clocks, and a card's one Start
  can hold only one. Every contact you reach gets their own person card, even a single cold ask you
  expect nothing back from - reaching out to someone starts their follow-up clock, and a card is where
  that clock lives so the follow-up can never be dropped.

  **Person cards lead the queue.** A `👤 Contact` card is worked ahead of the inbox and every application
  tier, so following up with an existing contact always beats starting or chasing an application. That
  banding lives in the drainer's `trello-adapter.py _PRIORITY_BAND`, keyed on the `👤 Contact` label.

  ## Mechanics live in the `trello` skill

  Everything about *carrying out* a Trello change is in the **`trello`** skill: the `trello_utils.py`
  import pattern, card/comment/label CRUD, checklists, card templates, the ⛔ Blocked dependency model,
  read-after-write verification, and the label-color vocabulary. Open a card, advance one, post a comment,
  apply a template, or set a blocked relationship through that skill's wrappers.
---
