"""Tests for scripts/zoom_client.py's cross-process refresh lock.

The bug this guards against: Zoom rotates the refresh token on every use, and several processes share
one token cache file (both drainer Zoom adapters, plus the host project's own Zoom scripts). Two of them
refreshing at the same moment both spent the same refresh token; Zoom honored one and answered the other
with `invalid_grant`, and whichever wrote the cache last could leave a spent refresh token behind.

Now every refresh runs under `<token_cache>.lock`, and whoever takes the lock re-reads the cache first,
so a burst of concurrent callers on an expired token costs exactly one refresh.

No network or Zoom credentials: a fake `_http` stands in for Zoom's OAuth endpoint and counts refreshes,
and a temp directory holds the token cache. Run directly:
    python plugins/drainer/tests/test_zoom_client_refresh_lock.py
"""
import json
import os
import sys
import tempfile
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills", "drainer", "scripts"))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import zoom_client  # noqa: E402

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


def fake_oauth():
    state = {"refreshes": 0, "valid_refresh": "rt-0"}
    guard = threading.Lock()

    def _http(method, url, headers=None, data=None, timeout=45):
        time.sleep(0.05)  # widen the race window a real network round-trip would have
        with guard:
            sent = dict(p.split("=", 1) for p in data.decode().split("&"))["refresh_token"]
            if sent != state["valid_refresh"]:
                return 400, json.dumps({"error": "invalid_grant", "reason": "Invalid refresh token"}).encode()
            state["refreshes"] += 1
            state["valid_refresh"] = f"rt-{state['refreshes']}"
            return 200, json.dumps({
                "access_token": f"at-{state['refreshes']}", "refresh_token": state["valid_refresh"],
                "token_type": "bearer", "expires_in": 3599, "scope": "s"}).encode()
    return state, _http


def stale_cache(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "at-old", "refresh_token": "rt-0",
                   "obtained_at": "2026-01-01T00:00:00+00:00"}, f)


def test_concurrent_callers_share_one_refresh():
    with tempfile.TemporaryDirectory() as d:
        cache = os.path.join(d, "zoom-tokens.json")
        stale_cache(cache)
        state, zoom_client._http = fake_oauth()
        os.environ["ZOOM_CLIENT_SECRET_TEST"] = "x"
        results, errors = [], []

        def worker():
            client = zoom_client.ZoomClient("cid", client_secret_env="ZOOM_CLIENT_SECRET_TEST", token_cache=cache)
            try:
                results.append(client.access_token())
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        check("no caller hit invalid_grant", not errors)
        check("exactly one refresh for eight concurrent callers", state["refreshes"] == 1)
        check("every caller got the same fresh token", set(results) == {"at-1"})
        with open(cache, encoding="utf-8") as f:
            check("cache holds the live refresh token", json.load(f)["refresh_token"] == state["valid_refresh"])
        check("lock file released", not os.path.exists(cache + ".lock"))


def test_abandoned_lock_is_reclaimed():
    with tempfile.TemporaryDirectory() as d:
        cache = os.path.join(d, "zoom-tokens.json")
        stale_cache(cache)
        lock = cache + ".lock"
        with open(lock, "w") as f:
            f.write("99999")
        old = time.time() - zoom_client.LOCK_STALE_SECONDS - 5
        os.utime(lock, (old, old))
        state, zoom_client._http = fake_oauth()
        client = zoom_client.ZoomClient("cid", client_secret_env="ZOOM_CLIENT_SECRET_TEST", token_cache=cache)
        check("refresh proceeds past an abandoned lock", client.access_token() == "at-1")
        check("abandoned lock cleaned up", not os.path.exists(lock))


if __name__ == "__main__":
    test_concurrent_callers_share_one_refresh()
    test_abandoned_lock_is_reclaimed()
    print(f"\n{'FAILED' if failures else 'OK'}: {len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
