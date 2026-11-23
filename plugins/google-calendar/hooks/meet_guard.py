#!/usr/bin/env python3
"""Meet guard - a calendar event never gets a Google Meet link nobody chose.

The claude.ai Google Calendar connector's `create_event` attaches a Google Meet link to
any event with guests unless the call passes `addGoogleMeetUrl: false` explicitly, even
though its schema documents a `false` default. The invites go out the moment the event is
created, so a Meet link removed afterward has already reached every guest.

This PreToolUse hook sees every `create_event` and `update_event` call before it runs,
whether or not the session loaded the google-calendar skill. A call that sets
`addGoogleMeetUrl` (either way) or `googleMeetUrl` has made its choice and defers. A call
that leaves both out is denied, with the corrected call spelled out, in two cases:

  * The event's `location` or `description` names a Zoom, Teams, Webex, or other non-Meet
    meeting host - the call needs `addGoogleMeetUrl: false` when that URL is the join link.
  * The call adds guests (`attendees` on create, `addedAttendees` on update) - the call
    needs the flag set to whichever the meeting wants.

The hook denies or defers, and on any internal error it defers, so a calendar call it
can't evaluate runs as if the hook weren't installed.
"""

import json
import re
import sys

GUEST_FIELDS = ("attendees", "attendeeEmails", "addedAttendees", "addedAttendeeEmails")

NON_MEET_JOIN = re.compile(
    r"(?<![\w.@-])(?:https?://)?(?:[\w-]+\.)*(?:"
    r"zoom\.us|zoom\.com|zoomgov\.com|teams\.microsoft\.com|teams\.live\.com|webex\.com|"
    r"gotomeeting\.com|goto\.com|whereby\.com|bluejeans\.com|chime\.aws|meet\.jit\.si"
    r")(?=[/:?#\s\"'<>,;)&]|\.(?!\w)|$)",
    re.IGNORECASE,
)

CHOICE = (
    "Re-run the same call with `addGoogleMeetUrl` set explicitly: `true` for a meeting "
    "meant to be on Google Meet, `false` for every other meeting, with a non-Meet join URL "
    "in both `location` and `description`. "
    "The flag only stops a new Meet link; `google-calendar.js --strip-meet` removes one an "
    "event already has."
)


def verdict(tool_input):
    if tool_input.get("addGoogleMeetUrl") is not None or tool_input.get("googleMeetUrl") is not None:
        return None
    for field in ("location", "description"):
        match = NON_MEET_JOIN.search(str(tool_input.get(field) or ""))
        if match:
            return (
                f"This event mentions {match.group(0)} and leaves `addGoogleMeetUrl` out, so "
                "the connector would attach a Google Meet link once it has guests, despite its "
                "documented `false` default. "
                "If that URL is the meeting's join link, the flag is `false`. " + CHOICE
            )
    if any(tool_input.get(field) for field in GUEST_FIELDS):
        return (
            "This call adds guests and leaves `addGoogleMeetUrl` out, so the connector would "
            "attach a Google Meet link to the invites despite its documented `false` default. "
            + CHOICE
        )
    return None


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        reason = verdict(payload.get("tool_input") or {})
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
