#!/usr/bin/env python3
"""Meet guard - a calendar event never gets a Google Meet link nobody chose.

The claude.ai Google Calendar connector's `create_event` attaches a Google Meet link to
any event with guests unless the call passes `addGoogleMeetUrl: false` explicitly, even
though its schema documents a `false` default. The invites go out the moment the event is
created, so a Meet link removed afterward has already reached every guest.

This PreToolUse hook sees every `create_event` and `update_event` call before it runs,
whether or not the session loaded the google-calendar skill, and denies three shapes, each
with the corrected call spelled out:

  * The event carries a Zoom, Teams, Webex, or other non-Meet join URL in `location` or
    `description`, and the call doesn't pass `addGoogleMeetUrl: false` - or it asks for
    Meet outright (`addGoogleMeetUrl: true`, `googleMeetUrl`).
  * `create_event` has guests and leaves `addGoogleMeetUrl` out, so the connector would
    add Meet silently. Passing either `true` or `false` lets it through; the call just has
    to say which.

Everything else defers. The hook is a pure veto - it only denies or defers, never
approves - and on any internal error it defers, so it can never be the mysterious reason
a legitimate calendar call fails.
"""

import json
import re
import sys

CREATE = "mcp__claude_ai_Google_Calendar__create_event"
UPDATE = "mcp__claude_ai_Google_Calendar__update_event"

NON_MEET_JOIN = re.compile(
    r"https?://(?:[\w-]+\.)*(?:"
    r"zoom\.us|zoomgov\.com|teams\.microsoft\.com|teams\.live\.com|webex\.com|"
    r"gotomeeting\.com|goto\.com|whereby\.com|bluejeans\.com|chime\.aws|meet\.jit\.si|"
    r"around\.co|discord\.gg|slack\.com/huddle"
    r")\b",
    re.IGNORECASE,
)


def join_url(tool_input):
    for field in ("location", "description"):
        match = NON_MEET_JOIN.search(str(tool_input.get(field) or ""))
        if match:
            return match.group(0)
    return None


def verdict(tool_name, tool_input):
    flag = tool_input.get("addGoogleMeetUrl")
    url = join_url(tool_input)
    if url:
        if tool_input.get("googleMeetUrl") or flag is not False:
            return (
                f"This event's join link is {url}, so it must not get a Google Meet link. "
                "Re-run the same call with `addGoogleMeetUrl: false` and no `googleMeetUrl` - "
                "the connector attaches Meet to any event with guests unless that flag is "
                "explicitly false, despite its documented `false` default. "
                "Keep the join URL in both `location` and `description`."
            )
        return None
    if tool_name == CREATE and flag is None and not tool_input.get("googleMeetUrl"):
        if tool_input.get("attendees") or tool_input.get("attendeeEmails"):
            return (
                "This event has guests and no `addGoogleMeetUrl`, so the connector would "
                "attach a Google Meet link to the invites despite its documented `false` "
                "default. Re-run the same call with `addGoogleMeetUrl` set explicitly: "
                "`false` for an in-person meeting or one with its own Zoom/Teams link (put "
                "that URL in `location` and `description`), `true` only when the meeting "
                "should be on Google Meet."
            )
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        tool_name = payload.get("tool_name")
        if tool_name not in (CREATE, UPDATE):
            return
        reason = verdict(tool_name, payload.get("tool_input") or {})
    except Exception:
        return
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))


if __name__ == "__main__":
    main()
