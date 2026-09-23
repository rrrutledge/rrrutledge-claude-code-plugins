"""Unit tests for the pure parsing/classification helpers — the pieces most
prone to subtle regression during refactoring."""
import os
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, PLUGIN_DIR)

from safe_compounds.shell import (  # noqa: E402
    ASSIGNMENT_ONLY, extract_substitutions, first_word, has_unquoted_windows_drive_path,
    split_segments, strip_var_assignment,
)
from safe_compounds import config  # noqa: E402
from safe_compounds.commands import (  # noqa: E402
    is_git_command_safe, is_curl_safe, is_output_redirection_safe, is_sed_command_safe, is_start_safe,
    is_taskkill_safe, strip_safe_redirections, check_cwd_file_command,
)
from safe_compounds.paths import is_safe_read_location  # noqa: E402
from safe_compounds import paths  # noqa: E402
from safe_compounds import procs  # noqa: E402
from safe_compounds.mcp import classify_mcp_tool  # noqa: E402
from safe_compounds.enforce import (  # noqa: E402
    detect_complex_bash, detect_simple_expansion, detect_cd_compound, detect_function_definition,
    detect_plugin_cache_reference, detect_gh_api_contents_write, detect_raw_trello_write,
    enforce_bash,
)
from safe_compounds.scripts import check_node_segment, get_block_reason, reset_block_reason  # noqa: E402
from safe_compounds import ai  # noqa: E402
from safe_compounds import workflow  # noqa: E402
from safe_compounds.workflow import classify_workflow_tool  # noqa: E402
from safe_compounds.worktree_tool import classify_enter_worktree, classify_exit_worktree  # noqa: E402


def set_config(**kwargs):
    base = {
        "trusted_commands": [], "curl_domains": [], "mcp_blanket_servers": [],
        "mcp_blanket_tools": [], "trusted_script_dirs": [], "workflow_blanket_names": [],
        "trusted_destination_dirs": [],
    }
    base.update(kwargs)
    config._CONFIG = base


class TestSplitSegments:
    def test_operators(self):
        assert split_segments("a && b || c ; d | e") == ["a ", " b ", " c ", " d ", " e"]

    def test_quotes_protect_operators(self):
        assert split_segments("echo 'a && b'") == ["echo 'a && b'"]

    def test_double_quotes_protect_pipe(self):
        assert split_segments('echo "x | y"') == ['echo "x | y"']

    def test_bare_newline_splits_like_semicolon(self):
        # A bare (unquoted) newline terminates a statement in real bash,
        # exactly like `;` -- so each line of a multi-command Bash call (e.g.
        # `git checkout main\ngit pull\ngit worktree remove ...`) is evaluated
        # as its own independently-trusted segment.
        assert split_segments("echo foo\necho bar; echo baz") == ["echo foo", "echo bar", " echo baz"]

    def test_backslash_newline_continuation_stays_joined(self):
        # A trailing backslash before the newline is bash's real line
        # continuation syntax -- it does not terminate the statement.
        assert split_segments("grep -iE \\\n      \"pattern\"") == ["grep -iE \\\n      \"pattern\""]

    def test_newline_inside_quotes_does_not_split(self):
        assert split_segments('echo "a\nb"') == ['echo "a\nb"']


class TestFirstWord:
    def test_plain(self):
        assert first_word("grep -r foo") == "grep"

    def test_strips_env_assignment(self):
        assert first_word("FOO=bar grep x") == "grep"

    def test_pure_assignment(self):
        assert first_word("FOO=bar") == ASSIGNMENT_ONLY

    def test_path_basename(self):
        assert first_word("/usr/bin/grep x") == "grep"

    def test_exe_suffix(self):
        assert first_word("where.exe python") == "where"

    def test_leading_redirection(self):
        assert first_word("2>/dev/null grep x") == "grep"


class TestStripVarAssignment:
    def test_simple(self):
        assert strip_var_assignment("X=1 echo hi") == "echo hi"

    def test_substitution_value(self):
        assert strip_var_assignment("X=$(date) echo hi") == "echo hi"

    def test_no_assignment(self):
        assert strip_var_assignment("echo hi") == "echo hi"


class TestExtractSubstitutions:
    def test_simple(self):
        assert extract_substitutions("echo $(date)") == ["date"]

    def test_nested(self):
        assert extract_substitutions("echo $(a $(b))") == ["a $(b)"]

    def test_single_quote_suppresses(self):
        assert extract_substitutions("echo '$(date)'") == []


class TestGit:
    # allowlist: known read-only / reversible subcommands pass
    def test_status_ok(self):
        assert is_git_command_safe("git status") is True

    def test_commit_ok(self):
        assert is_git_command_safe("git commit -m x") is True

    def test_push_plain_ok(self):
        assert is_git_command_safe("git push") is True

    def test_reset_soft_ok(self):
        assert is_git_command_safe("git reset --soft HEAD~1") is True

    def test_checkout_branch_ok(self):
        assert is_git_command_safe("git checkout -b feature") is True

    def test_clean_dry_ok(self):
        assert is_git_command_safe("git clean -n") is True

    # destructive flags / unlisted subcommands fall through
    def test_push_force_blocked(self):
        assert is_git_command_safe("git push --force") is False

    def test_reset_hard_blocked(self):
        assert is_git_command_safe("git reset --hard") is False

    def test_checkout_dot_allowed(self):
        assert is_git_command_safe("git checkout .") is True

    def test_branch_delete_allowed(self):
        assert is_git_command_safe("git branch -D old") is True

    def test_clean_force_blocked(self):
        assert is_git_command_safe("git clean -fd") is False

    def test_rebase_allowed(self):
        assert is_git_command_safe("git rebase main") is True

    def test_sparse_checkout_allowed(self):
        assert is_git_command_safe("git -C .tmp/repo sparse-checkout set board/meeting") is True

    def test_global_opt_before_subcommand(self):
        assert is_git_command_safe("git -C /repo push --force") is False

    def test_redirect_not_mistaken_for_subcommand(self):
        # A redirect after global opts must not be read as the subcommand;
        # a subcommand-less git invocation is harmless (allow_empty).
        assert is_git_command_safe("git -C dir 2>/dev/null") is True
        assert is_git_command_safe("git -C dir 2> /dev/null") is True
        assert is_git_command_safe("git status 2>/dev/null") is True

    # "git -C <dir>" is not a special form to block: its safety is decided by the
    # subcommand + flags exactly as without -C. enforce_bash must let it through so
    # the subcommand checker can validate it (the shell CWD resets between Bash
    # calls, so "cd <dir> && git ..." is not a usable alternative).
    def test_global_opt_not_blocked_by_enforce(self):
        assert enforce_bash("git -C /repo push -u origin feature") is None
        assert enforce_bash("git -C /repo add file.txt") is None
        assert enforce_bash("git -C /repo commit -m x") is None


