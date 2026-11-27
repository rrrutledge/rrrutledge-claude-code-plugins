"""Phone mode: the switch a session reads while Russell is at his phone. SKILL.md, one folder up,
says which steps it parks.

    python phone_mode.py on | off | status
    python phone_mode.py park --board <id> --name <text> --desc-file <path> [--list <name>]
    python phone_mode.py park --card <id> --desc-file <path>
    python phone_mode.py remind

The switch is one marker file, ~/.claude/phone-mode: present means on. A running process keeps the
environment it started with, so only a file reaches the sessions already running when the switch
flips.

`on` also sends a note to every other live session. `park` puts work on a Trello card whose Start is
an hour from now, so the ordinary Trello drain picks it up again. `remind` is the awareness hook: one
line while the switch is on, nothing while it is off.
"""
import argparse
import datetime
import importlib
import os
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

SESSION_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               "..", "..", "resume-sessions", "scripts"))
sys.path.insert(0, SESSION_SCRIPTS)
import bg_session  # noqa: E402

MARKER = os.path.expanduser("~/.claude/phone-mode")
REMINDER = "Phone mode is on: before waiting on Russell, check `/session-mgr:phone-mode` for whether it parks that step"
NOTE = ("Russell turned phone mode on.\n"
        "If this session is waiting on him, check that step against /session-mgr:phone-mode now.\n"
        "Otherwise carry on, and check the same way before you next wait on him.")
# Each note costs one `claude -p` process, so this many go out side by side.
NOTES_AT_ONCE = 6
PARK_FOR = datetime.timedelta(hours=1)
# Where a new card goes: the first of these lists its board has. The Trello drain reads each of them,
# including on a board whose initiative holds its intake lists out of the queue.
PARK_LISTS = ("In Progress", "To Do", "Job Search Admin")


def is_on():
    return os.path.exists(MARKER)


def turn_on():
    os.makedirs(os.path.dirname(MARKER), exist_ok=True)
    with open(MARKER, "w", encoding="utf-8"):
        pass
    print("phone mode is on.")
    tell_open_sessions()


def turn_off():
    pathlib.Path(MARKER).unlink(missing_ok=True)
    print("phone mode is off.")


def tell_open_sessions():
    """Send NOTE to every live session but the one running this, and print who it missed."""
    agents = bg_session.claude_agents()
    if agents is None:
        print("Open sessions could not be listed, so none were told.")
        return
    me = os.environ.get("CLAUDE_CODE_SESSION_ID")
    others = [a for a in agents if a.get("pid") and a.get("sessionId") != me]

    def tell(agent):
        address = bg_session.session_address(agent.get("sessionId"), agents)
        return bool(address) and bg_session.send_note(address, NOTE, os.getcwd())

    with ThreadPoolExecutor(max_workers=NOTES_AT_ONCE) as pool:
        told = list(pool.map(tell, others))
    print(f"Told {sum(told)} of {len(others)} other open session(s).")
    for agent, ok in zip(others, told):
        if not ok:
            print(f"  not reached: {agent.get('name') or agent.get('sessionId')}")


def load_trello():
    """The trello plugin's trello_utils module, found the way worker-done.py finds it."""
    scripts = importlib.import_module("worker-done").trello_utils_dir()
    if not scripts:
        sys.exit("phone_mode: the trello plugin's trello_utils.py was not found.")
    sys.path.insert(0, scripts)
    import trello_utils
    return trello_utils


def park(desc, board=None, name=None, card=None, list_name=None):
    """Defer work by PARK_FOR: on the existing `card` (Start moved, `desc` added as a dated comment),
    else on a new card `name` on `board` carrying `desc`."""
    trello = load_trello()
    session = trello.get_trello_session()
    now = datetime.datetime.now(datetime.timezone.utc)
    due = now + PARK_FOR
    start = due.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if card:
        stamp = now.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        # The comment lands first, so a card is never deferred without its note.
        trello.add_comment(card, f"Parked for phone mode, {stamp}\n\n{desc}", session)
        url = trello.update_card(card, {"start": start}, session)["shortUrl"]
    else:
        lists = trello.get_board_lists(board, session)
        wanted = (list_name,) if list_name else PARK_LISTS
        target = next((lst for w in wanted for lst in lists if lst["name"] == w), None)
        if not target:
            sys.exit(f"phone_mode: no list named {' or '.join(wanted)} on that board. Pass --list with one of: "
                     + ", ".join(lst["name"] for lst in lists))
        new = {"title": name, "description": desc, "start": start}
        url = trello.create_card(target["id"], new, session)["shortUrl"]
    print(f"parked: {url} - starts {due.astimezone().strftime('%H:%M %Z')}.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for command in ("on", "off", "status", "remind"):
        sub.add_parser(command)
    p = sub.add_parser("park")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--board", help="board id for a new card")
    where.add_argument("--card", help="id of the card that already tracks the work")
    p.add_argument("--name", help="the new card's name (with --board)")
    p.add_argument("--list", dest="list_name", help="the new card's list, when the board has none of the usual ones")
    p.add_argument("--desc-file", required=True, help="file holding the card description or comment")
    a = ap.parse_args(argv)

    if a.command == "on":
        turn_on()
    elif a.command == "off":
        turn_off()
    elif a.command == "status":
        print("on" if is_on() else "off")
    elif a.command == "remind":
        if is_on():
            print(REMINDER)
    else:
        if a.board and not a.name:
            p.error("--board needs --name")
        with open(a.desc_file, encoding="utf-8") as f:
            desc = f.read().strip()
        park(desc, board=a.board, name=a.name, card=a.card, list_name=a.list_name)
    return 0


if __name__ == "__main__":
    # Session and list names carry characters the Windows console encoding lacks.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
