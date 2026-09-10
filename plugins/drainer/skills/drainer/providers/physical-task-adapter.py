"""physical-task poller adapter — a dedicated Outlook calendar of physical-world to-dos, drained
through the Microsoft Graph API via the ms-graph skill's calendar.js.

All physical-task mechanics live HERE, alongside the prose contract in
`physical-task-provider.md`: locating calendar.js, the queued/gap ENUMERATE, the id scheme (the raw
Graph event/occurrence id, stable while a task stays queued), and the captured item shape. The poller
(`scripts/run-poller.py`) loads this adapter dynamically and drives it through the `ProviderBase`
interface.

The model: a task is QUEUED while it sits on the dedicated calendar with a start date today-or-earlier
AND a start time landing EXACTLY on the overnight parking grid (`:00` at midnight, `:00/:15/:30/:45`
at 1 AM, `:00/:30` at 2 AM — see calendar.js's isQueuedSlot) — mirrors the old pre-drainer habit of
staging a to-do in an overnight band and dragging it out once picked up. Exact-slot matching (not just
"somewhere in that hour") matters because CLEAR's "started" step moves start to the moment the worker
tab launched, which can itself fall inside 00:00-02:59 if Russell's up working late — a real timestamp
essentially never lands exactly on a slot, so alignment is what actually distinguishes "still sitting
untouched" from "just started." Nothing here ever moves a queued task on its own, so an undone one just
keeps coming back until it's started; there is no delete and no second archive calendar. What's unique
to this source is a SECOND gate on top of "queued": a physical task also needs a live free gap —
computed fresh every cycle — of at least its own duration (plus a buffer) before Russell's next real
(non-solo) calendar commitment, because unlike every other source, nobody but Russell can do the
actual work.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from provider_base import ProviderBase, ProviderError, run_node, slug, find_skill_script  # noqa: E402


class Provider(ProviderBase):
    name = "physical-task"

    def __init__(self):
        self.calendarjs = self._find_calendar_js()
        self.calendar = "Physical Tasks"
        self.lookahead_hours = 2
        self.buffer_minutes = 20
        self.exclude = []

    @staticmethod
    def _find_calendar_js():
        """Locate ms-graph's calendar.js — same search shape as outlook-graph-adapter's `_find_mail_js`."""
        path = find_skill_script(__file__, "ms-graph", os.path.join("scripts", "calendar.js"))
        if not path:
            raise ProviderError("Could not locate ms-graph calendar.js for physical-task.", kind="config")
        return path

    # --------------------------------------------------------------- config
    def configure(self, cfg):
        """`providers.physical-task` in drainer.local.md: `calendar` (default "Physical Tasks"),
        `lookahead_hours` (default 2 — how far ahead the gap check looks for the next real
        commitment; short on purpose, since no task should ever be sized past an hour — see
        CAPTURE's duration note), `buffer_minutes` (default 20 — added on top of a task's own
        duration before it counts as eligible, covering the lag between a gap being detected and
        Russell actually opening the worker tab), `exclude` (calendar names to leave out of the gap
        check, e.g. a read-only subscription). Called by the poller after construction; harmless
        with no block at all."""
        block = self._block(cfg.get("repo"))
        self.calendar = self._str_knob(block, "calendar") or self.calendar
        self.lookahead_hours = self._int_knob(block, "lookahead_hours", self.lookahead_hours)
        self.buffer_minutes = self._int_knob(block, "buffer_minutes", self.buffer_minutes)
        self.exclude = self._list_knob(block, "exclude")

    @staticmethod
    def _block(repo):
        if not repo:
            return ""
        try:
            with open(os.path.join(repo, ".claude", "drainer.local.md"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return ""
        out, in_block = [], False
        for line in text.splitlines():
            if re.match(r"^  physical-task\s*:\s*$", line):
                in_block = True
                continue
            if in_block:
                if re.match(r"^  \S", line) or re.match(r"^\S", line):
                    break
                out.append(line)
        return "\n".join(out)

    @staticmethod
    def _int_knob(block, key, default):
        m = re.search(rf"^\s*{re.escape(key)}\s*:\s*(\d+)\s*(?:#.*)?$", block, re.MULTILINE)
        return int(m.group(1)) if m else default

    @staticmethod
    def _str_knob(block, key):
        m = re.search(rf"^\s*{re.escape(key)}\s*:\s*(.+?)\s*(?:#.*)?$", block, re.MULTILINE)
        return m.group(1).strip().strip('"\'') if m else None

    @staticmethod
    def _list_knob(block, key):
        m = re.search(rf"^\s*{re.escape(key)}\s*:\s*\[(.*?)\]\s*$", block, re.MULTILINE)
        if not m or not m.group(1).strip():
            return []
        return [x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip()]

    # --------------------------------------------------------------- reads
    def _due_tasks(self):
        """Every due (start date today-or-earlier) task on the configured calendar, regardless of
        current gap — the full candidate set. Raises ProviderError on an auth/API failure."""
        res = run_node([self.calendarjs, "--list-due-tasks", f"--calendar={self.calendar}", "--json"])
        if res.returncode != 0:
            raise ProviderError(f"physical-task enumerate failed (auth?): {res.stderr.strip()[:300]}",
                                kind="auth")
        return json.loads(res.stdout or "[]")

    def _gap_minutes(self):
        args = [self.calendarjs, "--gap-minutes", "--json", f"--lookahead-hours={self.lookahead_hours}"]
        if self.exclude:
            args.append(f"--exclude={','.join(self.exclude)}")
        res = run_node(args)
        if res.returncode != 0:
            raise ProviderError(f"physical-task gap check failed: {res.stderr.strip()[:300]}", kind="auth")
        return json.loads(res.stdout or "{}").get("minutes", 0)

    def enumerate(self, limit):
        due = self._due_tasks()
        if not due:
            return []
        gap = self._gap_minutes()
        # `buffer_minutes` covers the lag between a gap being detected here and Russell actually
        # opening the worker tab (a tab budget it has to wait for, or just not being the tab he's
        # on right now) — a task only counts as fitting once its own duration PLUS that buffer is
        # covered, not just its bare duration.
        eligible = [t for t in due if t["minutes"] + self.buffer_minutes <= gap]
        # Newest-due first, same as email (drain today's backlog before working backwards into
        # older stale items) — then, among tasks that became due on the same day, the longest one
        # that fits so a big gap isn't wasted on a short task. This is what the poller's cross-source
        # dispatch order actually follows (it otherwise ties same-priority-band items and falls back
        # to insertion order) — see `still_in_inbox_ids`, unaffected by this order.
        eligible.sort(key=lambda t: (t["date"], t["minutes"]), reverse=True)
        return eligible[:limit]

    def still_in_inbox_ids(self):
        """Reconcile's analog of "still in the inbox": every currently-queued task's id, gap or no
        gap. A task the poller dispatched whose tab later closed without being started (still sitting
        exactly on the parking grid — see CLEAR) is still queued — dropping its seen key here lets it
        dispatch again next time a real gap opens, instead of being silently forgotten for good."""
        try:
            return {t["id"] for t in self._due_tasks()}
        except ProviderError:
            return None

    def stable_id(self, item):
        # Keyed on the underlying Graph event/occurrence id. For a one-off this is stable for the
        # task's whole queued lifetime (nothing here ever recreates or re-stamps it). For a recurring
        # series it's the most recent queued occurrence's own id (see the adapter's _due_tasks/
        # listDueTasks), which legitimately changes as a fresh occurrence becomes the representative
        # one — exactly like Trello's Start-stamped id scheme, just keyed on Graph's own instance id
        # instead of a date string.
        #
        # This id becomes part of the worker's prompt-file NAME (run-poller.py's spawn_worker), which
        # has to survive as a path argument through the poller -> wt.exe -> PowerShell relay that opens
        # the worker tab. Graph's event ids on this (consumer) tenant always end in "="/"==" base64
        # padding, and that "=" reliably breaks that relay partway through — launch-session.ps1 then
        # reports the resulting (silently truncated, extension-less) path as not found, every single
        # time, for every physical task. Stripping to bare alphanumerics before taking the last 10
        # characters keeps the id segment just as distinguishing (verified against a live batch of
        # tasks) while never putting a relay-breaking character in the path.
        safe_tail = re.sub(r"[^A-Za-z0-9]", "", item["id"])[-10:]
        return f"{self.name}-{slug(item['subject'])}-{safe_tail}"

    def correspondent(self, item):
        # Every physical task shares ONE identity, not a per-item one: Russell can only physically do
        # one task at a time, so a second eligible task must wait behind whichever one already has an
        # open worker tab — the same hold the poller uses to keep two messages from the same person out
        # of dispatch together (see provider_base.ProviderBase.correspondent / run-poller.py's
        # held_for_correspondent). Returning a constant here reuses that existing mechanism instead of
        # needing a bespoke single-flight lock: once the open task's tab closes (started, finished, or
        # deferred — see CLEAR), the hold releases and the next-longest eligible task dispatches.
        return "physical-task"

    def capture(self, item, iid, runtime_dir):
        items_dir = os.path.join(runtime_dir, "items")
        os.makedirs(items_dir, exist_ok=True)
        record = {
            "id": iid, "source": self.name, "triage": item["_bucket"], "kind": item.get("_kind"),
            "subject": item["subject"], "date": item["date"], "minutes": item["minutes"],
            "isRecurring": item["isRecurring"], "calendar": self.calendar,
            "eventId": item["id"], "seriesMasterId": item.get("seriesMasterId"),
            "url": item.get("webLink"), "correspondent": item.get("_correspondent"),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        json_file = os.path.join(items_dir, f"{iid}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        return json_file
