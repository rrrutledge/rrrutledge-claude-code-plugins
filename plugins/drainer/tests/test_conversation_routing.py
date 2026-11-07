"""Tests for one-conversation-one-live-worker routing: each provider's conversation key, the live-holder
scan, the route decision (forward / same-cycle hold / dispatch), the session address lookup, the
delivery check on the SendMessage call, the hand-over's records, and the live-session list a new
worker's seed carries.

Run directly:
    python plugins/drainer/tests/test_conversation_routing.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
PROVIDERS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers")
POLLER = os.path.join(SCRIPTS, "run-poller.py")

sys.path.insert(0, SCRIPTS)
import provider_base  # noqa: E402


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


poller = load(POLLER, "run_poller")
failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def adapter(file, name):
    """An adapter's Provider class built without its __init__, which locates a helper script."""
    cls = load(os.path.join(PROVIDERS, file), name).Provider
    return cls.__new__(cls)


slack = adapter("slack-adapter.py", "slack_adapter")
gmail = adapter("gmail-adapter.py", "gmail_adapter")
outlook = adapter("outlook-graph-adapter.py", "outlook_adapter")
teams = adapter("teams-adapter.py", "teams_adapter")

print("slack: a thread's @mention and its later replies share one key (the 2026-11-03 #isc-treasurer burst)")
CH, ROOT = "C03782DSK88", "1793724695.518669"
mention = {"channel": CH, "channelType": "thread", "ts": "1793726307.979309", "threadTs": ROOT}
reply1 = {"channel": CH, "channelType": "thread", "ts": "1793729462.312419", "threadTs": ROOT}
reply2 = {"channel": CH, "channelType": "thread", "ts": "1793730756.421599", "threadTs": ROOT}
keys = {slack.conversation_key(i) for i in (mention, reply1, reply2)}
check("three thread items -> one key", keys, {f"slack:{CH}:{ROOT}"})
root_unread = {"channel": CH, "channelType": "channel", "ts": ROOT, "threadTs": ""}
check("an Unread-in item for the thread's root message shares the thread's key",
      slack.conversation_key(root_unread), f"slack:{CH}:{ROOT}")
top_a = {"channel": CH, "channelType": "channel", "ts": "1793723372.599409", "threadTs": ""}
top_b = {"channel": CH, "channelType": "channel", "ts": "1793725772.527749", "threadTs": ""}
check("two top-level messages are two conversations",
      slack.conversation_key(top_a) == slack.conversation_key(top_b), False)
dm_a = {"channel": "D061W7Y6M6V", "channelType": "im", "ts": "1.1", "threadTs": ""}
dm_thread = {"channel": "D061W7Y6M6V", "channelType": "thread", "ts": "1.3", "threadTs": "1.1"}
check("a DM is one conversation, thread replies included",
      {slack.conversation_key(dm_a), slack.conversation_key(dm_thread)}, {"slack:D061W7Y6M6V"})
check("no channel -> no key", slack.conversation_key({}), None)

print("\nemail and Teams: the thread / conversation id, namespaced by provider")
check("gmail thread", gmail.conversation_key({"threadId": "18f"}), "gmail:18f")
check("outlook conversation", outlook.conversation_key({"conversationId": "AAQk"}), "outlook-graph:AAQk")
check("teams chat", teams.conversation_key({"id": "19:abc@thread.v2"}), "teams:19:abc@thread.v2")
check("no id -> no key", (gmail.conversation_key({}), outlook.conversation_key({})), (None, None))
check("a source with no conversation keys nothing", provider_base.ProviderBase().conversation_key({"x": 1}), None)


def workspace(sessions):
    """A runtime_dir holding, per (iid, receipt, record), the receipt and captured json a worker leaves."""
    rt = tempfile.mkdtemp(prefix="conv-route-")
    os.makedirs(os.path.join(rt, "seeds"))
    os.makedirs(os.path.join(rt, "items"))
    for iid, receipt, rec in sessions:
        with open(os.path.join(rt, "seeds", f"{iid}.prompt.txt.session"), "w", encoding="utf-8") as f:
            f.write(receipt)
        with open(os.path.join(rt, "items", f"{iid}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": iid, **rec}, f)
    return rt


