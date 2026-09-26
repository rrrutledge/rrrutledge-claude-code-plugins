"""Characterization test: run the hook exactly as the harness does — a fresh
`python hook.py` subprocess fed the tool payload on stdin — and assert it
produces the decision the corpus specifies.

Hermetic: AI disabled, trusted set pinned to fixtures/trusted_commands.json,
config from fixtures/config.json, learned store redirected to a temp file.
"""
import json
import os
import subprocess
import sys

import pytest

from _runner import canonical, setup_case
from corpus_def import CASES

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(TESTS_DIR)
HOOK = os.path.join(PLUGIN_DIR, "hook.py")
FIX = os.path.join(TESTS_DIR, "fixtures")


def run_hook(payload, cwd_path, learned_path, trusted_path, home_path):
    env = dict(os.environ)
    env["CLAUDE_CWD"] = cwd_path
    env["SAFE_COMPOUNDS_DISABLE_AI"] = "1"
    env["SAFE_COMPOUNDS_TRUSTED_JSON"] = trusted_path
    env["SAFE_COMPOUNDS_CONFIG_JSON"] = os.path.join(FIX, "config.json")
    env["SAFE_COMPOUNDS_LEARNED_JSON"] = learned_path
    # os.path.expanduser('~') must resolve to the case's {HOME} fixture dir
    # (not this machine's real home), or checks like is_path_within_claude_drainer
    # compare a {HOME}-rooted case path against the wrong base and never match.
    env["HOME"] = home_path
    env["USERPROFILE"] = home_path
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True, text=True, env=env, timeout=30,
    )
    return canonical(proc.stdout)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_decision(case, tmp_path, trusted_json):
    cwd_path, payload = setup_case(str(tmp_path), case)
    learned = os.path.join(str(tmp_path), "learned.json")
    home_path = os.path.join(str(tmp_path), "home")
    decision = run_hook(payload, cwd_path, learned, trusted_json, home_path)
    assert decision == case["expect"], f"{case['id']}: got {decision}, expected {case['expect']}"


def test_case_ids_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