class TestCurl:
    def test_localhost(self):
        set_config()
        assert is_curl_safe("curl http://localhost:3000/x") is True

    def test_get_public(self):
        set_config()
        assert is_curl_safe("curl https://example.com") is True

    def test_post_public_blocked(self):
        set_config()
        assert is_curl_safe("curl -X POST https://example.com -d x=1") is False

    def test_post_configured_domain_ok(self):
        set_config(curl_domains=["mycorp.example"])
        assert is_curl_safe('curl -X POST https://api.mycorp.example/x ') is True

    def test_post_unconfigured_domain_blocked(self):
        set_config(curl_domains=["mycorp.example"])
        assert is_curl_safe('curl -X POST https://other.com/x') is False


class TestSed:
    def test_plain(self):
        assert is_sed_command_safe("sed s/a/b/ f") is True

    def test_inplace(self):
        assert is_sed_command_safe("sed -i s/a/b/ f") is False

    def test_inplace_combined(self):
        assert is_sed_command_safe("sed -ni s/a/b/ f") is False


class TestTaskkill:
    def test_self_pid_approved(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: 16552)
        assert is_taskkill_safe("taskkill /PID 16552 /T /F") is True

    def test_other_pid_rejected(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: 16552)
        assert is_taskkill_safe("taskkill /PID 9999 /T /F") is False

    def test_undetermined_host_rejected(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: None)
        assert is_taskkill_safe("taskkill /PID 16552 /T /F") is False

    def test_image_name_rejected(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: 16552)
        assert is_taskkill_safe("taskkill /IM chrome.exe /F") is False

    def test_remote_machine_rejected(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: 16552)
        assert is_taskkill_safe("taskkill /S remotehost /PID 16552 /F") is False

    def test_no_pid_rejected(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: 16552)
        assert is_taskkill_safe("taskkill /F") is False

    def test_non_numeric_pid_rejected(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: 16552)
        assert is_taskkill_safe("taskkill /PID abc /F") is False

    def test_multiple_pids_all_must_match(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: 16552)
        assert is_taskkill_safe("taskkill /PID 16552 /PID 9999 /F") is False

    def test_case_insensitive_flags(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: 16552)
        assert is_taskkill_safe("taskkill /pid 16552 /t /f") is True

    def test_msys_double_slash_approved(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: 16552)
        assert is_taskkill_safe("taskkill //PID 16552 //T //F") is True

    def test_msys_double_slash_other_pid_rejected(self, monkeypatch):
        monkeypatch.setattr(procs, "self_tab_host_pid", lambda: 16552)
        assert is_taskkill_safe("taskkill //PID 9999 //T //F") is False


class TestSelfTabHostPid:
    def _snapshot(self):
        # hook.py (pid 100) -> sh.exe (200) -> claude.exe (300) -> powershell.exe
        # (400, the tab host) -> WindowsTerminal.exe (500) -> services.exe (600)
        return {
            100: (200, 'python.exe'),
            200: (300, 'sh.exe'),
            300: (400, 'claude.exe'),
            400: (500, 'powershell.exe'),
            500: (600, 'windowsterminal.exe'),
            600: (0, 'services.exe'),
        }

    def test_finds_immediate_parent_of_claude(self):
        assert procs.self_tab_host_pid(start_pid=100, snapshot=self._snapshot()) == 400

    def test_stops_at_nearest_claude_in_nested_sessions(self):
        snap = self._snapshot()
        # An outer claude.exe further up must not be selected over the inner one.
        snap[500] = (700, 'claude.exe')
        snap[700] = (800, 'powershell.exe')
        assert procs.self_tab_host_pid(start_pid=100, snapshot=snap) == 400

    def test_no_claude_in_chain_returns_none(self):
        snap = self._snapshot()
        del snap[300]
        assert procs.self_tab_host_pid(start_pid=100, snapshot=snap) is None

    def test_empty_snapshot_returns_none(self):
        assert procs.self_tab_host_pid(start_pid=100, snapshot={}) is None

    def test_cycle_returns_none(self):
        snap = {100: (200, 'python.exe'), 200: (100, 'sh.exe')}
        assert procs.self_tab_host_pid(start_pid=100, snapshot=snap) is None


class TestOutputRedirectionSafe:
    def test_literal_gt_inside_quoted_pattern_is_not_a_redirect(self):
        # Regression: a literal '>' inside a quoted grep pattern (e.g. matching
        # ">$886" in a report) used to be misread as a real redirect operator,
        # producing a bogus absolute-path "target" and failing the segment.
        seg = 'grep -oE "eight to one|>\\$886" "report.html"'
        assert is_output_redirection_safe(seg) is True

    def test_quoted_tmp_target_still_recognized_safe(self):
        assert is_output_redirection_safe('echo hi > ".tmp/foo.txt"') is True

    def test_quoted_absolute_path_target_still_recognized_unsafe(self):
        assert is_output_redirection_safe('echo hi > "/etc/passwd"') is False

    def test_real_absolute_path_redirect_is_unsafe(self):
        assert is_output_redirection_safe('echo hi > /etc/passwd') is False

    def test_devnull_merge_is_safe(self):
        assert is_output_redirection_safe('cmd 2>/dev/null') is True


