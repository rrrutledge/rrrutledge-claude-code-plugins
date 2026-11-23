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


def decide(tool_name, tool_input):
    result = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    if not result.stdout.strip():
        return "defer"
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]


def event(**fields):
    return {"summary": "Sync", "startTime": "2026-11-24T08:00:00", "endTime": "2026-11-24T09:00:00", **fields}


@pytest.mark.parametrize("tool", [CREATE, UPDATE])
@pytest.mark.parametrize("field", ["location", "description"])
def test_zoom_without_false_flag_is_denied(tool, field):
    assert decide(tool, event(**{field: f"Join: {ZOOM}"})) == "deny"


@pytest.mark.parametrize("tool", [CREATE, UPDATE])
def test_zoom_with_false_flag_passes(tool):
    assert decide(tool, event(location=ZOOM, description=ZOOM, addGoogleMeetUrl=False, attendees=GUESTS)) == "defer"


@pytest.mark.parametrize("extra", [{"addGoogleMeetUrl": True}, {"addGoogleMeetUrl": False, "googleMeetUrl": "abc-defg-hij"}])
def test_zoom_with_meet_requested_is_denied(extra):
    assert decide(CREATE, event(location=ZOOM, **extra)) == "deny"


@pytest.mark.parametrize("url", [
    "https://teams.microsoft.com/l/meetup-join/19%3a",
    "https://acme.webex.com/meet/pat",
    "https://zoomgov.com/j/123",
])
def test_other_non_meet_links_are_caught(url):
    assert decide(CREATE, event(description=f"<a href=\"{url}\">join</a>")) == "deny"


def test_guests_without_flag_are_denied():
    assert decide(CREATE, event(attendees=GUESTS)) == "deny"
    assert decide(CREATE, event(attendeeEmails=["guest@example.com"])) == "deny"


@pytest.mark.parametrize("flag", [True, False])
def test_guests_with_explicit_flag_pass(flag):
    assert decide(CREATE, event(attendees=GUESTS, addGoogleMeetUrl=flag)) == "defer"


def test_plain_events_defer():
    assert decide(CREATE, event(location="Conference room B")) == "defer"
    assert decide(UPDATE, {"eventId": "x", "summary": "Renamed"}) == "defer"
    assert decide(CREATE, event(location="https://meet.google.com/abc-defg-hij", attendees=GUESTS, addGoogleMeetUrl=True)) == "defer"


def test_other_tools_and_garbage_defer():
    assert decide("mcp__claude_ai_Google_Calendar__delete_event", {"eventId": "x"}) == "defer"
    result = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True, text=True)
    assert result.returncode == 0 and not result.stdout.strip()