print("\nopen_conversations: only a LIVE worker holds its conversation")
KEY = f"slack:{CH}:{ROOT}"
rt = workspace([("w1", "guid-live", {"conversationKey": KEY, "correspondent": "georg"}),
                ("w2", "guid-dead", {"conversationKey": "gmail:t2"}),
                ("w3", "guid-live2", {"correspondent": "x@y.z"})])
check("the live holder is found", poller.open_conversations(rt, {"guid-live", "guid-live2"}), {KEY: "guid-live"})
check("no live scan -> nothing held, every item launches", poller.open_conversations(rt, None), {})
check("open_correspondents still reads the same scan",
      poller.open_correspondents(rt, {"guid-live", "guid-live2"}), {"georg", "x@y.z"})

print("\nconversation_route: forward to a live holder, hold behind a same-cycle launch, else dispatch")
check("live holder -> forward", poller.conversation_route(KEY, {KEY: "g"}, set()), "forward")
check("claimed this cycle -> hold", poller.conversation_route(KEY, {}, {KEY}), "hold")
check("unheld conversation -> dispatch", poller.conversation_route(KEY, {}, set()), None)
check("no key -> dispatch", poller.conversation_route(None, {KEY: "g"}, {KEY}), None)
check("a screen-flagged item gets its own worker", poller.conversation_route(KEY, {KEY: "g"}, set(), True), None)
check("a screen-flagged item is not held behind a same-cycle launch",
      poller.conversation_route(KEY, {}, {KEY}, True), None)

print("\nsession_address: the receipt's live session -> its messaging pipe, on its own account")
account = tempfile.mkdtemp(prefix="conv-account-")  # the session's CLAUDE_CONFIG_DIR
os.makedirs(os.path.join(account, "sessions"))
PIPE = r"\\.\pipe\LOCAL\cc-msg-abc"
with open(os.path.join(account, "sessions", "4242.json"), "w", encoding="utf-8") as f:
    json.dump({"pid": 4242, "sessionId": "abc12345-0000", "messagingSocketPath": PIPE}, f)
agents = [{"id": "abc12345", "sessionId": "abc12345-0000", "pid": 4242, "configDir": account},
          {"id": "dead0000", "sessionId": "dead0000-0000", "configDir": account}]
check("a short-id receipt resolves", poller.session_address("abc12345", agents), (PIPE, account))
check("a guid receipt resolves", poller.session_address("abc12345-0000", agents), (PIPE, account))
check("a pid-less session has no address", poller.session_address("dead0000", agents), None)
with open(os.path.join(account, "sessions", "4242.json"), "w", encoding="utf-8") as f:
    json.dump({"pid": 4242, "sessionId": "someone-else", "messagingSocketPath": PIPE}, f)
check("a pid file now owned by another session is not trusted", poller.session_address("abc12345", agents), None)


def stream(to, message, result):
    """The stream-json lines a delivery call prints: the SendMessage call, then its result."""
    use = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "SendMessage", "input": {"to": to, "message": message}}]}}
    res = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": json.dumps(result)}]}]}}
    return json.dumps(use) + "\n" + json.dumps(res) + "\n"


print("\n_forward_delivered: success counts only for the exact address and text")
TO, MSG = f"uds:{PIPE}", "New drainer item x.\nRead it."
check("exact call, success -> delivered", poller._forward_delivered(stream(TO, MSG, {"success": True}), TO, MSG), True)
check("tool reported failure -> not delivered",
      poller._forward_delivered(stream(TO, MSG, {"success": False}), TO, MSG), False)
check("model doubled a backslash in the address -> not delivered",
      poller._forward_delivered(stream(TO.replace("\\pipe", "\\\\pipe"), MSG, {"success": True}), TO, MSG), False)
check("model altered the text -> not delivered",
      poller._forward_delivered(stream(TO, "something else", {"success": True}), TO, MSG), False)
check("no output -> not delivered", poller._forward_delivered("", TO, MSG), False)

print("\nforward_note: built from code values only, with a forward-slashed path")
note = poller.forward_note("slack-x", "C:\\rt\\items\\slack-x.json", "needs-you")
check("names the item and its path", ("slack-x" in note, "C:/rt/items/slack-x.json" in note), (True, True))
check("carries no backslash", "\\" in note, False)


