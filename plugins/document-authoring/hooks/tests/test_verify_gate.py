"""Tests for the writing-review gate (verify_gate.py).

Drive the script exactly as the harness does: the hook via stdin JSON, mint/check via
argv. Receipts are pointed at a per-test temp dir through WRITING_REVIEW_RECEIPT_DIR so
nothing touches the real ~/.claude store.
"""
import json
import os
import subprocess
import sys

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "verify_gate.py")


def run(args, stdin=None, cwd=None, receipt_dir=None):
    env = dict(os.environ)
    if receipt_dir:
        env["WRITING_REVIEW_RECEIPT_DIR"] = receipt_dir
    return subprocess.run(
        [sys.executable, HOOK, *args],
        input=stdin,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def decision(stdout):
    text = (stdout or "").strip()
    if not text:
        return "DEFER"
    obj = json.loads(text)
    return obj["hookSpecificOutput"]["permissionDecision"].upper()  # ALLOW / DENY / ASK


def bash_payload(command):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def mcp_payload(tool):
    return json.dumps({"tool_name": tool, "tool_input": {}})


def write_body(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# --- stage command detection + gating ---

def test_stage_without_receipt_is_blocked(tmp_path):
    receipts = str(tmp_path / "receipts")
    write_body(tmp_path, "reply.md", "Hi Addie - I can help with that.")
    r = run([], stdin=bash_payload("node gmail.js --reply --message-id=X --body-file=reply.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"
    assert "writing-review" in r.stdout


def test_mint_then_stage_is_allowed_through(tmp_path):
    receipts = str(tmp_path / "receipts")
    body = write_body(tmp_path, "reply.md", "Hi Addie - I can help with that.")
    m = run(["mint", str(body)], receipt_dir=receipts)
    assert m.returncode == 0
    r = run([], stdin=bash_payload("node gmail.js --reply --message-id=X --body-file=reply.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_edit_after_review_reblocks(tmp_path):
    receipts = str(tmp_path / "receipts")
    body = write_body(tmp_path, "reply.md", "Original reviewed text.")
    run(["mint", str(body)], receipt_dir=receipts)
    body.write_text("Happy to help!", encoding="utf-8")  # edited after review
    r = run([], stdin=bash_payload("node gmail.js --reply --message-id=X --body-file=reply.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"


def test_draft_new_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    write_body(tmp_path, "note.md", "A fresh outreach note with prose.")
    r = run([], stdin=bash_payload('node gmail.js --draft-new --to="a@b.com" --subject="Hi" --body-file=note.md'),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"


def test_ms_graph_body_file_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    write_body(tmp_path, "b.html", "<p>Some real prose here.</p>")
    r = run([], stdin=bash_payload("node mail.js --reply --message-id=X --body-file=b.html"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"


def test_ms_rest_json_space_form_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    write_body(tmp_path, "d.json", '{"comment": "<p>reply prose</p>"}')
    r = run([], stdin=bash_payload("node outlook-mail.js create-reply MSGID --json d.json"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"


def test_send_self_is_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    write_body(tmp_path, "s.md", "A note to myself with words.")
    r = run([], stdin=bash_payload("node mail.js --send-self --subject=Note --body-file=s.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"


def test_slack_send_without_receipt_is_blocked(tmp_path):
    receipts = str(tmp_path / "receipts")
    write_body(tmp_path, "dm.md", "Raimund - can you take a look at the pattern?")
    r = run([], stdin=bash_payload("node slack.js --send --channel=DAQPLK5PD --body-file=dm.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"
    assert "writing-review" in r.stdout


def test_slack_send_mint_then_allowed_through(tmp_path):
    receipts = str(tmp_path / "receipts")
    body = write_body(tmp_path, "dm.md", "Raimund - can you take a look at the pattern?")
    run(["mint", str(body)], receipt_dir=receipts)
    r = run([], stdin=bash_payload("node slack.js --send --channel=DAQPLK5PD --body-file=dm.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


# --- things that must NOT be gated ---

def test_send_draft_not_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    r = run([], stdin=bash_payload("node gmail.js --send-draft --draft-id=ABC"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pure_reaction_exempt(tmp_path):
    receipts = str(tmp_path / "receipts")
    write_body(tmp_path, "react.md", "👍")
    r = run([], stdin=bash_payload("node gmail.js --reply --message-id=X --body-file=react.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_unrelated_bash_defers(tmp_path):
    receipts = str(tmp_path / "receipts")
    r = run([], stdin=bash_payload("git status && ls -la"), cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_missing_body_file_fails_open(tmp_path):
    receipts = str(tmp_path / "receipts")
    r = run([], stdin=bash_payload("node gmail.js --reply --message-id=X --body-file=nope.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_unparseable_command_defers(tmp_path):
    receipts = str(tmp_path / "receipts")
    r = run([], stdin=bash_payload('node gmail.js --reply --body-file="unbalanced'),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


# --- MCP native Gmail ---

def test_native_gmail_create_draft_blocked(tmp_path):
    r = run([], stdin=mcp_payload("mcp__claude_ai_Gmail__create_draft"))
    assert decision(r.stdout) == "DENY"
    assert "gmail.js" in r.stdout


def test_native_gmail_send_blocked(tmp_path):
    r = run([], stdin=mcp_payload("mcp__claude_ai_Gmail__send_message"))
    assert decision(r.stdout) == "DENY"


def test_native_gmail_readonly_defers(tmp_path):
    r = run([], stdin=mcp_payload("mcp__claude_ai_Gmail__search_threads"))
    assert decision(r.stdout) == "DEFER"


def test_other_mcp_defers(tmp_path):
    r = run([], stdin=mcp_payload("mcp__playwright__browser_click"))
    assert decision(r.stdout) == "DEFER"


# --- check subcommand ---

def test_check_fails_without_receipt(tmp_path):
    receipts = str(tmp_path / "receipts")
    body = write_body(tmp_path, "t.md", "Prose needing review.")
    r = run(["check", str(body)], receipt_dir=receipts)
    assert r.returncode == 1


def test_check_passes_after_mint(tmp_path):
    receipts = str(tmp_path / "receipts")
    body = write_body(tmp_path, "t.md", "Prose needing review.")
    run(["mint", str(body)], receipt_dir=receipts)
    r = run(["check", str(body)], receipt_dir=receipts)
    assert r.returncode == 0


def test_check_reaction_passes_without_receipt(tmp_path):
    receipts = str(tmp_path / "receipts")
    body = write_body(tmp_path, "t.md", "✅")
    r = run(["check", str(body)], receipt_dir=receipts)
    assert r.returncode == 0


def test_compound_command_is_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    write_body(tmp_path, "reply.md", "A real reply with prose.")
    r = run([], stdin=bash_payload("echo staging && node gmail.js --reply --message-id=X --body-file=reply.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"


def test_grep_mentioning_script_not_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    r = run([], stdin=bash_payload("grep --reply gmail.js"), cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_browser_composer_with_body_file_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    write_body(tmp_path, "slack-body.md", "Hey folks - here is the update.")
    r = run([], stdin=bash_payload("node .tmp/compose-slack.js --cdp-port=9222 --body-file=slack-body.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"


def test_browser_composer_mint_then_pass(tmp_path):
    receipts = str(tmp_path / "receipts")
    body = write_body(tmp_path, "teams-body.md", "Hey folks - here is the update.")
    run(["mint", str(body)], receipt_dir=receipts)
    r = run([], stdin=bash_payload("node .tmp/compose-teams.js --cdp-port=9222 --body-file=teams-body.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_browser_screenshot_run_not_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    r = run([], stdin=bash_payload("node .tmp/screenshot.js --cdp-port=9222"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_body_file_without_cdp_or_known_script_not_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    write_body(tmp_path, "x.md", "Some prose in a file.")
    r = run([], stdin=bash_payload("node .tmp/other.js --body-file=x.md"),
            cwd=str(tmp_path), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_malformed_stdin_defers(tmp_path):
    r = run([], stdin="not json")
    assert decision(r.stdout) == "DEFER"


# --- PR prose gate ---
#
# These build a throwaway git repo with a `main` base and a feature branch, so the hook's
# own `git diff` runs against real history exactly as it would at PR time.

def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True)


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    r = str(repo)
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t.t")
    git(r, "config", "user.name", "T")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "seed")
    git(r, "checkout", "-q", "-b", "feature")
    return repo


def commit_file(repo, relpath, text):
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(str(repo), "add", "-A")
    git(str(repo), "commit", "-qm", f"add {relpath}")
    return p


def test_pr_create_with_unreviewed_skill_blocked(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "plugins/x/skills/y/SKILL.md", "# Y\n\nReal shipped prose here.\n")
    r = run([], stdin=bash_payload("gh pr create --base main --title T --body B"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"
    assert "SKILL.md" in r.stdout


def test_pr_create_after_mint_passes(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    body = commit_file(repo, "plugins/x/skills/y/SKILL.md", "# Y\n\nReal shipped prose here.\n")
    run(["mint", str(body)], receipt_dir=receipts)
    r = run([], stdin=bash_payload("gh pr create --base main --title T --body B"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pr_create_reblocks_after_edit(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    body = commit_file(repo, "docs/guide.md", "# Guide\n\nOriginal reviewed prose.\n")
    run(["mint", str(body)], receipt_dir=receipts)
    commit_file(repo, "docs/guide.md", "# Guide\n\nEdited prose after the review.\n")
    r = run([], stdin=bash_payload("gh pr create --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"


def test_pr_ready_not_gated(tmp_path):
    # `gh pr ready` acts on an already-open PR whose prose reached Russell at create time,
    # so it is the time-to-merge step and is not gated - even with unreviewed prose present.
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "README.md", "# Readme\n\nUnreviewed shipped prose.\n")
    r = run([], stdin=bash_payload("gh pr ready"), cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pr_merge_not_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "README.md", "# Readme\n\nUnreviewed shipped prose.\n")
    r = run([], stdin=bash_payload("gh pr merge --squash"), cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pr_create_repo_flag_other_repo_defers(tmp_path):
    # `-R owner/other` names a DIFFERENT repo than the cwd's own (origin here), so the cwd
    # diff would gate the wrong repo's prose - defer.
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    git(str(repo), "remote", "add", "origin", "https://github.com/rrrutledge/mine.git")
    commit_file(repo, "README.md", "# Readme\n\nUnreviewed shipped prose.\n")
    r = run([], stdin=bash_payload("gh pr create -R owner/other --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pr_create_repo_flag_same_repo_gates(tmp_path):
    # `-R <cwd's own owner/repo>` (the redundant defensive habit) targets this same repo, so
    # the cwd diff IS the PR's diff - gate it, denying when the changed prose has no receipt.
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    git(str(repo), "remote", "add", "origin", "https://github.com/rrrutledge/personal-ai-pod.git")
    commit_file(repo, "README.md", "# Readme\n\nUnreviewed shipped prose.\n")
    r = run([], stdin=bash_payload("gh pr create -R rrrutledge/personal-ai-pod --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"
    assert "README.md" in r.stdout


def test_pr_create_repo_flag_same_repo_url_form_gates(tmp_path):
    # The `--repo` value is a full https URL while origin is an ssh URL for the same repo;
    # both normalize to the same `owner/repo`, so this still gates.
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    git(str(repo), "remote", "add", "origin", "git@github.com:rrrutledge/personal-ai-pod.git")
    commit_file(repo, "README.md", "# Readme\n\nUnreviewed shipped prose.\n")
    r = run([], stdin=bash_payload("gh pr create --repo https://github.com/rrrutledge/personal-ai-pod --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"


def test_pr_create_repo_flag_no_remote_defers(tmp_path):
    # Flag present but the cwd repo has no remote to resolve, so the target can't be verified
    # as this same repo - defer (fail-open) rather than gate possibly-wrong prose.
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "README.md", "# Readme\n\nUnreviewed shipped prose.\n")
    r = run([], stdin=bash_payload("gh pr create -R owner/other --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pr_create_code_only_defers(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "src/app.py", "print('hi')\n")
    r = run([], stdin=bash_payload("gh pr create --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pr_create_tmp_markdown_not_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, ".tmp/handoff-seed.md", "# Handoff\n\nA change-explanation, not shipped.\n")
    r = run([], stdin=bash_payload("gh pr create --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pr_create_handoffs_dir_not_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "handoffs/next.md", "# Next session\n\nSeed prose for a handoff.\n")
    r = run([], stdin=bash_payload("gh pr create --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pr_create_default_base_resolves(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "docs/g.md", "# G\n\nUnreviewed prose with no --base flag.\n")
    r = run([], stdin=bash_payload("gh pr create --title T"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"


def test_pr_create_multi_file_lists_only_unreviewed(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    reviewed = commit_file(repo, "docs/a.md", "# A\n\nReviewed prose.\n")
    commit_file(repo, "docs/b.md", "# B\n\nUnreviewed prose.\n")
    run(["mint", str(reviewed)], receipt_dir=receipts)
    r = run([], stdin=bash_payload("gh pr create --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"
    assert "docs/b.md" in r.stdout
    assert "docs/a.md" not in r.stdout


def test_pr_create_pure_reaction_markdown_defers(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "docs/emoji.md", "👍\n")
    r = run([], stdin=bash_payload("gh pr create --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_grep_mentioning_gh_pr_create_not_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "docs/g.md", "# G\n\nUnreviewed prose.\n")
    r = run([], stdin=bash_payload('grep -r "gh pr create" docs/g.md'),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_gh_pr_list_not_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "docs/g.md", "# G\n\nUnreviewed prose.\n")
    r = run([], stdin=bash_payload("gh pr list"), cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pr_create_outside_git_repo_fails_open(tmp_path):
    receipts = str(tmp_path / "receipts")
    plain = tmp_path / "plain"
    plain.mkdir()
    r = run([], stdin=bash_payload("gh pr create --base main"),
            cwd=str(plain), receipt_dir=receipts)
    assert decision(r.stdout) == "DEFER"


def test_pr_create_base_uses_remote_not_stale_local(tmp_path):
    # gh diffs --base main against the REMOTE main. A local `main` that lags origin/main
    # must not drag unrelated files (changed on origin since) into this PR's diff.
    receipts = str(tmp_path / "receipts")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    repo = make_repo(tmp_path)  # on `feature`, at the seed commit
    r = str(repo)
    git(r, "checkout", "-q", "main")
    git(r, "remote", "add", "origin", str(origin))
    # An unrelated prose file lands on the real (remote) main after our branch diverged.
    commit_file(repo, "docs/other.md", "# Other\n\nSomeone else's unreviewed prose.\n")
    git(r, "push", "-q", "origin", "main")  # origin/main = seed -> other
    git(r, "checkout", "-q", "feature")  # clean switch: other.md is committed, removed here
    git(r, "branch", "-f", "main", "feature")  # local main lags at seed, off HEAD so no dirt
    # Our branch changes only our own file, which we review + mint.
    body = commit_file(repo, "docs/mine.md", "# Mine\n\nMy reviewed prose.\n")
    run(["mint", str(body)], receipt_dir=receipts)
    r2 = run([], stdin=bash_payload("gh pr create --base main"),
             cwd=r, receipt_dir=receipts)
    assert decision(r2.stdout) == "DEFER"


# --- mint --score-context (send-confidence) ---

def write_ctx(tmp_path, **overrides):
    ctx = {
        "channel": "gmail", "account": "isc", "recipient": "person@example.org",
        "thread_ref": "thread-1", "iid": None, "session_kind": "live",
        "ask": "Reply confirming the meeting.", "inputs": "Meeting is Tuesday at 2pm.",
        "turns_before_draft": 0, "sent_count": 5,
    }
    ctx.update(overrides)
    p = tmp_path / "score-ctx.json"
    p.write_text(json.dumps(ctx), encoding="utf-8")
    return str(p)


def test_mint_with_score_context_writes_receipt_and_log_row(tmp_path):
    receipts = str(tmp_path / "receipts")
    data_dir = str(tmp_path / "send-confidence-data")
    body = write_body(tmp_path, "reply.md", "Confirmed - see you Tuesday at 2pm, looking forward to it.")
    ctx_path = write_ctx(tmp_path)
    env = dict(os.environ)
    env["WRITING_REVIEW_RECEIPT_DIR"] = receipts
    env["SEND_CONFIDENCE_DIR"] = data_dir
    env["SEND_CONFIDENCE_JUDGE"] = "off"
    r = subprocess.run(
        [sys.executable, HOOK, "mint", str(body), "--score-context", ctx_path],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "writing-review receipt minted" in r.stdout
    assert "send-confidence: logged" in r.stdout
    log_files = list((tmp_path / "send-confidence-data").glob("log-*.jsonl"))
    assert len(log_files) == 1
    assert "\"event\": \"scored\"" in log_files[0].read_text(encoding="utf-8")


def test_mint_without_score_context_writes_no_log_row(tmp_path):
    receipts = str(tmp_path / "receipts")
    data_dir = str(tmp_path / "send-confidence-data")
    body = write_body(tmp_path, "reply.md", "Confirmed - see you Tuesday at 2pm.")
    env = dict(os.environ)
    env["WRITING_REVIEW_RECEIPT_DIR"] = receipts
    env["SEND_CONFIDENCE_DIR"] = data_dir
    r = subprocess.run([sys.executable, HOOK, "mint", str(body)], env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert "send-confidence" not in r.stdout
    assert not os.path.isdir(data_dir)


def test_mint_score_context_exception_still_exits_0_with_receipt(tmp_path):
    receipts = str(tmp_path / "receipts")
    body = write_body(tmp_path, "reply.md", "Confirmed - see you Tuesday at 2pm.")
    env = dict(os.environ)
    env["WRITING_REVIEW_RECEIPT_DIR"] = receipts
    # A nonexistent context file makes send_confidence.score_and_log raise - mint must still
    # succeed and the receipt must still exist.
    r = subprocess.run(
        [sys.executable, HOOK, "mint", str(body), "--score-context", str(tmp_path / "missing-ctx.json")],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "writing-review receipt minted" in r.stdout
    assert "send-confidence: not logged" in r.stderr
    text = normalized_text_for_test(body)
    h = content_hash_for_test(text)
    assert os.path.isfile(os.path.join(receipts, h + ".json"))


def normalized_text_for_test(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def content_hash_for_test(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_pr_create_after_compound_command_gated(tmp_path):
    receipts = str(tmp_path / "receipts")
    repo = make_repo(tmp_path)
    commit_file(repo, "docs/g.md", "# G\n\nUnreviewed prose.\n")
    r = run([], stdin=bash_payload("git push -u origin feature && gh pr create --base main"),
            cwd=str(repo), receipt_dir=receipts)
    assert decision(r.stdout) == "DENY"
