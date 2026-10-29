"""Claude Haiku safety analysis — a single request implementation shared by
every AI fallback (command/script/subcommand classification and temp-file
detection).

Set SAFE_COMPOUNDS_DISABLE_AI=1 to make every AI call return None (used by the
test suite and as a runtime kill-switch). Without credentials the call also
returns None, so callers must treat None as "could not decide".
"""
import json
import os
import subprocess
import urllib.error
import urllib.request

from .log import log_debug


def _ai_disabled():
    return os.environ.get('SAFE_COMPOUNDS_DISABLE_AI') == '1'


def _vertex_token():
    gcloud_path = os.path.join(
        os.path.expanduser('~'),
        'AppData', 'Local', 'Google', 'Cloud SDK', 'google-cloud-sdk', 'bin', 'gcloud.cmd'
    )
    if not os.path.exists(gcloud_path):
        gcloud_path = 'gcloud'
    try:
        result = subprocess.run(
            [gcloud_path, 'auth', 'application-default', 'print-access-token'],
            capture_output=True, timeout=10, text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception:
        return None


def _build_request(prompt, max_tokens):
    """Build an urllib Request for Vertex or the Anthropic API, or None."""
    vertex_project = os.environ.get('ANTHROPIC_VERTEX_PROJECT_ID')
    vertex_region = os.environ.get('CLOUD_ML_REGION', 'us-east5')
    if vertex_region in ('us', 'us-central1'):
        vertex_region = 'us-east5'
    anthropic_key = os.environ.get('ANTHROPIC_HOOK_API_KEY') or os.environ.get('ANTHROPIC_API_KEY')

    if vertex_project:
        log_debug(f"Using Vertex AI (project: {vertex_project}, region: {vertex_region})")
        token = _vertex_token()
        if not token:
            return None
        url = (f'https://{vertex_region}-aiplatform.googleapis.com/v1/projects/{vertex_project}'
               f'/locations/{vertex_region}/publishers/anthropic/models/claude-haiku-4-5:rawPredict')
        data = json.dumps({
            'anthropic_version': 'vertex-2023-10-16',
            'max_tokens': max_tokens,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode('utf-8')
        return urllib.request.Request(url, data=data, headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        })

    if anthropic_key:
        data = json.dumps({
            'model': 'claude-haiku-5-5',
            'max_tokens': max_tokens,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode('utf-8')
        return urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=data,
            headers={
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
                'x-api-key': anthropic_key,
            },
        )

    return None


def ask(prompt, max_tokens=10):
    """Send `prompt` to Haiku and return the response text as-is (stripped), or
    None if AI is disabled, unconfigured, or the request fails. Callers match
    keywords case-insensitively (e.g. `'SAFE' in text.upper()`)."""
    if _ai_disabled():
        return None
    try:
        req = _build_request(prompt, max_tokens)
        if req is None:
            return None
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result.get('content', [{}])[0].get('text', '').strip()
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, KeyError, IndexError) as e:
        log_debug(f"AI call failed: {type(e).__name__}: {str(e)[:100]}")
        return None


def call_ai(prompt):
    """Classify a SAFE/DANGEROUS prompt. Returns (verdict, reason): verdict is
    True (SAFE), False (DANGEROUS), or None (could not decide); reason is the
    one-line explanation when the prompt asks for `DANGEROUS: <reason>` and the
    model supplies one, else None. Callers that only need the verdict can ignore
    the reason — generating it costs nothing on a one-word SAFE reply."""
    text = ask(prompt, max_tokens=80)
    if text is None:
        return None, None
    upper = text.upper()
    if 'DANGEROUS' in upper:
        reason = text.split(':', 1)[1].strip() if ':' in text else ''
        log_debug(f"AI response: {text} -> DANGEROUS")
        return False, (reason or None)
    if 'SAFE' in upper:
        log_debug(f"AI response: {text} -> SAFE")
        return True, None
    log_debug(f"AI response: {text} -> undecided")
    return None, None
