"""trello poller adapter — outreach boards via the `trello` skill's trello_utils.py (Trello REST).

All Trello mechanics live HERE, alongside the prose contract in `trello-provider.md`: locating
trello_utils.py, reading the `providers.trello` board config out of `.claude/drainer.local.md`, the
Start-date-as-queue enumerate, the `trello-<slug>-<last6>` id scheme, and the captured item shape. The
poller (`scripts/run-poller.py`) loads this adapter dynamically and drives it through the
`ProviderBase` interface — it contains no Trello specifics.

Unlike the gmail/slack/outlook adapters (which enumerate a single inbox), Trello drains several boards,
so this adapter takes a `configure(cfg)` pass from the poller to learn the repo. The board list is the
single source of truth shared with the `trello-outreach` skill: `<repo>/trello-boards.yaml` (name + id
per board). The drainer drains EVERY board in that registry. Per-drainer knobs (`skip_lists` /
`label_vocab`) stay in that repo's drainer.local.md. If no registry file is present, it falls back to a
legacy `providers.trello.boards` block in drainer.local.md.

The **Start date IS the queue** (startable-task model): the native Start date is a card's
"work-on-it / go-live" date, and it is the only date the queue reads. enumerate returns cards in active
lists that are startable and NOT wearing a ⛔ Blocked (skip) label. A card is startable once its Start
has arrived, or immediately when it carries no Start; a future Start is the sole way to defer a card -
"not okay to begin until this day" (see _startable). Cards rank by their Start (their go-live), most
recent first; an undated card ranks by its creation date, decoded from the card's ObjectId, so it sorts
by age. Blocked cards are suppressed until finishing their upstream runs trello_utils.cascade_unblock
(strips ⛔, sets Start=today). Credentials are TRELLO_API_KEY / TRELLO_TOKEN in the environment (read by
trello_utils.get_trello_session).
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

# scripts/ is on sys.path (the poller inserts it); fall back to a relative add when run standalone.
_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from provider_base import (ProviderBase, ProviderError, slug, NEUTRAL_PRIORITY_BAND, band_rank,  # noqa: E402
                           find_skill_script)

# A priority label is exactly "P1"/"P2"/"P3" (optionally with a 🎯 prefix), written by the job-board
# poller (personal-ai-pod job-board-poll.js) to rank a role's fit. Anchored so it matches only a card
# whose whole label IS the priority marker — never a contact name that happens to contain "P1".
_PRIORITY_RE = re.compile(r"^\s*(?:🎯\s*)?P([1-3])\s*$")

# A referral label marks a Job Search Outreach card at a company where someone in Russell's network has
# agreed to refer him — the highest predictor of landing an interview, so within a fit tier a
# referral-backed role is worked before an equally-ranked cold one. The label is always written as the
# exact string "🤝 Referral" by personal-ai-pod's apply-referral-labels.js (the sole writer), so this
# matches that one canonical form; anchored so a contact name containing "referral" never trips it.
_REFERRAL_RE = re.compile(r"^\s*🤝 Referral\s*$")

# A person follow-up card wears the "👤 Contact" label: it tracks the follow-up cadence with one
# network contact (its Start is the next-ping date), as opposed to an application card, which tracks a
# job's own progress and carries a P1/P2/P3 label instead. The two never share a card. Written as the
# exact string "👤 Contact"; anchored so a role/company name containing "contact" never trips it.
_CONTACT_RE = re.compile(r"^\s*👤 Contact\s*$")

# THE priority policy — the one place it is defined; every other site that mentions a band points here.
# A Job Search Outreach card maps to a queue band, ranked (band, date) descending against every other
# drained item. Bands are relative to NEUTRAL_PRIORITY_BAND (email/Slack and every unlabeled card):
#   👤 Contact (person follow-up) → above neutral   worked ahead of email/Slack and every application
#   P1 application card            → neutral         interleaves with that day's email/Slack by date
#   P2/P3 application card         → below neutral   worked once the neutral band is drained
# Following up with an existing contact is the highest-value move, so person cards sit one band ABOVE
# neutral, ahead of the inbox (the 👤 Contact pin lives in _priority_band). A P1 (top-fit) application
# stays AT neutral so a fresh strong-fit role is caught the same day as email; P2/P3 drop below. This is
# inert for every other board — only the poller-labeled job-search cards and 👤 Contact cards leave the
# default neutral.
_PRIORITY_BAND = {
    1: NEUTRAL_PRIORITY_BAND,
    2: NEUTRAL_PRIORITY_BAND - 1,
    3: NEUTRAL_PRIORITY_BAND - 2,
}


class Provider(ProviderBase):
    name = "trello"

    def __init__(self):
        self._utils = self._import_trello_utils()
        self._session = None
        # Defaults; overridden by configure(cfg) once the poller hands us the repo + parsed config.
        self.boards = []
        # Stored lowercased; a list is skipped if any token is a substring of its name (case-insensitive),
        # so emoji-prefixed lists like "📋 Templates" still match the plain "Templates" token.
        self.skip_lists = {"abandoned", "finished", "adopted", "templates"}
        # Same substring rule for labels: a card wearing a skip label is suppressed. "⛔ Blocked" cards
        # are hidden until their upstream clears (the push cascade strips the label). status_labels are
        # the superset held out of contact classification (a ⛔/⏳ label is never a contact name).
        self.skip_labels = {"blocked"}
        self.status_labels = {"blocked", "waiting"}
        self.channels = []
        self.features = []
        # Board name → default initiative slug (from a board's `initiative:` field in the registry);
        # applies to every card on that board when no per-card initiative label is present.
        self.board_initiatives = {}
        # Resolved lazily on first enumerate; used to skip cards assigned to someone else.
        self._my_member_id = None

    # --------------------------------------------------------------- locate the trello-outreach helper
    @staticmethod
    def _import_trello_utils():
        """Locate the `trello` plugin's trello_utils.py — same search shape as gmail-adapter's
        `_find_gmail_js`, via the shared `find_skill_script` helper — and import it."""
        path = find_skill_script(__file__, "trello", os.path.join("scripts", "trello_utils.py"))
        if not path:
            raise ProviderError(
                "Could not locate trello-outreach's trello_utils.py for the trello provider.",
                kind="config")
        import importlib.util
        spec = importlib.util.spec_from_file_location("trello_utils", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @property
    def session(self):
        if self._session is None:
            self._session = self._utils.get_trello_session()
        return self._session

    # --------------------------------------------------------------- config (boards/skip/label vocab)
    def configure(self, cfg):
        """Learn the boards to drain + the drainer knobs from the repo.

        Boards come from the shared registry `<repo>/trello-boards.yaml` (the same file the
        trello-outreach skill reads) — the drainer drains every board in it. `skip_lists` / `label_vocab`
        are drainer-specific and stay in `.claude/drainer.local.md`'s `providers.trello` block. If the
        registry file is absent, boards fall back to a legacy `boards:` list in that same block.

        Called by the poller after construction; a no-op contract on ProviderBase means gmail/slack
        ignore it.
        """
        repo = cfg.get("repo")
        if not repo:
            return
        # Drainer knobs (skip_lists / label_vocab) — and legacy boards fallback — from drainer.local.md.
        block = ""
        try:
            with open(os.path.join(repo, ".claude", "drainer.local.md"), encoding="utf-8") as f:
                block = self._slice_trello_block(f.read())
        except OSError:
            pass
        if block:
            skip = self._parse_inline_list(block, "skip_lists")
            if skip:
                self.skip_lists = {s.lower() for s in skip}
            skip_labels = self._parse_inline_list(block, "skip_labels")
            if skip_labels:
                self.skip_labels = {s.lower() for s in skip_labels}
            status_labels = self._parse_inline_list(block, "status_labels")
            if status_labels:
                self.status_labels = {s.lower() for s in status_labels}
            self.channels = self._parse_inline_list(block, "channels")
            self.features = self._parse_inline_list(block, "features")
        # Boards: the shared registry is authoritative; drainer.local.md's boards block is the fallback.
        boards = []
        try:
            with open(os.path.join(repo, "trello-boards.yaml"), encoding="utf-8") as f:
                boards = self._parse_registry(f.read())
        except OSError:
            pass
        if not boards and block:
            boards = self._parse_boards(block)
        if boards:
            self.boards = boards
            self.board_initiatives = {
                b["name"]: b["initiative"] for b in boards if b.get("initiative")}

    @staticmethod
    def _parse_registry(text):
        """Parse `<repo>/trello-boards.yaml` — a `boards:` list of board items, each a `- name:` at
        two-space indent with an `id:` field at four-space indent. Anchoring on those exact indents
        keeps the parse robust against deeper nested fields (per-board `purpose`, `template_cards`, …)
        without needing a full YAML library (none ships with the poller's stdlib runtime). The
        optional four-space `initiative:` field is captured as a board's default initiative slug."""
        boards, cur = [], None
        for line in text.splitlines():
            m_name = re.match(r"^  -\s*name\s*:\s*(.+?)\s*$", line)
            m_id = re.match(r"^    id\s*:\s*(.+?)\s*$", line)
            m_init = re.match(r"^    initiative\s*:\s*(.+?)\s*$", line)
            if m_name:
                if cur:
                    boards.append(cur)
                cur = {"name": m_name.group(1).strip().strip('"\'')}
            elif m_id and cur is not None and "id" not in cur:
                cur["id"] = m_id.group(1).strip().strip('"\'')
            elif m_init and cur is not None and "initiative" not in cur:
                cur["initiative"] = m_init.group(1).strip().strip('"\'')
        if cur:
            boards.append(cur)
        return [b for b in boards if b.get("id")]

    @staticmethod
    def _slice_trello_block(text):
        """Return the lines under `  trello:` up to the next 2-space-indented provider key (or EOF)."""
        lines = text.splitlines()
        out, in_block = [], False
        for line in lines:
            if re.match(r"^  trello\s*:\s*$", line):
                in_block = True
                continue
            if in_block:
                # A new 2-space top-level key (sibling provider) or a non-indented line ends the block.
                if re.match(r"^  \S", line) or re.match(r"^\S", line):
                    break
                out.append(line)
        return "\n".join(out)

    @staticmethod
    def _parse_boards(block):
        """Parse the `boards:` list of `{name, id}` from the trello block."""
        boards, cur = [], None
        in_boards = False
        for line in block.splitlines():
            if re.match(r"^\s*boards\s*:\s*$", line):
                in_boards = True
                continue
            if in_boards:
                # End of the boards list: a less-indented key at the trello level (4 spaces).
                if re.match(r"^    \S", line) and not line.lstrip().startswith("- ") \
                        and not re.match(r"^\s*(name|id)\s*:", line):
                    in_boards = False
                m_name = re.match(r"^\s*-\s*name\s*:\s*(.+?)\s*$", line)
                m_id = re.match(r"^\s*id\s*:\s*(.+?)\s*$", line)
                if m_name:
                    if cur:
                        boards.append(cur)
                    cur = {"name": m_name.group(1).strip().strip('"\'')}
                elif m_id and cur is not None:
                    cur["id"] = m_id.group(1).strip().strip('"\'')
        if cur:
            boards.append(cur)
        return [b for b in boards if b.get("id")]

    @staticmethod
    def _parse_inline_list(block, key):
        """Parse an inline YAML list `key: [a, b, c]` from the block (returns [] if absent/empty)."""
        m = re.search(rf"^\s*{re.escape(key)}\s*:\s*\[(.*?)\]\s*$", block, re.MULTILINE)
        if not m:
            return []
        inner = m.group(1).strip()
        if not inner:
            return []
        return [x.strip().strip('"\'') for x in inner.split(",") if x.strip()]

    # --------------------------------------------------------------- label classification
    def _classify_labels(self, card):
        """Split a card's labels into (channelLabel, features, contacts, initiativeLabel).

        A yellow label is the card's initiative (its name is returned as initiativeLabel and held out
        of contacts, so an initiative label is never mistaken for a contact name). Remaining labels
        classify by the label_vocab as before."""
        labels = card.get("labels", [])
        initiative = next((l.get("name") for l in labels
                           if (l.get("color") or "").lower() == "yellow" and l.get("name")),
                          None)
        # Hold status labels (⛔ Blocked / ⏳ Waiting), priority labels (🎯 P1/P2/P3), the 🤝 Referral
        # label, and the 👤 Contact card-type label out of the name pool so none is ever read as a
        # contact — a status label carries dependency state, a priority label carries fit rank, the
        # referral label carries a queue promotion, and 👤 Contact marks the card as a person-follow-up
        # card, none of them a contact's name.
        names = [l.get("name") for l in labels
                 if l.get("name") and l.get("name") != initiative
                 and not any(tok in l.get("name").lower() for tok in self.status_labels)
                 and not _PRIORITY_RE.match(l.get("name"))
                 and not _REFERRAL_RE.match(l.get("name"))
                 and not _CONTACT_RE.match(l.get("name"))]
        channel = next((n for n in names if n in self.channels), None)
        feats = [n for n in names if n in self.features]
        contacts = [n for n in names if n not in self.channels and n not in self.features]
        return channel, feats, contacts, initiative

    @staticmethod
    def _priority_band(card):
        """Return a card's queue band (see _PRIORITY_BAND), or neutral when it carries no ranking label.
        A 👤 Contact person-follow-up card is pinned one band above neutral first, so it is worked ahead
        of the inbox and every application tier even if it also happens to carry another label. Otherwise
        the first P1/P2/P3 priority label wins; an application card normally wears exactly one."""
        labels = card.get("labels", [])
        for l in labels:
            if _CONTACT_RE.match(l.get("name") or ""):
                return NEUTRAL_PRIORITY_BAND + 1
        for l in labels:
            m = _PRIORITY_RE.match(l.get("name") or "")
            if m:
                return _PRIORITY_BAND[int(m.group(1))]
        return NEUTRAL_PRIORITY_BAND

    @staticmethod
    def _referral_band(card):
        """Return a card's referral rank from its 🤝 Referral label — 1 when a referral is in hand, 0
        otherwise. Referral is the last band in band_rank (below level), so it only breaks ties among
        same-level roles: a referral role ahead of a cold one of the SAME level, but a cold leadership
        role still ahead of a referral IC role (a leadership role without a referral is worked before an
        IC role even with one). Zero is the shared neutral rank email/Slack and every non-referral card
        carry."""
        for l in card.get("labels", []):
            if _REFERRAL_RE.match(l.get("name") or ""):
                return 1
        return 0

    @staticmethod
    def _level_band(card):
        """Return a card's level rank from its 'Priority: ... · <level>' desc line — 0 for
        Director/VP-level, every non-job-search card, and a job-search card job-board-poll hasn't
        scored yet; -1 for an IC-level job-search card. Level leads referral in band_rank, so within a
        fit tier the level decides before referral. Zero is the shared neutral level that email/Slack and
        ordinary Trello cards also carry by default, so a Director/VP-level lead interleaves with today's
        mail by date within its priority band; only an IC-level posting drops below and waits for that
        neutral level to clear. Still mirrors job-board-poll's tiers.js PREVIEW_LEVEL_RANK's relative
        order (lead ahead of ic)."""
        desc = card.get("desc") or ""
        if "IC-level" in desc:
            return -1
        return 0

    def _has_skip_label(self, card):
        """True if the card wears a suppress label (default: ⛔ Blocked) — hidden until it's cleared."""
        for l in card.get("labels", []):
            nm = (l.get("name") or "").lower()
            if nm and any(tok in nm for tok in self.skip_labels):
                return True
        return False

    @staticmethod
    def _parse_dt(value):
        """Parse a Trello date field (an ISO-8601 string) to an aware datetime, or None if absent/bad."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _created_dt(card_id):
        """Decode a card's creation time from its id: a Trello card id is a Mongo ObjectId whose first
        8 hex chars are the creation Unix timestamp. Used as the sort rank for undated cards, so a blank
        ranks by its age."""
        try:
            return datetime.fromtimestamp(int(card_id[:8], 16), timezone.utc)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _startable(start_dt, now):
        """Whether a card is in play now. Start is the only date the queue reads: a card is startable
        when it has no Start (start immediately) or its Start has arrived. A future Start is the sole way
        to defer a card - it means "not okay to begin until this day". No other field holds it back."""
        return start_dt is None or start_dt <= now

    def _startable_cards(self, board, now, my_id):
        """Yield `(card, list_name, start_dt)` for every currently-startable card on one board.

        The single source of truth for "in play", so enumerate (which builds full items) and
        still_in_inbox_ids (which needs only the ids, for the reconcile) apply an identical gate and can
        never drift: on an active, non-skip list; not wearing a ⛔ Blocked/skip label; not assigned to
        someone else; and startable now (no Start, or a Start that has arrived - a future Start defers).
        The board fetches hit the Trello REST API; a bad token or network error raises to the caller."""
        bid = board["id"]
        lists = {l["id"]: l["name"] for l in self._utils.get_board_lists(bid, self.session)}
        for card in self._utils.get_board_cards(bid, self.session, fields="all"):
            list_name = lists.get(card.get("idList"))
            # get_board_lists returns only OPEN lists; a card whose list isn't in that map sits on an
            # archived/closed list (still an open card, but off the board) — not active, skip it.
            if not list_name:
                continue
            if any(tok in list_name.lower() for tok in self.skip_lists):
                continue
            # ⛔ Blocked cards are suppressed until their upstream clears (the push cascade removes the
            # label and sets Start = today, so they resurface on a later drain).
            if self._has_skip_label(card):
                continue
            # Skip cards assigned to someone else; unassigned cards are always Russell's.
            assigned = card.get("idMembers") or []
            if assigned and my_id not in assigned:
                continue
            start_dt = self._parse_dt(card.get("start"))
            if not self._startable(start_dt, now):
                continue
            yield card, list_name, start_dt

    # --------------------------------------------------------------- the ProviderBase contract
    def enumerate(self, limit):
        if not self.boards:
            return []
        try:
            return self._enumerate()
        except ProviderError:
            raise  # already typed (kind preserved) — don't re-wrap as auth
        except Exception as e:
            # The board/list/card fetches hit the Trello REST API with TRELLO_API_KEY / TRELLO_TOKEN;
            # a bad/expired token or network error surfaces here. Raise the typed error so the poller
            # isolates trello and records it to provider-health instead of aborting the whole cycle.
            raise ProviderError(f"trello enumerate failed (auth/API?): {e}", kind="auth")

    def _my_id(self):
        """Return the authenticated member's Trello ID, fetched once and cached."""
        if self._my_member_id is None:
            me = self._utils.trello_request("GET", "/members/me", self.session,
                                            params={"fields": "id"})
            self._my_member_id = me.get("id") if isinstance(me, dict) else None
        return self._my_member_id

    def _enumerate(self):
        now = datetime.now(timezone.utc)
        my_id = self._my_id()
        items = []
        # Pull-unblock backstop, once per cycle across EVERY board: cascade_unblock only fires when a
        # worker finishes a specific card (a push, triggered at that moment, scoped to one board); it
        # never catches a blocker finished by hand in the Trello UI, in a session that forgot the call,
        # or living on a different board than the card it blocks (a resume-prep task on Personal
        # Follow-Up blocking an application card on Job Search Outreach, say). This combined sweep is a
        # no-op when nothing needs freeing, so it's safe to run every cycle and guarantees a blocked card
        # resurfaces on its own once its blockers are done, regardless of who/how/where they finished.
        unblocked = self._utils.sweep_unblock([b["id"] for b in self.boards], self.session)
        if unblocked:
            print(f"  sweep_unblock freed {len(unblocked)} card(s): {unblocked}")
        for board in self.boards:
            bname = board.get("name", board["id"])
            # fields="all" (inside _startable_cards) so the newer `start` date comes back; the same gate
            # feeds still_in_inbox_ids, so a card in play here is exactly one reconcile treats as
            # outstanding.
            for card, list_name, start_dt in self._startable_cards(board, now, my_id):
                # Sort rank / go-live: a card ranks by its Start (its resurface day); an undated card by
                # its creation date (always in the past), so it sorts by age alongside email/Slack.
                sort_dt = start_dt or self._created_dt(card["id"])
                # The three band values; band_rank (provider_base) defines the order they sort in.
                priority_band = self._priority_band(card)  # see _PRIORITY_BAND
                referral_band = self._referral_band(card)  # see _referral_band
                level_band = self._level_band(card)        # see _level_band
                channel, feats, contacts, initiative_label = self._classify_labels(card)
                # A per-card initiative label wins over the board's default initiative. The slug is the
                # initiative label's name slugified (→ initiatives/<slug>.md); board defaults are already
                # slugs in the registry.
                initiative = slug(initiative_label, 60) if initiative_label \
                    else self.board_initiatives.get(bname)
                who = ", ".join(contacts) if contacts else bname
                items.append({
                    "cardId": card["id"],
                    "board": bname,
                    "list": list_name,
                    "name": card.get("name", ""),
                    "start": card.get("start"),
                    "url": card.get("shortUrl") or card.get("url"),
                    "desc": card.get("desc", ""),
                    "channelLabel": channel,
                    "features": feats,
                    "contacts": contacts,
                    "initiative": initiative,
                    # Triage payload fields (mirror the inbox adapters):
                    "from": who,
                    "subject": card.get("name", ""),
                    # `received` carries the card's Start for the cross-source ordering; an undated card
                    # uses its creation date, so it sorts by age alongside email/Slack.
                    "received": sort_dt.isoformat() if sort_dt else "(no date)",
                    "preview": f"[{bname} / {list_name}] {(card.get('desc') or '').strip()[:200]}",
                    "_sort_dt": sort_dt,
                    "_priority_band": priority_band,
                    "_referral_band": referral_band,
                    "_level_band": level_band,
                })
        # Ranked by band_rank (priority, level, referral — the queue policy, defined in provider_base)
        # then Start date, all descending: a card's Start (most-recent-first) breaks ties within a
        # band+level+referral; an undated card uses its creation date (set above), or `now` if even that
        # couldn't be derived.
        items.sort(key=lambda it: (*band_rank(it), it["_sort_dt"] or now), reverse=True)
        # Return every eligible card, untruncated: get_board_cards() already fetched all of them
        # regardless, and the poller's cross-source band_rank sort against
        # target_open_tabs (run-poller.py's `needs` list) is what decides how much actually gets
        # dispatched. Truncating
        # here instead would share one board-local budget across only Trello's own boards: once the OTHER
        # boards' neutral-band cards alone outnumbered it, every job-search card (always ranked below
        # neutral — see _PRIORITY_BAND) would be cut before the poller's real throttle ever saw it,
        # regardless of how many tabs were actually free.
        return items

    def stable_id(self, item):
        # The Start date is PART OF the identity, not just a field: a card recurs every cycle (a nudge
        # bumps its Start out, CLEAR bumps it out), and seen-state's `clear` leaves a drained id in
        # seen.json forever (status=cleared, key persists) while dedup is pure key-presence. So a stable
        # per-card id would be marked seen on the first drain and never resurface when the next Start
        # arrives. Folding the Start (YYYYMMDD) in makes each go-live a distinct item that surfaces anew;
        # an undated card stamps to the fixed sentinel `nodue`, so it stays seen until it's given a Start.
        stamp_src = item.get("start") or ""
        stamp = "".join(c for c in stamp_src if c.isdigit())[:8] or "nodue"
        return f"{self.name}-{slug(item.get('name'), 40)}-{item['cardId'][-6:]}-{stamp}".strip("-")[:72]

    def still_in_inbox_ids(self):
        """The stable_ids of every currently-startable card across all drained boards — Trello's answer
        to the reconcile's "is this seen item still outstanding at the source?".

        Trello has no inbox, but it has an equivalent: a seen id that STILL names a startable card is one
        whose worker never ran CLEAR (a crashed or closed tab). CLEAR bumps a card's Start, and the Start
        is part of the stable_id, so a properly cleared card mints a NEW id and its old id is absent from
        this set — reconcile then leaves it alone, exactly as it leaves an archived email alone. The set
        is computed fresh from the boards, so a card CLEAR deferred into the future (its new Start not yet
        arrived) is correctly excluded, and it applies the same gate as enumerate (via _startable_cards),
        so a blocked / skip-listed / reassigned card is never re-queued.

        Returns None on any board-fetch failure so reconcile SKIPS Trello that cycle: an empty set would
        read as "every Trello card is gone" and re-queue all of them — the failure mode the email path
        guards against the same way. `capture` writes each item's messageId = its stable_id, so the
        reconcile's existing messageId-in-inbox check works over this set unchanged."""
        if not self.boards:
            return None
        try:
            now = datetime.now(timezone.utc)
            my_id = self._my_id()
            ids = set()
            for board in self.boards:
                for card, _list_name, _start_dt in self._startable_cards(board, now, my_id):
                    ids.add(self.stable_id({"name": card.get("name", ""),
                                            "cardId": card["id"], "start": card.get("start")}))
            return ids
        except Exception as e:
            print(f"  trello: still_in_inbox check FAILED ({e}); reconcile skips trello this cycle.")
            return None

    def capture(self, item, iid, runtime_dir):
        items_dir = os.path.join(runtime_dir, "items")
        os.makedirs(items_dir, exist_ok=True)
        body_file = os.path.join(items_dir, f"{iid}.trello.md")
        comments = self._fetch_comments(item["cardId"])
        with open(body_file, "w", encoding="utf-8") as f:
            f.write(f"# {item.get('name')}\n\n"
                    f"Board: {item.get('board')} / List: {item.get('list')}\n"
                    f"Start (work-on-it date): {item.get('start') or '(startable now)'}\n"
                    f"Channel: {item.get('channelLabel') or '(none)'}\n"
                    f"Contacts: {', '.join(item.get('contacts') or []) or '(none)'}\n"
                    f"Initiative: {item.get('initiative') or '(none)'}\n"
                    f"Link: {item.get('url')}\nCardId: {item['cardId']}\n\n"
                    f"## Description\n\n{item.get('desc') or '(empty)'}\n\n## Comments\n\n{comments}\n")
        record = {
            "id": iid, "source": self.name, "triage": item.get("_bucket", "needs-you"),
            # messageId == the stable_id: it is the handle the reconcile checks against
            # still_in_inbox_ids() (the set of startable-card ids). A cleared card's Start bump mints a
            # new id, so this old one drops out of that set and reconcile leaves it alone; a crashed
            # worker's card keeps this id, stays in the set, and is re-queued.
            "messageId": iid,
            "kind": item.get("_kind"), "cardId": item["cardId"], "board": item.get("board"),
            "list": item.get("list"), "name": item.get("name"),
            "start": item.get("start"),
            "url": item.get("url"), "contacts": item.get("contacts") or [],
            "channelLabel": item.get("channelLabel"), "initiative": item.get("initiative"),
            "bodyFile": body_file,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        json_file = os.path.join(items_dir, f"{iid}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        return json_file

    def _fetch_comments(self, card_id):
        try:
            actions = self._utils.trello_request(
                "GET", f"/cards/{card_id}/actions", self.session,
                params={"filter": "commentCard", "limit": "50"})
        except Exception:
            return "(could not load comments)"
        if not actions:
            return "(none)"
        out = []
        for a in actions:
            who = (a.get("memberCreator") or {}).get("fullName", "?")
            when = a.get("date", "")
            text = (a.get("data") or {}).get("text", "")
            out.append(f"- {when} {who}: {text}")
        return "\n".join(out)