class FakeProvider:
    name = "slack"

    def __init__(self, rt):
        self.rt, self.cleared = rt, []

    def capture(self, item, iid, runtime_dir):
        path = os.path.join(runtime_dir, "items", f"{iid}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"id": iid}, f)
        return path

    def clear(self, item):
        self.cleared.append(item["_id"])
        return True


def hand_over(delivered, address=(PIPE, None)):
    rt = workspace([])
    prov = FakeProvider(rt)
    recorded, sent = [], []
    poller.session_address = lambda receipt, agents: address
    poller.forward_to_worker = lambda addr, message, repo: sent.append(message) or delivered
    poller.seen_state = lambda *args: recorded.append(args)
    it = {"_id": "slack-new", "_source": "slack", "_bucket": "needs-you"}
    ok = poller.route_to_live_worker(it, prov, KEY, "guid-live", [], rt, rt)
    receipt = os.path.join(rt, "seeds", "slack-new.prompt.txt.session")
    item_json = os.path.join(rt, "items", "slack-new.json")
    rec = json.load(open(item_json, encoding="utf-8")) if os.path.exists(item_json) else None
    return ok, recorded, prov.cleared, (open(receipt).read() if os.path.exists(receipt) else None), rec, sent


real = (poller.session_address, poller.forward_to_worker, poller.seen_state)
try:
    print("\nroute_to_live_worker: a verified delivery records the item against the holder")
    ok, recorded, cleared, receipt, rec, sent = hand_over(True)
    check("handed over", ok, True)
    check("the receipt names the holder, so reconcile reads it live while the holder is", receipt, "guid-live")
    check("recorded seen under its own bucket", recorded, [("record", recorded[0][1], "slack", "slack-new", "needs-you")])
    check("the source is left for the holder to clear", cleared, [])
    check("the note tells the holder to clear it", "CLEAR" in sent[0], True)
    check("the captured item names its conversation", rec.get("conversationKey"), KEY)
    check("one note sent", len(sent), 1)

    print("\nroute_to_live_worker: a failed delivery leaves the item for a normal launch")
    ok, recorded, cleared, receipt, _, _ = hand_over(False)
    check("not handed over", ok, False)
    check("nothing recorded, cleared, or receipted", (recorded, cleared, receipt), ([], [], None))
    ok, recorded, _, _, _, sent = hand_over(True, address=None)
    check("no address -> not handed over, nothing sent", (ok, recorded, sent), (False, [], []))
finally:
    poller.session_address, poller.forward_to_worker, poller.seen_state = real

print("\nlive_peer_lines: every live, named session, for the new worker's seed")
lines = poller.live_peer_lines([
    {"name": "Newsletter", "pid": 1, "kind": "interactive", "status": "idle"},
    {"name": "Slack: thread", "pid": 2, "kind": "background", "state": "blocked"},
    {"name": "Stopped one", "kind": "background"},
    {"pid": 3, "kind": "background"},
])
check("live named sessions only, with their state", lines, ["- Newsletter (idle)", "- Slack: thread (blocked)"])
check("a failed scan lists nothing", poller.live_peer_lines(None), [])



def seed_for(item):
    """The seed spawn_worker writes for `item`, with the launch stubbed out."""
    real_spawn, bg = poller.spawn_bg, sys.modules["bg_session"]
    real_agents = bg.claude_agents
    poller.spawn_bg = lambda *a, **k: "abc12345"
    bg.claude_agents = lambda: []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            json_file = os.path.join(tmp, "item.json")
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump({"id": "x1", "source": "slack", "subject": "Thread reply", "triage": "needs-you"}, f)
            local = os.path.join(tmp, "config")
            os.makedirs(local)
            poller.spawn_worker("item-x1", json_file, tmp, os.path.join(tmp, "rt"), "sonnet", local, tmp, item,
                                peers=["- Newsletter (idle)"])
            with open(os.path.join(tmp, "rt", "seeds", "item-x1.prompt.txt"), encoding="utf-8") as f:
                return f.read()
    finally:
        poller.spawn_bg, bg.claude_agents = real_spawn, real_agents


print("\nspawn_worker seed: the live sessions to compare against, and a conversation holder it couldn't reach")
seed = seed_for({"_source": "slack", "subject": "Thread reply"})
check("lists the live sessions", "- Newsletter (idle)" in seed, True)
check("no holder line without a failed hand-over", "already on this item's conversation" in seed, False)
seed = seed_for({"_source": "slack", "subject": "Thread reply", "_conversationHolder": "Slack: @mention in #t"})
check("names the holder after a failed hand-over", "`Slack: @mention in #t`" in seed, True)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
