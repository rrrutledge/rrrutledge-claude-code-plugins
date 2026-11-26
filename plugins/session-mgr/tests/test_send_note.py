"""Tests for bg_session's note to a live session: finding the session's address, and counting a delivery
only when the SendMessage call carried the exact address and text and reported success.

Run directly: python plugins/session-mgr/tests/test_send_note.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "skills", "resume-sessions", "scripts")))
import bg_session  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


print("session_address: the receipt's live session -> its messaging pipe, on its own account")
account = tempfile.mkdtemp(prefix="note-account-")  # the session's CLAUDE_CONFIG_DIR
os.makedirs(os.path.join(account, "sessions"))
PIPE = r"\\.\pipe\LOCAL\cc-msg-abc"
with open(os.path.join(account, "sessions", "4242.json"), "w", encoding="utf-8") as f:
    json.dump({"pid": 4242, "sessionId": "abc12345-0000", "messagingSocketPath": PIPE}, f)
agents = [{"id": "abc12345", "sessionId": "abc12345-0000", "pid": 4242, "configDir": account},
          {"id": "dead0000", "sessionId": "dead0000-0000", "configDir": account}]
check("a short-id receipt resolves", bg_session.session_address("abc12345", agents), (PIPE, account))
check("a guid receipt resolves", bg_session.session_address("abc12345-0000", agents), (PIPE, account))
check("a pid-less session has no address", bg_session.session_address("dead0000", agents), None)
with open(os.path.join(account, "sessions", "4242.json"), "w", encoding="utf-8") as f:
    json.dump({"pid": 4242, "sessionId": "someone-else", "messagingSocketPath": PIPE}, f)
check("a pid file now owned by another session is not trusted",
      bg_session.session_address("abc12345", agents), None)


def stream(to, message, result):
    """The stream-json lines a delivery call prints: the SendMessage call, then its result."""
    use = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "SendMessage", "input": {"to": to, "message": message}}]}}
    res = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": json.dumps(result)}]}]}}
    return json.dumps(use) + "\n" + json.dumps(res) + "\n"


print("\n_note_delivered: success counts only for the exact address and text")
TO, MSG = f"uds:{PIPE}", "A note.\nRead it."
delivered = bg_session._note_delivered
check("exact call, success -> delivered", delivered(stream(TO, MSG, {"success": True}), TO, MSG), True)
check("tool reported failure -> not delivered", delivered(stream(TO, MSG, {"success": False}), TO, MSG), False)
check("model doubled a backslash in the address -> not delivered",
      delivered(stream(TO.replace("\\pipe", "\\\\pipe"), MSG, {"success": True}), TO, MSG), False)
check("model altered the text -> not delivered",
      delivered(stream(TO, "something else", {"success": True}), TO, MSG), False)
check("no output -> not delivered", delivered("", TO, MSG), False)
status = json.dumps({"type": "system", "message": "retrying..."}) + "\n"
check("a status event with a text message is skipped, not a crash",
      delivered(status + stream(TO, MSG, {"success": True}), TO, MSG), True)

print("\nsend_note: one SendMessage-only call under the session's account, true on a verified delivery")
real_run = bg_session.run_bounded
calls = []


def fake_run(returncode):
    def run(args, timeout, input=None, **kw):
        calls.append((args, input, kw.get("env")))
        return subprocess.CompletedProcess(args, returncode, stream(TO, MSG, {"success": True}), "")
    return run


try:
    bg_session.run_bounded = fake_run(0)
    check("delivered", bg_session.send_note((PIPE, account), MSG, account), True)
    args, prompt, env = calls[-1]
    check("only the SendMessage tool", args[args.index("--tools") + 1], "SendMessage")
    check("the prompt carries the address and the note", (TO in prompt, MSG in prompt), (True, True))
    check("runs under the session's account", env["CLAUDE_CONFIG_DIR"], account)
    bg_session.run_bounded = fake_run(1)
    check("a failed call -> not delivered", bg_session.send_note((PIPE, None), MSG, account), False)
finally:
    bg_session.run_bounded = real_run

print("\nrun_bounded: text for stdin reaches the child")
echo = bg_session.run_bounded([sys.executable, "-c", "import sys; print(sys.stdin.read())"], 30, input="hello")
check("echoed", echo.stdout.strip(), "hello")

if failures:
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("\nall passed")