class TestStripSafeRedirections:
    def test_stderr_merge_suffix(self):
        assert strip_safe_redirections('start report.docx 2>&1').strip() == 'start report.docx'

    def test_stderr_merge_before_pipe(self):
        # split_segments() already separates "| head -2" into its own segment;
        # this is the "start ... 2>&1" segment as approve.py sees it.
        assert strip_safe_redirections('start report.docx 2>&1 ').strip() == 'start report.docx'

    def test_devnull_merge(self):
        assert strip_safe_redirections('cmd >/dev/null 2>&1').strip() == 'cmd'

    def test_no_redirect_unchanged(self):
        assert strip_safe_redirections('start report.docx') == 'start report.docx'

    def test_preserves_quoted_spaces(self):
        seg = 'start "" "C:/some dir/My File.docx" 2>&1'
        assert strip_safe_redirections(seg).strip() == 'start "" "C:/some dir/My File.docx"'


class TestStartSafe:
    def test_bare_docx(self):
        assert is_start_safe('start report.docx') is True

    def test_stderr_merge_does_not_defeat_target_check(self):
        # Regression: a trailing "2>&1" used to become the last token, so
        # is_start_safe read it as the launch target and rejected the command.
        assert is_start_safe(strip_safe_redirections('start report.docx 2>&1')) is True

    def test_unsafe_extension_still_rejected_with_redirect(self):
        assert is_start_safe(strip_safe_redirections('start evil.exe 2>&1')) is False


class TestMcp:
    def test_read(self):
        assert classify_mcp_tool("mcp__s__get_thing") is True

    def test_reversible_write(self):
        assert classify_mcp_tool("mcp__s__create_thing") is True

    def test_copy_is_reversible_write(self):
        assert classify_mcp_tool("mcp__s__copy_file") is True

    def test_destructive(self):
        assert classify_mcp_tool("mcp__s__delete_thing") is False

    def test_blanket_server_configured(self):
        set_config(mcp_blanket_servers=["myserver"])
        assert classify_mcp_tool("mcp__myserver__anything") is True

    def test_blanket_server_not_configured(self):
        set_config()
        # 'anything' is not a recognized verb, so without a blanket config it prompts
        assert classify_mcp_tool("mcp__myserver__anything") is False

    def test_unknown_verb(self):
        set_config()
        assert classify_mcp_tool("mcp__s__frobnicate_thing") is False

    def test_blanket_tool_configured(self):
        set_config(mcp_blanket_tools=["mcp__s__frobnicate_thing"])
        assert classify_mcp_tool("mcp__s__frobnicate_thing") is True

    def test_blanket_tool_does_not_affect_other_tools_on_same_server(self):
        set_config(mcp_blanket_tools=["mcp__s__frobnicate_thing"])
        assert classify_mcp_tool("mcp__s__other_thing") is False


class TestWorkflow:
    def setup_method(self):
        workflow.reset_block_reason()

    def test_named_blanket(self):
        set_config(workflow_blanket_names=["code-review"])
        assert classify_workflow_tool({"name": "code-review"}) is True

    def test_named_not_blanket(self):
        set_config(workflow_blanket_names=["code-review"])
        assert classify_workflow_tool({"name": "other"}) is False

    def test_no_config(self):
        set_config()
        assert classify_workflow_tool({"name": "code-review"}) is False

    def test_inline_script_never_blanket_via_name_even_if_it_claims_the_name(self, monkeypatch):
        # An inline/dynamic script's own text is unverified at call time, so a
        # self-declared meta.name must never grant blanket trust through the
        # name path — it always goes through the AI content check instead.
        set_config(workflow_blanket_names=["code-review"])
        monkeypatch.setattr(ai, "call_ai", lambda prompt: (None, None))
        script = "export const meta = {\n  name: 'code-review',\n  description: 'x',\n}\nlog('hi')"
        assert classify_workflow_tool({"script": script}) is False

    def test_no_name_or_script(self):
        set_config(workflow_blanket_names=["code-review"])
        assert classify_workflow_tool({}) is False

    def test_inline_script_ai_disabled_prompts_without_block(self):
        # With no AI available, the verdict is undecided: falls through to a
        # plain prompt, and must not be mistaken for an unsafe verdict.
        set_config()
        os.environ['SAFE_COMPOUNDS_DISABLE_AI'] = '1'
        try:
            assert classify_workflow_tool({"script": "log('hi')"}) is False
        finally:
            del os.environ['SAFE_COMPOUNDS_DISABLE_AI']
        assert workflow.get_block_reason() is None

    def test_inline_script_ai_safe_allows(self, monkeypatch):
        set_config()
        monkeypatch.setattr(ai, "call_ai", lambda prompt: (True, None))
        assert classify_workflow_tool({"script": "log('hi')"}) is True
        assert workflow.get_block_reason() is None

    def test_inline_script_ai_dangerous_blocks_with_reason(self, monkeypatch):
        set_config()
        monkeypatch.setattr(ai, "call_ai", lambda prompt: (False, "spawns an agent told to force-push"))
        assert classify_workflow_tool({"script": "log('hi')"}) is False
        reason = workflow.get_block_reason()
        assert reason is not None
        assert "BLOCKED" in reason
        assert "force-push" in reason

    def test_ai_prompt_includes_script_text(self, monkeypatch):
        # The AI check must actually see the script, not a placeholder.
        captured = {}

        def fake_call_ai(prompt):
            captured['prompt'] = prompt
            return True, None

        set_config()
        monkeypatch.setattr(ai, "call_ai", fake_call_ai)
        classify_workflow_tool({"script": "const X = 'unique-marker-42'"})
        assert "unique-marker-42" in captured['prompt']

    def test_script_path_is_read_and_checked(self, tmp_path, monkeypatch):
        set_config()
        script_file = tmp_path / "workflow.js"
        script_file.write_text("log('from file')", encoding="utf-8")
        monkeypatch.setattr(ai, "call_ai", lambda prompt: (True, None))
        assert classify_workflow_tool({"scriptPath": str(script_file)}) is True

    def test_missing_script_path_prompts_without_crash(self):
        set_config()
        assert classify_workflow_tool({"scriptPath": "/definitely/not/a/real/workflow.js"}) is False
        assert workflow.get_block_reason() is None


