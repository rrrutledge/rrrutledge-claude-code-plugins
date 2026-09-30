"""Shared Zoom Server-to-Server OAuth client for drainer Zoom-based adapters.

Any adapter that talks to Zoom's REST API - the plugin's own `zoom-adapter.py`, or a machine-local
adapter watching one specific meeting series - shares this one client instead of each carrying its own
copy of the OAuth token cache, the refresh flow, and the REST GET/download plumbing. Two adapters on the
same machine pointed at the same `token_cache` file also share one login: Zoom rotates the refresh token
on every use, so two independent copies of this logic reading/writing the same cache file would
invalidate each other's tokens.

Other processes on the machine share that cache file too (the host project's own Zoom scripts, a Zoom
MCP headersHelper), so every refresh runs under a cross-process lock: `<token_cache>.lock`, created
exclusively and treated as abandoned after a minute. Whoever takes the lock re-reads the cache first and
reuses a token another process just minted, so only one process ever spends a given refresh token.
Anything else writing the same cache follows the same protocol.

Provider-agnostic: raises `ProviderError` (imported lazily from `provider_base`, which every adapter
already has on `sys.path` by the time it runs under the poller) rather than assuming any one adapter's
error-handling shape.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone

from provider_base import ProviderError

API_BASE = "https://api.zoom.us"
OAUTH_URL = "https://zoom.us/oauth/token"
FRESH_SECONDS = 3500
LOCK_WAIT_SECONDS = 30
LOCK_STALE_SECONDS = 60


def _http(method, url, headers=None, data=None, timeout=45):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class ZoomClient:
    """Token-cache-backed Zoom REST client. One instance per adapter; point `token_cache` at a shared
    file to share a login with another adapter on the same machine."""

    def __init__(self, client_id, client_secret_env="ZOOM_CLIENT_SECRET",
                 refresh_bootstrap_env="ZOOM_REFRESH_TOKEN", token_cache=None, error_prefix="zoom"):
        self.client_id = client_id
        self.client_secret_env = client_secret_env
        self.refresh_bootstrap_env = refresh_bootstrap_env
        self.token_cache = token_cache
        self.error_prefix = error_prefix  # namespaces this client's ProviderError messages per adapter

    # --------------------------------------------------------------- token cache
    def _read_cache(self):
        try:
            with open(self.token_cache, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return {}

    def _write_cache(self, data):
        if not self.token_cache:
            return
        os.makedirs(os.path.dirname(self.token_cache), exist_ok=True)
        tmp = f"{self.token_cache}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.token_cache)

    @contextmanager
    def _refresh_lock(self):
        """Hold `<token_cache>.lock` for the duration of a refresh (see the module docstring)."""
        if not self.token_cache:
            yield
            return
        lock = f"{self.token_cache}.lock"
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(lock) > LOCK_STALE_SECONDS:
                        os.remove(lock)
                        continue
                except OSError:
                    continue
                if time.monotonic() > deadline:
                    raise ProviderError(
                        f"{self.error_prefix}: timed out waiting for the Zoom token lock at {lock}",
                        kind="auth")
                time.sleep(0.2)
        try:
            os.write(fd, str(os.getpid()).encode())
            yield
        finally:
            os.close(fd)
            try:
                os.remove(lock)
            except OSError:
                pass

    @staticmethod
    def _age_seconds(obtained_at):
        try:
            t = datetime.fromisoformat((obtained_at or "").replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - t).total_seconds()
        except (ValueError, TypeError):
            return 1e12

    # --------------------------------------------------------------- OAuth
    def access_token(self):
        """A fresh access token, refreshing (and rotating the refresh token in the cache) when the
        cached one is near expiry. Bootstraps from `refresh_bootstrap_env` if the cache has none."""
        cache = self._read_cache()
        if self._is_fresh(cache):
            return cache["access_token"]
        with self._refresh_lock():
            cache = self._read_cache()
            if self._is_fresh(cache):
                return cache["access_token"]
            return self._refresh(cache.get("refresh_token") or os.environ.get(self.refresh_bootstrap_env))

    def _is_fresh(self, cache):
        return bool(cache.get("access_token")) and self._age_seconds(cache.get("obtained_at")) < FRESH_SECONDS

    def _refresh(self, refresh_token):
        if not refresh_token:
            raise ProviderError(
                f"{self.error_prefix}: no Zoom refresh token - set {self.refresh_bootstrap_env} (first "
                f"run) or point token_cache at an existing Zoom token file.", kind="auth")
        secret = os.environ.get(self.client_secret_env)
        if not self.client_id or not secret:
            raise ProviderError(
                f"{self.error_prefix}: set client_id in drainer.local.md and {self.client_secret_env} "
                f"in the environment.", kind="config")
        auth = base64.b64encode(f"{self.client_id}:{secret}".encode()).decode()
        body = urllib.parse.urlencode(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}).encode()
        status, raw = _http("POST", OAUTH_URL, {
            "Authorization": "Basic " + auth,
            "Content-Type": "application/x-www-form-urlencoded",
        }, body)
        data = json.loads(raw.decode("utf-8", "replace")) if raw else {}
        if status != 200 or data.get("error"):
            raise ProviderError(
                f"{self.error_prefix}: zoom token refresh failed: {data.get('error') or status} "
                f"{data.get('reason', '')}".strip(), kind="auth")
        self._write_cache({
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token") or refresh_token,  # rotates each refresh
            "token_type": data.get("token_type"),
            "expires_in": data.get("expires_in"),
            "scope": data.get("scope"),
            "obtained_at": datetime.now(timezone.utc).isoformat(),
        })
        return data.get("access_token")

    # --------------------------------------------------------------- REST
    def get(self, path):
        """GET `{API_BASE}{path}` with a fresh bearer token. Returns `(status, parsed_json_or_raw_text)`."""
        status, raw = _http("GET", API_BASE + path, {
            "Authorization": "Bearer " + self.access_token(), "Content-Type": "application/json"})
        try:
            return status, json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    def download(self, url):
        """GET an absolute URL (e.g. a recording file's `download_url`) with a fresh bearer token.
        Returns the decoded text on 200, else `None`."""
        status, raw = _http("GET", url, {"Authorization": "Bearer " + self.access_token()})
        return raw.decode("utf-8", "replace") if status == 200 else None

    @staticmethod
    def double_encode(uuid):
        """A Zoom occurrence UUID must be double-URL-encoded when it begins with `/` or contains `//`;
        Zoom's own guidance is to always double-encode for safety, so every meeting-scoped endpoint call
        does it unconditionally."""
        return urllib.parse.quote(urllib.parse.quote(uuid, safe=""), safe="")
