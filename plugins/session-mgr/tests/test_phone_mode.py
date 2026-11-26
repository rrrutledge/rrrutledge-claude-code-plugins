"""Tests for phone_mode.py: the marker switch and its reminder, the note `on` sends to the other open
sessions, and where `park` puts the work.

The marker is redirected to a temp folder, and Trello, the session scan, and delivery are stubbed.
Run directly: python plugins/session-mgr/tests/test_phone_mode.py
"""
import contextlib
import datetime
import importlib.util
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.abspath(os.path.join(HERE, ".."))
SCRIPT = os.path.join(PLUGIN, "skills", "phone-mode", "scripts", "phone_mode.py")
spec = importlib.util.spec_from_file_location("phone_mode", SCRIPT)
phone_mode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone_mode)

phone_mode.MARKER = os.path.join(tempfile.mkdtemp(), "home", "phone-mode")
failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def run(*argv):
    """phone_mode.main's printed output."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        phone_mode.main(list(argv))
    return out.getvalue().strip()


bg = phone_mode.bg_session
real = (bg.claude_agents, bg.session_address, bg.send_note, os.environ.get("CLAUDE_CODE_SESSION_ID"))
sent = []
AGENTS = [{"sessionId": "me", "pid": 1, "name": "caller"},
          {"sessionId": "a", "pid": 2, "name": "Slack: reachable"},
          {"sessionId": "b", "pid": 3, "name": "Trello: unreachable"},
          {"sessionId": "c", "pid": 4, "name": "no inbox"},
          {"sessionId": "gone", "name": "stopped"}]
try:
    bg.claude_agents = lambda: AGENTS
    bg.session_address = lambda sid, agents: None if sid == "c" else (sid, None)
    bg.send_note = lambda address, message, cwd: sent.append((address[0], message)) or address[0] != "b"
    os.environ["CLAUDE_CODE_SESSION_ID"] = "me"

    print("switch: off until turned on, and the reminder prints only while on")
    check("starts off", run("status"), "off")
    check("no reminder while off", run("remind"), "")
    check("turning it off while off is fine", run("off"), "phone mode is off.")

    print("\non: creates the marker and notes every other live session")
    out = run("on")
    check("marker created", (os.path.exists(phone_mode.MARKER), run("status")), (True, "on"))
    check("reminder", run("remind"), phone_mode.REMINDER)
    check("only the other live sessions with an inbox are sent to", sorted(s for s, _ in sent), ["a", "b"])
    check("the note names the skill", all("/session-mgr:phone-mode" in m for _, m in sent), True)
    check("counts what landed", "Told 1 of 3 other open session(s)." in out, True)
    check("names each miss", [line.strip() for line in out.splitlines() if "not reached" in line],
          ["not reached: Trello: unreachable", "not reached: no inbox"])

    bg.claude_agents = lambda: None
    check("an unreadable session list still turns it on", ("none were told" in run("on"), run("status")),
          (True, "on"))
    run("off")
    check("off removes the marker", (os.path.exists(phone_mode.MARKER), run("status")), (False, "off"))
finally:
    bg.claude_agents, bg.session_address, bg.send_note = real[:3]
    if real[3] is None:
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    else:
        os.environ["CLAUDE_CODE_SESSION_ID"] = real[3]


class FakeTrello:
    def __init__(self, lists=("Unaware", "To Do", "In Progress", "Finished")):
        self.lists, self.calls = [{"id": f"id-{n}", "name": n} for n in lists], []

    def get_trello_session(self):
        return ("k", "t")

    def get_board_lists(self, board, session):
        return self.lists

    def create_card(self, list_id, data, session):
        self.calls.append(("create", list_id, data))
        return {"id": "new", "shortUrl": "https://trello.com/c/new"}

    def add_comment(self, card, text, session):
        self.calls.append(("comment", card, text))

    def update_card(self, card, updates, session):
        self.calls.append(("update", card, updates))
        return {"shortUrl": "https://trello.com/c/old"}


def park(trello, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        phone_mode.park("the description", trello=trello, now=NOW, **kw)
    return out.getvalue()


NOW = datetime.datetime(2026, 11, 26, 23, 30, 15, tzinfo=datetime.timezone.utc)
START = "2026-11-27T00:30:15.000Z"

print("\npark --board: a new card on the working list, starting an hour from now")
t = FakeTrello()
out = park(t, board="B", name="Card name")
check("created on In Progress", t.calls,
      [("create", "id-In Progress", {"title": "Card name", "description": "the description", "start": START})])
check("prints the card link", "https://trello.com/c/new" in out, True)
t = FakeTrello(("Applied", "Identified", "Job Search Admin"))
park(t, board="B", name="n")
check("a board with intake lists uses its admin list", t.calls[0][1], "id-Job Search Admin")
t = FakeTrello()
park(t, board="B", name="n", list_name="Unaware")
check("--list wins", t.calls[0][1], "id-Unaware")
t = FakeTrello(("Backlog",))
try:
    park(t, board="B", name="n")
    refused = ""
except SystemExit as e:
    refused = str(e)
check("no usual list -> refused, naming the board's lists, nothing created",
      ("Backlog" in refused, t.calls), (True, []))

print("\npark --card: the comment first, then Start an hour out")
t = FakeTrello()
out = park(t, card="C1")
check("order", [c[0] for c in t.calls], ["comment", "update"])
check("dated comment carries the description",
      (t.calls[0][2].startswith("Parked for phone mode, 2026-11-2"), t.calls[0][2].endswith("\n\nthe description")),
      (True, True))
check("only Start moves", t.calls[1], ("update", "C1", {"start": START}))
check("prints the card link", "https://trello.com/c/old" in out, True)

print("\nthe trello plugin's own writers keep the hour, not just the day")
trello = phone_mode.load_trello()
requests = []
real_request = trello.trello_request
try:
    trello.trello_request = lambda method, path, session, params=None, body=None: requests.append(body) or {}
    trello.create_card("L", {"title": "n", "start": START}, ("k", "t"))
    trello.update_card("C", {"start": START}, ("k", "t"))
finally:
    trello.trello_request = real_request
check("start unchanged", [r["start"] for r in requests], [START, START])

print("\nargument checks")
desc = os.path.join(tempfile.mkdtemp(), "desc.md")
with open(desc, "w", encoding="utf-8") as f:
    f.write("d")
for name, argv in (("--board needs --name", ["park", "--board", "B", "--desc-file", desc]),
                   ("a board or a card", ["park", "--desc-file", desc])):
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            phone_mode.main(argv)
        code = 0
    except SystemExit as e:
        code = e.code
    check(name, code, 2)

print("\nhooks: the reminder runs at session start and on each prompt")
with open(os.path.join(PLUGIN, "hooks", "hooks.json"), encoding="utf-8") as f:
    hooks = json.load(f)["hooks"]
for event in ("SessionStart", "UserPromptSubmit"):
    commands = [h["command"] for group in hooks[event] for h in group["hooks"]]
    check(event, any(c.endswith('skills/phone-mode/scripts/phone_mode.py" remind') for c in commands), True)

if failures:
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("\nall passed")