class TestEnterWorktree:
    def test_no_path_creates_new_worktree_approves(self):
        assert classify_enter_worktree({}) == (True, None)

    def test_name_only_creates_new_worktree_approves(self):
        assert classify_enter_worktree({"name": "some-feature"}) == (True, None)

    def test_path_under_claude_worktrees_blocks_with_direct_access_redirect(self, tmp_path):
        # Claude Code enforces its own permission-root confirmation for any
        # explicit-path EnterWorktree call, in-convention paths included, and
        # no hook decision can suppress it -- so approving here would buy
        # nothing. Block with a redirect to operate on the worktree directly
        # (git -C / absolute paths) instead of relocating into it.
        path = str(tmp_path / ".claude" / "worktrees" / "some-feature")
        approved, reason = classify_enter_worktree({"path": path})
        assert approved is False
        assert f'git -C "{path}"' in reason
        assert "git worktree move" not in reason

    def test_path_under_claude_worktrees_backslash_blocks(self, tmp_path):
        path = str(tmp_path / ".claude" / "worktrees" / "some-feature").replace("/", "\\")
        approved, reason = classify_enter_worktree({"path": path})
        assert approved is False
        assert "git -C" in reason

    def test_path_outside_claude_worktrees_blocks_with_move_redirect(self, tmp_path):
        # Same unavoidable-harness-prompt reasoning, plus this path isn't even
        # in convention yet -- redirect via `git worktree move` first, then
        # direct access.
        path = str(tmp_path / ".worktrees" / "some-feature")
        approved, reason = classify_enter_worktree({"path": path})
        assert approved is False
        assert "git worktree move" in reason
        assert ".claude/worktrees" in reason
        assert "git -C" in reason


class TestExitWorktree:
    def test_keep_approves(self):
        assert classify_exit_worktree({"action": "keep"}) is True

    def test_plain_remove_approves(self):
        # The tool itself refuses this call when there's uncommitted work or
        # unmerged commits, so a plain remove (no override) is safe to allow.
        assert classify_exit_worktree({"action": "remove"}) is True

    def test_remove_with_discard_changes_prompts(self):
        # discard_changes=True forces the tool past unsaved/unmerged work --
        # a genuinely destructive override, so this must not auto-approve.
        assert classify_exit_worktree({"action": "remove", "discard_changes": True}) is False

    def test_remove_with_discard_changes_false_approves(self):
        assert classify_exit_worktree({"action": "remove", "discard_changes": False}) is True

    def test_unrecognized_action_prompts(self):
        assert classify_exit_worktree({"action": "bogus"}) is False

    def test_missing_action_prompts(self):
        assert classify_exit_worktree({}) is False


class TestComplexBash:
    def test_subst(self):
        assert detect_complex_bash("echo $(date)")[0] is True

    def test_for(self):
        assert detect_complex_bash("for x in a; do echo $x; done")[0] is True

    def test_until(self):
        assert detect_complex_bash("until grep -q x file; do break; done")[0] is True

    def test_plain(self):
        assert detect_complex_bash("grep x file")[0] is False


class TestSimpleExpansion:
    def test_custom_var(self):
        assert detect_simple_expansion("echo $MYTOKEN") == "MYTOKEN"

    def test_standard_var_ignored(self):
        assert detect_simple_expansion("echo $HOME") is None

    def test_single_quote_ignored(self):
        assert detect_simple_expansion("echo '$MYTOKEN'") is None


class TestCheckNodeSegment:
    def test_check_flag_short_circuits(self):
        # `node --check <file>` only parses for syntax errors, never executes,
        # so it is auto-approved without reading or AI-judging the file.
        set_config()
        assert check_node_segment("node --check path/to/some/script.js") is True

    def test_check_flag_with_extra_spacing(self):
        set_config()
        assert check_node_segment("node  --check  foo.mjs") is True

    def test_unquoted_windows_path_gets_actionable_block(self):
        # A bare C:\...\script.js argument gets its backslashes stripped by
        # shell word-splitting before node ever sees it (real bash does this
        # identically to shlex — it isn't shlex-specific). The missing-file
        # case should be turned into a clear, actionable deny instead of a
        # silent fall-through to a manual prompt.
        set_config()
        reset_block_reason()
        seg = r'node C:\definitely\not\a\real\path\script.js'
        assert check_node_segment(seg) is False
        reason = get_block_reason()
        assert reason is not None
        assert "BLOCKED" in reason
        assert "unquoted" in reason.lower()

    def test_missing_file_without_windows_path_gets_generic_block(self):
        # A plain missing file (no Windows-path hazard) still denies, with the
        # generic "use the full path" message instead of the mangled-path one.
        set_config()
        reset_block_reason()
        assert check_node_segment("node /definitely/not/a/real/script.js") is False
        reason = get_block_reason()
        assert reason is not None
        assert "BLOCKED" in reason
        assert "unquoted" not in reason.lower()
        assert "full absolute path" in reason

    def test_double_quoted_windows_path_not_flagged_as_mangled(self):
        # Double-quoting preserves backslashes, so this isn't the mangled-path
        # hazard — it should fail for the ordinary "file not found" reason.
        set_config()
        reset_block_reason()
        seg = r'node "C:\definitely\not\a\real\path\script.js"'
        assert check_node_segment(seg) is False
        reason = get_block_reason()
        assert reason is not None
        assert "unquoted" not in reason.lower()
        assert "full absolute path" in reason


class TestScratchpadTrustedScriptDir:
    def test_session_scratchpad_path_is_trusted(self):
        # Mirrors the harness's actual per-session scratch layout:
        # .../Temp/claude/<project-hash>/<session-id>/scratchpad/<file>.js
        path = os.path.join(
            tempfile.gettempdir(), 'claude', 'C--Users-russe-Dev-some-repo',
            'a9ca91a0-8176-421a-8e53-95c64e6606ae', 'scratchpad', 'get-link.js',
        )
        assert paths.is_in_trusted_script_dir(path) is True

    def test_check_node_segment_skips_ai_for_scratchpad_script(self):
        # A script living in the scratchpad should be trusted by location
        # alone, with no AI content check (and thus no dependence on model
        # availability/latency for something already safe by convention).
        set_config()
        scratch_dir = os.path.join(tempfile.gettempdir(), 'claude', 'proj-hash', 'sess-id', 'scratchpad')
        os.makedirs(scratch_dir, exist_ok=True)
        script_path = os.path.join(scratch_dir, 'probe.js')
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write("console.log('hi')\n")
        try:
            assert check_node_segment(f'node "{script_path}"') is True
        finally:
            os.remove(script_path)


