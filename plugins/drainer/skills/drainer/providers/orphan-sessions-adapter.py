"""orphan-sessions poller adapter — crash-recovered Claude Code sessions, highest priority.

A thin ProviderBase wrapper, same shape as every other drainer provider, but around
something session-mgr already owns rather than an external service: the live-session
registry, the SessionStart/SessionEnd hooks that maintain it, and the crash-detection logic
in find-orphans.py. This adapter locates and shells out to the newest INSTALLED session-mgr
plugin's find-orphans.py (the same version-pinned resolver pattern the drainer's
close-session.py already uses to reach session-mgr's end-session.py) rather than
re-implementing any registry/liveness logic here.

Unlike every other source, this one has no draft/reply/CLEAR cycle — resuming the session
itself, in the background, IS the whole action. See orphan-sessions-provider.md for the (empty)
worker-facing contract, and run-poller.py for the dedicated spawn_resume() dispatch path and the
deterministic pre-triage bypass (this source never reaches the AI triage call).

id scheme: `orphan-<session_id>-<slug(started_at)>` — see stable_id() for why started_at
(not just session_id) is baked into the id. No body file — capture() writes only
`<id>.json`.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from provider_base import ProviderBase, ProviderError, slug  # noqa: E402

_REL_FIND_ORPHANS = os.path.join("skills", "resume-sessions", "scripts", "find-orphans.py")


def _parse_version(name):
    try:
        return tuple(int(p) for p in name.split("."))
    except ValueError:
        return (0, 0, 0)


def _find_orphans_script():
    """Locate the newest INSTALLED session-mgr plugin's find-orphans.py — same version-sort
    resolver pattern as the drainer's close-session.py, so this adapter is never tied to a
    floating working-clone branch of session-mgr. Falls back to the working-clone path when
    no installed copy exists yet (a fresh dev machine)."""
    base = os.path.expanduser(os.path.join(
        "~", ".claude", "plugins", "cache", "rrrutledge-claude-code-plugins", "session-mgr"))
    if os.path.isdir(base):
        for version in sorted(os.listdir(base), key=_parse_version, reverse=True):
            candidate = os.path.join(base, version, _REL_FIND_ORPHANS)
            if os.path.isfile(candidate):
                return candidate
    # From plugins/drainer/skills/drainer/providers up to plugins/, then into session-mgr.
    fallback = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..",
        "session-mgr", _REL_FIND_ORPHANS))
    if os.path.isfile(fallback):
        return fallback
    raise ProviderError(
        "Could not locate session-mgr's find-orphans.py (no installed copy and no "
        "working-clone fallback).", kind="config")


class Provider(ProviderBase):
    name = "orphan-sessions"

    def enumerate(self, limit):
        script = _find_orphans_script()
        try:
            result = subprocess.run(
                [sys.executable, script], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ProviderError(f"orphan-sessions: failed running find-orphans.py: {e}",
                                kind="auth")
        if result.returncode != 0:
            raise ProviderError(
                f"orphan-sessions: find-orphans.py exited {result.returncode}: "
                f"{result.stderr.strip()[:300]}", kind="auth")
        try:
            orphans = json.loads(result.stdout or "[]")
        except ValueError as e:
            raise ProviderError(f"orphan-sessions: find-orphans.py returned invalid JSON: {e}",
                                kind="auth")
        items = []
        for o in orphans[:limit]:
            items.append({
                "session_id": o.get("session_id"),
                "cwd": o.get("cwd"),
                "started_at": o.get("started_at"),
                # Orders MULTIPLE simultaneous orphans against each other (newest crash
                # first) within run-poller.py's dedicated orphan-dispatch pass —
                # cross-source priority is enforced structurally there (orphans dispatch
                # before the rest of needs-you), not by this timestamp.
                "received": o.get("started_at") or "",
            })
        return items

    def stable_id(self, item):
        # Baking started_at into the id (not just session_id) means a session already
        # auto-resumed once, that later crashes AGAIN, gets a fresh id on its second crash —
        # SessionStart re-fires on resume, advancing started_at — so it resurfaces as a new
        # item instead of staying permanently seen after the first resume. Same idiom the
        # trello adapter uses baking a card's go-live date into its id.
        stamp = slug(item.get("started_at"), 32) or "unknown"
        return f"orphan-{item.get('session_id')}-{stamp}"

    def capture(self, item, iid, runtime_dir):
        items_dir = os.path.join(runtime_dir, "items")
        os.makedirs(items_dir, exist_ok=True)
        record = {
            "id": iid,
            "source": self.name,
            "triage": item.get("_bucket", "needs-you"),
            "kind": item.get("_kind", "resume"),
            "session_id": item.get("session_id"),
            "cwd": item.get("cwd"),
            "started_at": item.get("started_at"),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        json_file = os.path.join(items_dir, f"{iid}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        return json_file
