"""Tests for the Meet guard (meet_guard.py), driven via stdin JSON exactly as the harness does."""
import json
import os
import subprocess
import sys

import pytest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "meet_guard.py")
CREATE = "mcp__claude_ai_Google_Calendar__create_event"
UPDATE = "mcp__claude_ai_Google_Calendar__update_event"
ZOOM = "https://us02web.zoom.us/j/81234567890?pwd=abc"
GUESTS = [{"email": "guest@example.com"}]


def run(stdin):
    result = subprocess.run([sys.executable, HOOK], input=stdin.encode("utf-8"), capture_output=True)
    assert result.returncode == 0
    return result.stdout.decode("utf-8").strip()


def decide(tool_name, tool_input):
    out = run(json.dumps({"tool_name": tool_name, "tool_input": tool_input}, ensure_ascii=False))
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else "defer"


def event(**fields):
    return {"summary": "Sync", "startTime": "2026-11-24T08:00:00", "endTime": "2026-11-24T09:00:00", **fields}


@pytest.mark.parametrize("tool", [CREATE, UPDATE])
@pytest.mark.parametrize("field", ["location", "description"])
def test_zoom_without_flag_is_denied(tool, field):
    assert decide(tool, event(**{field: f"Join: {ZOOM}"})) == "deny"


@pytest.mark.parametrize("tool", [CREATE, UPDATE])
def test_zoom_with_false_flag_passes(tool):
    assert decide(tool, event(location=ZOOM, description=ZOOM, addGoogleMeetUrl=False, attendees=GUESTS)) == "defer"


@pytest.mark.parametrize("text", [
    "https://teams.microsoft.com/l/meetup-join/19%3a",
    "<a href=\"https://acme.webex.com/meet/pat\">join</a>",
    "https://zoomgov.com/j/123",
    "zoom.us/j/81234567890",
])
def test_other_join_links_are_caught(text):
    assert decide(CREATE, event(description=text)) == "deny"


def test_look_alike_host_is_not_a_join_link():
    assert decide(CREATE, event(description="https://zoom.us-events.example.com/agenda")) == "defer"


def test_guests_without_flag_are_denied():
    assert decide(CREATE, event(attendees=GUESTS)) == "deny"
    assert decide(CREATE, event(attendeeEmails=["guest@example.com"])) == "deny"
    assert decide(UPDATE, {"eventId": "x", "addedAttendees": GUESTS}) == "deny"


@pytest.mark.parametrize("extra", [{"addGoogleMeetUrl": True}, {"addGoogleMeetUrl": False}, {"googleMeetUrl": "abc-defg-hij"}])
def test_an_explicit_choice_passes(extra):
    assert decide(CREATE, event(attendees=GUESTS, description=f"Recording: {ZOOM}", **extra)) == "defer"


def test_non_ascii_text_is_still_checked():
    assert decide(CREATE, event(description="Sync with Álvaro", attendees=GUESTS)) == "deny"


def test_plain_events_and_garbage_defer():
    assert decide(CREATE, event(location="Conference room B")) == "defer"
    assert decide(UPDATE, {"eventId": "x", "summary": "Renamed"}) == "defer"
    assert run("not json") == ""