class TestHasUnquotedWindowsDrivePath:
    def test_bare_path_flagged(self):
        assert has_unquoted_windows_drive_path(r'node C:\Users\russe\script.js') is True

    def test_double_quoted_path_not_flagged(self):
        assert has_unquoted_windows_drive_path(r'node "C:\Users\russe\script.js"') is False

    def test_forward_slash_path_not_flagged(self):
        assert has_unquoted_windows_drive_path('node C:/Users/russe/script.js') is False

    def test_posix_style_path_not_flagged(self):
        assert has_unquoted_windows_drive_path('node /c/Users/russe/script.js') is False

    def test_flags_mid_command_occurrence(self):
        seg = r'node script.js C:\Users\russe\Dev\out'
        assert has_unquoted_windows_drive_path(seg) is True


class TestMissingScriptBlocks(object):
    """A relative script filename that doesn't resolve against CLAUDE_CWD should
    deny with an actionable message (use the full path), not silently fall
    through to a bare approval prompt."""

    def setup_method(self):
        reset_block_reason()

    def test_missing_relative_script_denies_with_full_path_instruction(self, tmp_path):
        set_config()
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        assert check_node_segment("node gmail.js --search=foo") is False
        reason = get_block_reason()
        assert reason is not None
        assert "BLOCKED" in reason
        assert "gmail.js" in reason
        assert "full absolute path" in reason
        assert str(tmp_path).replace('\\', '/').lower() in reason.replace('\\', '/').lower()

    def test_existing_script_does_not_trip_missing_script_block(self, tmp_path):
        set_config()
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        os.environ['SAFE_COMPOUNDS_DISABLE_AI'] = '1'
        (tmp_path / "script.js").write_text("console.log('hi')", encoding="utf-8")
        try:
            check_node_segment("node script.js")
        finally:
            del os.environ['SAFE_COMPOUNDS_DISABLE_AI']
        # AI is disabled so the verdict is undecided (falls through to a plain
        # prompt), but the missing-script block must not have fired.
        assert get_block_reason() is None


class TestSensitiveReadDirsExcluded:
    """Credential/token stores under home must never be treated as a safe
    cp/mv/ln source, even though home itself is otherwise trusted."""

    def test_ssh_dir_is_not_safe(self):
        assert is_safe_read_location(os.path.expanduser("~/.ssh/id_rsa")) is False

    def test_aws_credentials_not_safe(self):
        assert is_safe_read_location(os.path.expanduser("~/.aws/credentials")) is False

    def test_powershell_profile_not_safe(self):
        path = os.path.expanduser("~/Documents/PowerShell/Microsoft.PowerShell_profile.ps1")
        assert is_safe_read_location(path) is False

    def test_claude_ms_graph_token_cache_not_safe(self):
        assert is_safe_read_location(os.path.expanduser("~/.claude/ms-graph/token-cache.json")) is False

    def test_ordinary_home_file_still_safe(self):
        assert is_safe_read_location(os.path.expanduser("~/notes.txt")) is True

    def test_cp_from_ssh_dir_into_cwd_not_approved(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        src = os.path.expanduser("~/.ssh/id_rsa")
        assert check_cwd_file_command(f'cp "{src}" ".tmp/id_rsa"', "cp") is False

    def test_cp_from_ordinary_home_file_into_cwd_approved(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        src = os.path.expanduser("~/notes.txt")
        assert check_cwd_file_command(f'cp "{src}" ".tmp/notes.txt"', "cp") is True

    def test_cp_to_downloads_approved_by_default(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        set_config()
        src = tmp_path / "resume.pdf"
        dest = os.path.expanduser("~/Downloads/resume.pdf")
        assert check_cwd_file_command(f'cp "{src}" "{dest}"', "cp") is True

    def test_cp_to_configured_trusted_destination_approved(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        set_config(trusted_destination_dirs=[str(tmp_path / "downloads")])
        src = tmp_path / "resume.pdf"
        dest = tmp_path / "downloads" / "resume.pdf"
        assert check_cwd_file_command(f'cp "{src}" "{dest}"', "cp") is True

    def test_cp_outside_trusted_destination_still_denied(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path / "cwd")
        set_config(trusted_destination_dirs=[str(tmp_path / "downloads")])
        src = tmp_path / "cwd" / "resume.pdf"
        dest = tmp_path / "other-dir" / "resume.pdf"
        assert check_cwd_file_command(f'cp "{src}" "{dest}"', "cp") is False

    def test_cp_to_dev_null_approved_regardless_of_source(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        src = "C:/Windows/System32/drivers/etc/hosts"
        assert check_cwd_file_command(f'cp "{src}" /dev/null', "cp") is True


class TestSafeReadLocation:
    def test_system_temp_dir_is_safe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        assert is_safe_read_location(str(tmp_path / "foo.html")) is True

    def test_posix_tmp_prefix_is_safe(self):
        assert is_safe_read_location("/tmp/cdc-wb.html") is True

    def test_path_outside_home_and_temp_is_not_safe(self):
        assert is_safe_read_location("C:/Windows/System32/drivers/etc/hosts") is False


class TestCpFromSystemTemp:
    def test_cp_from_system_temp_into_cwd_approved(self, tmp_path, monkeypatch):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "systmp"))
        src = tmp_path / "systmp" / "cdc-wb.html"
        assert check_cwd_file_command(f'cp "{src}" ".tmp/cdc-wb.html"', "cp") is True

    def test_cp_from_outside_home_and_temp_into_cwd_not_approved(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        src = "C:/Windows/System32/drivers/etc/hosts"
        assert check_cwd_file_command(f'cp "{src}" ".tmp/hosts"', "cp") is False


class TestTouchRespectsAllowedEditDirs:
    """touch/chmod must honor the same destination rules as cp/mv/ln's
    destination check (CWD, worktrees, Write/Edit-allowed dirs, ~/.claude/plugins,
    any `.tmp` dir) -- not just CWD."""

    def test_touch_outside_cwd_but_within_allowed_edit_dir_approved(self, tmp_path, monkeypatch):
        os.environ['CLAUDE_CWD'] = str(tmp_path / "cwd")
        allowed_dir = tmp_path / "other-repo"
        monkeypatch.setattr(paths, "_ALLOWED_EDIT_DIRS", {str(allowed_dir)})
        target = allowed_dir / ".tmp" / "marker.done"
        assert check_cwd_file_command(f'touch "{target}"', "touch") is True

    def test_touch_outside_cwd_and_outside_allowed_dirs_denied(self, tmp_path, monkeypatch):
        # Deliberately not under a `.tmp` segment -- that carve-out is covered
        # separately by TestAnyTmpDirDestination, and would otherwise mask
        # what this test is actually checking (no allowed-dir match at all).
        os.environ['CLAUDE_CWD'] = str(tmp_path / "cwd")
        monkeypatch.setattr(paths, "_ALLOWED_EDIT_DIRS", set())
        target = tmp_path / "other-repo" / "output" / "marker.done"
        assert check_cwd_file_command(f'touch "{target}"', "touch") is False


class TestAnyTmpDirDestination:
    """.tmp/ is scratch space in every repo, not just the current one -- a
    cp/mv/touch/ln destination under a `.tmp` directory is approved
    regardless of which repo (or no repo) it falls under."""

    def test_is_within_any_tmp_dir_true_for_unrelated_repo(self, tmp_path):
        target = tmp_path / "some-other-repo" / ".tmp" / "marker.done"
        assert paths.is_within_any_tmp_dir(str(target)) is True

    def test_is_within_any_tmp_dir_false_for_lookalike_segment(self, tmp_path):
        target = tmp_path / "nottmpdir" / "marker.done"
        assert paths.is_within_any_tmp_dir(str(target)) is False

    def test_cp_into_unrelated_repos_tmp_dir_approved(self, tmp_path, monkeypatch):
        os.environ['CLAUDE_CWD'] = str(tmp_path / "cwd")
        monkeypatch.setattr(paths, "_ALLOWED_EDIT_DIRS", set())
        src = tmp_path / "cwd" / "snapshot-target.js"
        dest = tmp_path / "other-repo" / ".tmp" / "snapshot-target-copy.js"
        assert check_cwd_file_command(f'cp "{src}" "{dest}"', "cp") is True

    def test_touch_into_unrelated_repos_tmp_dir_approved(self, tmp_path, monkeypatch):
        os.environ['CLAUDE_CWD'] = str(tmp_path / "cwd")
        monkeypatch.setattr(paths, "_ALLOWED_EDIT_DIRS", set())
        target = tmp_path / "other-repo" / ".tmp" / "marker.done"
        assert check_cwd_file_command(f'touch "{target}"', "touch") is True


class TestPluginCacheBlocking:
    """Reading (or writing) a path under the installed plugin cache always
    triggers Claude Code's own "sensitive file" confirmation, no matter what
    this hook decides -- so the hook blocks with a rewrite instead of letting
    the command reach that unavoidable prompt."""

    def test_detects_forward_slash_windows_path(self):
        cmd = 'cp "C:/Users/russe/.claude/plugins/cache/repo/plugin/1.0.0/templates/x.js" ".tmp/x.js"'
        assert detect_plugin_cache_reference(cmd) is True

    def test_detects_backslash_windows_path(self):
        cmd = r'cp "C:\Users\russe\.claude\plugins\cache\repo\plugin\1.0.0\x.js" ".tmp\x.js"'
        assert detect_plugin_cache_reference(cmd) is True

    def test_detects_ls_against_cache(self):
        cmd = (r'ls "C:\Users\russe\.claude\plugins\cache\repo\drainer\1.45.2\skills\drainer\providers" '
               r'&& echo "---LOCAL---" && ls "C:\Users\russe\Dev\personal-ai-pod\drainer-local\providers"')
        assert detect_plugin_cache_reference(cmd) is True

    def test_detects_cat_against_cache(self):
        cmd = 'cat "~/.claude/plugins/cache/repo/plugin/1.0.0/SKILL.md"'
        assert detect_plugin_cache_reference(cmd) is True

    def test_detects_grep_against_cache(self):
        cmd = 'grep -r "TODO" "~/.claude/plugins/cache/repo/plugin/1.0.0/"'
        assert detect_plugin_cache_reference(cmd) is True

    def test_does_not_flag_installed_plugins_dir_outside_cache(self):
        cmd = 'cat "~/.claude/plugins/config.json"'
        assert detect_plugin_cache_reference(cmd) is False

    def test_does_not_flag_ordinary_repo_path(self):
        cmd = 'cp "plugins/browser-chauffeur/skills/browser-chauffeur/templates/x.js" ".tmp/x.js"'
        assert detect_plugin_cache_reference(cmd) is False

    def test_does_not_flag_interpreter_running_a_plugin_script(self):
        # Running a plugin's own script straight from the cache (python/node
        # as the interpreter) is the normal, supported way plugins invoke
        # their own tooling -- only cp/mv/ln/touch/chmod-style file ops on a
        # cache path trip Claude Code's sensitive-file confirmation.
        cmd = 'python "C:/Users/x/.claude/plugins/cache/repo/drainer/1.39.0/skills/drainer/scripts/close-session.py"'
        assert detect_plugin_cache_reference(cmd) is False

    def test_enforce_bash_blocks_with_rewrite_hint(self):
        cmd = 'cp "C:/Users/russe/.claude/plugins/cache/repo/plugin/1.0.0/templates/x.js" ".tmp/x.js"'
        reason = enforce_bash(cmd)
        assert reason is not None
        assert "plugin cache" in reason
        assert "Read tool" in reason


class TestGhApiContentsWriteBlocking:
    """`gh api` writing straight to the Contents API is always the wrong
    mechanism for editing a file in another repo (CLAUDE.md: clone, don't
    API) -- blocked outright, not left to the reversibility-based approve
    layer that governs other gh api write methods."""

    def test_detects_put(self):
        cmd = 'gh api repos/o/r/contents/path/file.md -X PUT --input payload.json'
        assert detect_gh_api_contents_write(cmd) is True

    def test_detects_delete(self):
        cmd = 'gh api -X DELETE repos/o/r/contents/path/file.md -f message=x -f sha=abc'
        assert detect_gh_api_contents_write(cmd) is True

    def test_get_not_flagged(self):
        cmd = 'gh api repos/o/r/contents/path/file.md'
        assert detect_gh_api_contents_write(cmd) is False

    def test_write_to_other_endpoint_not_flagged(self):
        cmd = 'gh api -X POST repos/o/r/issues -f title=x'
        assert detect_gh_api_contents_write(cmd) is False

    def test_enforce_bash_blocks_with_rewrite_hint(self):
        cmd = 'gh api repos/o/r/contents/path/file.md -X PUT --input payload.json'
        reason = enforce_bash(cmd)
        assert reason is not None
        assert 'Contents API' in reason
        assert 'clone' in reason.lower()


class TestRawTrelloWriteBlocking:
    """A raw curl write to api.trello.com is always the wrong mechanism -- every
    Trello mutation goes through the `trello` skill's trello_utils.py. Writes are
    blocked with a pointer to that path; a plain GET read is left alone."""

    def test_detects_post_with_verb(self):
        cmd = 'curl -X POST "https://api.trello.com/1/cards/abc/actions/comments?key=K&token=T" -d text=hi'
        assert detect_raw_trello_write(cmd) is True

    def test_detects_put(self):
        cmd = 'curl -X PUT "https://api.trello.com/1/cards/abc?key=K&token=T&due=2026-08-01"'
        assert detect_raw_trello_write(cmd) is True

    def test_detects_delete(self):
        cmd = 'curl -X DELETE "https://api.trello.com/1/cards/abc/idLabels/xyz?key=K&token=T"'
        assert detect_raw_trello_write(cmd) is True

    def test_detects_glued_verb(self):
        cmd = 'curl -XPOST "https://api.trello.com/1/cards?key=K&token=T&name=x&idList=y"'
        assert detect_raw_trello_write(cmd) is True

    def test_detects_body_flag_implying_post(self):
        cmd = 'curl "https://api.trello.com/1/cards/abc/actions/comments" --data-urlencode "text=hi"'
        assert detect_raw_trello_write(cmd) is True

    def test_get_read_not_flagged(self):
        cmd = 'curl "https://api.trello.com/1/boards/abc/cards?key=K&token=T"'
        assert detect_raw_trello_write(cmd) is False

    def test_explicit_get_with_body_not_flagged(self):
        cmd = 'curl -X GET "https://api.trello.com/1/boards/abc?key=K&token=T"'
        assert detect_raw_trello_write(cmd) is False

    def test_non_trello_write_not_flagged(self):
        cmd = 'curl -X POST "https://example.com/api/thing" -d foo=bar'
        assert detect_raw_trello_write(cmd) is False

    def test_enforce_bash_blocks_with_skill_pointer(self):
        cmd = 'curl -X POST "https://api.trello.com/1/cards/abc/actions/comments?key=K&token=T" -d text=hi'
        reason = enforce_bash(cmd)
        assert reason is not None
        assert 'trello_utils' in reason
        assert 'api.trello.com' in reason


class TestCdCompoundDetection:
    """Test cd followed by more commands in various formats."""

    def test_cd_with_ampersand_other_dir(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        assert detect_cd_compound("cd /other/dir && git status") == "/other/dir"

    def test_cd_with_semicolon_other_dir(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        assert detect_cd_compound("cd /other/dir; git status") == "/other/dir"

    def test_cd_with_newline_other_dir(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        assert detect_cd_compound("cd /other/dir\ngit status") == "/other/dir"

    def test_cd_with_newline_multiline(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        cmd = "cd /other/dir\necho test\ngit status"
        assert detect_cd_compound(cmd) == "/other/dir"

    def test_cd_to_same_dir_not_detected(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        assert detect_cd_compound(f"cd {tmp_path} && git status") is None

    def test_cd_alone_not_detected(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        assert detect_cd_compound("cd /other/dir") is None

    def test_cd_with_only_whitespace_after_not_detected(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        assert detect_cd_compound("cd /other/dir\n  \n\t\n") is None

    def test_cd_quoted_path_with_ampersand(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        assert detect_cd_compound('cd "/other/path with spaces" && ls') == "/other/path with spaces"

    def test_cd_quoted_path_with_newline(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        assert detect_cd_compound('cd "/other/path with spaces"\nls') == "/other/path with spaces"

    def test_original_reported_command(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        cmd = ("cd ~/Dev/rrrutledge/rrrutledge-claude-code-plugins\n"
               'echo "=== 1. restore ==="; git checkout origin/main -- plugins/drainer/drainer_config.py && echo "restored"\n'
               'echo "=== 2. rename ==="; git mv plugins/drainer/outlook-adapter.py plugins/drainer/outlook-rest-adapter.py')
        assert detect_cd_compound(cmd) == "~/Dev/rrrutledge/rrrutledge-claude-code-plugins"


class TestCdCompoundBlocking:
    """Test that enforce_bash blocks cd compound patterns with correct message."""

    def test_block_cd_ampersand(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /other/dir && git status")
        assert reason is not None
        assert "BLOCKED" in reason
        assert "NEVER" in reason
        assert "git -C /other/dir" in reason

    def test_block_cd_newline(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /other/dir\ngit status")
        assert reason is not None
        assert "BLOCKED" in reason
        assert "NEVER" in reason

    def test_block_message_has_script_examples(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /other && python script.py")
        assert "python /other/script.py" in reason
        assert "bash /other/script.sh" in reason

    def test_block_message_has_git_c_flag(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /repo && git status")
        assert "git -C /repo status" in reason
        assert "git -C /repo checkout" in reason

    def test_no_split_bash_calls_advice(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /other && git status")
        assert "split into" not in reason.lower()
        assert "two separate Bash tool calls" not in reason

    def test_no_block_cd_alone(self, tmp_path):
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        assert enforce_bash("cd /other/dir") is None


class TestFunctionDefinitionBlocking:
    """Test that enforce_bash blocks shell function definitions, since a function
    can silently shadow a real command and can't be statically validated."""

    def test_detects_cd_shim(self):
        assert detect_function_definition('cd() { :; }; gh pr create --title x') == 'cd'

    def test_detects_arbitrary_name(self):
        assert detect_function_definition('git() { echo pwned; }; git status') == 'git'

    def test_detects_function_keyword_form(self):
        assert detect_function_definition('function cd { :; }; ls') == 'cd'

    def test_no_match_on_plain_command(self):
        assert detect_function_definition('gh pr create --title x') is None
        assert detect_function_definition('echo "a() { b }"') is None

    def test_block_cd_shim_has_tailored_message(self):
        reason = enforce_bash('cd() { :; }; gh pr create --repo x/y --title z')
        assert reason is not None
        assert "BLOCKED" in reason
        assert 'cd() { ... }' in reason
        assert "already runs every command in the correct working directory" in reason

    def test_block_other_function_has_generic_message(self):
        reason = enforce_bash('git() { echo pwned; }; git status')
        assert reason is not None
        assert "BLOCKED" in reason
        assert "shadow a real command" in reason

    def test_no_block_plain_gh_command(self):
        assert enforce_bash('gh pr create --repo x/y --title z --body-file file.md') is None


class TestCdCompoundDetection:
    """Test cd followed by more commands in various formats."""

    def test_cd_with_ampersand_other_dir(self, tmp_path):
        """cd /other && cmd should be detected when target != cwd."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        target = detect_cd_compound("cd /other/dir && git status")
        assert target == "/other/dir"

    def test_cd_with_semicolon_other_dir(self, tmp_path):
        """cd /other ; cmd should be detected."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        target = detect_cd_compound("cd /other/dir; git status")
        assert target == "/other/dir"

    def test_cd_with_newline_other_dir(self, tmp_path):
        """cd /other\\ncmd should be detected (the new case!)."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        target = detect_cd_compound("cd /other/dir\ngit status")
        assert target == "/other/dir"

    def test_cd_with_newline_multiline_other_dir(self, tmp_path):
        """cd /other\\ncmd1\\ncmd2 should be detected."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        cmd = """cd /other/dir
echo "test"
git status
git commit"""
        target = detect_cd_compound(cmd)
        assert target == "/other/dir"

    def test_cd_to_same_dir_not_detected(self, tmp_path):
        """cd <cwd> && cmd should return None (handled by detect_cd_cwd_prefix)."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        target = detect_cd_compound(f"cd {tmp_path} && git status")
        assert target is None

    def test_cd_alone_not_detected(self, tmp_path):
        """Just 'cd /other' with no following commands should return None."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        target = detect_cd_compound("cd /other/dir")
        assert target is None

    def test_cd_with_only_whitespace_after_not_detected(self, tmp_path):
        """cd /other\\n\\n (only whitespace after) should return None."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        target = detect_cd_compound("cd /other/dir\n  \n\t\n")
        assert target is None

    def test_cd_quoted_path_with_ampersand(self, tmp_path):
        """cd "/path with spaces" && cmd should work."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        target = detect_cd_compound('cd "/other/path with spaces" && ls')
        assert target == "/other/path with spaces"

    def test_cd_quoted_path_with_newline(self, tmp_path):
        """cd "/path with spaces"\\ncmd should work."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        target = detect_cd_compound('cd "/other/path with spaces"\nls')
        assert target == "/other/path with spaces"

    def test_original_reported_command(self, tmp_path):
        """The actual command that wasn't blocked but should have been."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        cmd = """cd ~/Dev/rrrutledge/rrrutledge-claude-code-plugins
echo "=== 1. restore drainer_config.py to main ==="; git checkout origin/main -- plugins/drainer/providers/drainer_config.py && echo "restored"
echo "=== 2. rename ==="; git mv plugins/drainer/providers/outlook-adapter.py plugins/drainer/providers/outlook-rest-adapter.py"""
        target = detect_cd_compound(cmd)
        assert target == "~/Dev/rrrutledge/rrrutledge-claude-code-plugins"


class TestCdCompoundBlocking:
    """Test that enforce_bash blocks cd compound patterns with correct message."""

    def test_block_cd_ampersand(self, tmp_path):
        """cd && cmd should be blocked with helpful message."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /other/dir && git status")
        assert reason is not None
        assert "BLOCKED" in reason
        assert "cd /other/dir" in reason
        assert "NEVER" in reason
        assert "git -C /other/dir" in reason

    def test_block_cd_newline(self, tmp_path):
        """cd\\ncmd should be blocked with helpful message."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /other/dir\ngit status")
        assert reason is not None
        assert "BLOCKED" in reason
        assert "cd /other/dir" in reason
        assert "NEVER" in reason

    def test_block_message_has_script_examples(self, tmp_path):
        """Block message should show how to run scripts from current dir."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /other && python script.py")
        assert "python /other/script.py" in reason
        assert "bash /other/script.sh" in reason

    def test_block_message_has_git_c_flag(self, tmp_path):
        """Block message should show git -C alternative."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /repo && git status")
        assert "git -C /repo status" in reason
        assert "git -C /repo checkout" in reason

    def test_no_block_message_about_splitting_bash_calls(self, tmp_path):
        """Block message should NOT suggest splitting into two Bash calls (that doesn't work)."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /other && git status")
        assert "split into" not in reason.lower()
        assert "two separate Bash tool calls" not in reason

    def test_no_block_cd_alone(self, tmp_path):
        """Just 'cd /other' should not be blocked."""
        os.environ['CLAUDE_CWD'] = str(tmp_path)
        reason = enforce_bash("cd /other/dir")
        assert reason is None
