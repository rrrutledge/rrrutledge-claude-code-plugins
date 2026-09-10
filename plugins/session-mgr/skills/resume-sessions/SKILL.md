---
skill: resume-sessions
description: Find and resume Claude Code sessions that ended abruptly (without an "exit" command). Use when the user asks to resume sessions after a computer restart, crash, or unplanned shutdown, or when they want to recover sessions that weren't properly closed.
instructions: |-
  Find all Claude Code sessions that ended without an explicit "exit" command and launch each one
  in a new Windows Terminal tab for resumption. Skip sessions that are currently open.

  ## Step 1 — Find confirmed orphans (shared script)

  Run the shared detection script:

  ```bash
  python "$HOME/Dev/rrrutledge/rrrutledge-claude-code-plugins/plugins/session-mgr/skills/resume-sessions/scripts/find-orphans.py"
  ```

  It works in two parts:
  1. Scans running `claude.exe` processes for their session ID — from `--resume`/
     `--session-id` on the command line, or (for a bare launch with neither flag)
     `CLAUDE_CODE_SESSION_ID` in the process's own environment. In practice that env-var
     fallback never matches: Claude Code only sets `CLAUDE_CODE_SESSION_ID` for the child
     processes it spawns (hooks, the Bash tool), never on its own process.
  2. Reads the live-session registry (`~/.claude/session-mgr/live-sessions.json` — a dict of
     `{session_id: {cwd, started_at, pid, host_pid}}` for every session that has started but not
     cleanly ended, maintained by this plugin's `SessionStart`/`SessionEnd` hooks) and returns every
     entry whose session isn't in the active set from part 1 **and** whose recorded `pid`
     isn't a still-live `claude.exe` either. That `pid` — the launching `claude.exe`'s PID,
     found by walking the hook's own process ancestry at `SessionStart` — is what actually
     covers a bare launch with no `--resume`/`--session-id`: since part 1 can't discover such a
     session's id from either the command line or the (dead) env-var fallback, without the pid
     check a still-open bare-launched tab would be misdetected as an orphan and get a
     duplicate resume tab spawned on top of it. Two kinds of session survive both checks as a
     **confirmed** one to resume, needing no content heuristics: a hard crash or forced restart
     (which never fires `SessionEnd`, so the entry is never removed), and a real user tab closed
     abruptly with the window/tab X (which fires `SessionEnd` with reason `"other"` — the hook
     keeps that entry in place rather than deregistering it, because the tab carries a `host_pid`
     marking it as a launched interactive tab the user parked and wants back). A deliberate end —
     `/exit`, `/clear`, logout, or the plugin's own `self_close` primitive — deregisters the
     session, and a reason-`"other"` end with no `host_pid` (a background or scheduled `claude`
     run) deregisters too, so neither is ever resurrected.

  It also applies the self-close tail check before returning anything: a session that ends
  itself by force-killing its own tab dies before the harness can fire `SessionEnd`, so its
  registry entry survives even though the close was deliberate. The proper self-close
  primitive (`scripts/end-session.py`, next to the launcher) fires the SessionEnd hooks first
  and can't leave this residue, but entries written before a session's tooling adopted it —
  or by any independently-authored force-kill — still can. The script scans each candidate's
  last ~30 transcript entries for an **executed** self-close among its final actions — a
  `taskkill /PID <pid> /T /F`, a `close-session.py` / `end-session.py` run, or a
  `session-mgr:close` skill call that actually ran, not merely a passing mention of those names
  in the session's context (a session editing the drainer references them without closing
  itself). A match means it closed itself on purpose, so the script excludes it from the output
  AND deletes its entry from `live-sessions.json` (so a later run doesn't re-litigate it) — you
  don't need to re-check this by hand.

  Prints a JSON array to stdout: `[{"session_id", "cwd", "started_at"}, ...]`. Everything in
  this list is confirmed — go straight to the launch list (Step 3) for these; do not apply
  Step 2's `last_user_text` exclusion rules to them (those are for the fallback scan only,
  next section) — the registry already proved they were still open.

  Registry entries are self-healing: resuming a session re-fires `SessionStart` (re-adding
  it), and a later clean exit fires `SessionEnd` (removing it) — so nothing needs manual
  pruning beyond what the script already does for self-closed sessions.

  ## Step 2 — Fallback scan for sessions the registry doesn't cover

  The registry only covers sessions started after this hook was installed. For completeness (and
  as a safety net if a registry write ever fails), also run the content-heuristic scan below, then
  merge its results with Step 1's, de-duplicating by session ID.

  ```python
  import importlib.util
  import os, json
  from datetime import datetime

  PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
  # Reuse Step 1's script for the active-process scan rather than re-implementing it —
  # step1_orphans is the JSON list Step 1's `python find-orphans.py` call printed.
  spec = importlib.util.spec_from_file_location(
      "find_orphans",
      os.path.expanduser("~/Dev/rrrutledge/rrrutledge-claude-code-plugins/plugins/session-mgr/"
                          "skills/resume-sessions/scripts/find-orphans.py"))
  find_orphans = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(find_orphans)
  active_ids = find_orphans.active_session_ids()
  confirmed_ids = {o["session_id"] for o in step1_orphans}
  already_found = confirmed_ids | active_ids  # from Step 1

  abrupt = []

  for project_dir in os.listdir(PROJECTS_DIR):
      full_project = os.path.join(PROJECTS_DIR, project_dir)
      if not os.path.isdir(full_project):
          continue

      for fname in os.listdir(full_project):
          if not fname.endswith(".jsonl"):
              continue
          session_id = fname.replace(".jsonl", "")
          if session_id in already_found:
              continue

          fpath = os.path.join(full_project, fname)
          title = None
          cwd = None
          last_user_text = None

          try:
              with open(fpath, encoding="utf-8") as f:
                  for line in f:
                      line = line.strip()
                      if not line:
                          continue
                      try:
                          entry = json.loads(line)
                      except json.JSONDecodeError:
                          continue
                      t = entry.get("type")
                      if t == "ai-title" and not title:
                          title = entry.get("aiTitle")
                      if t == "user":
                          if not cwd and entry.get("cwd"):
                              cwd = entry["cwd"]
                          msg = entry.get("message", {})
                          content = msg.get("content") if isinstance(msg, dict) else None
                          if isinstance(content, str) and content.strip():
                              last_user_text = content.strip()
          except Exception:
              continue

          if last_user_text is None:
              continue
          if last_user_text.lower() == "exit":
              continue

          mtime = os.path.getmtime(fpath)
          abrupt.append({
              "session_id": session_id,
              "cwd": cwd,
              "title": title or "(no title)",
              "last_user_text": last_user_text,
              "mtime": mtime,
              "mtime_str": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
          })

  abrupt.sort(key=lambda x: x["mtime"])
  ```

  ### Exclusions — apply only these, and only in this fallback scan

  Exclude a session found by the fallback scan only if its last_user_text matches one of these
  exact machine-generated patterns — do NOT apply any other judgment about whether a session looks
  "complete" or "worth resuming":

  - Proper exit: last_user_text is exactly `<local-command-stdout>Goodbye!</local-command-stdout>`
    or `<local-command-stdout>Bye!</local-command-stdout>`
  - Automated triage prompt: last_user_text starts with `You are the drainer poller's triage step`
    (a self-contained classification job that completes and ends on its own — never a live
    conversation to resume)
  - Deliberate self-close: the transcript tail shows the session killing its own tab — the same
    self-close tail check Step 1's find-orphans.py applies to registry entries (a
    `taskkill /PID <pid> /T /F` or a `close-session.py` / `end-session.py` invocation among its
    final actions)

  Do **not** exclude sessions whose last message is a `<task-notification>` block. A pending
  task-notification means a background action (a Monitor watch, a browser-chauffeur command, etc.)
  never reported back - that is itself evidence of an interrupted session, not a reason to skip it.
  A notification looks machine-generated even when the session around it was live: a session
  mid-way through replying to a recruiter DM about a job posting reads exactly like one, right up
  to the moment a restart killed its background browser action. A `<status>killed</status>`
  field inside the notification is an especially strong signal the restart is exactly what
  interrupted it. If the notification instead shows a passive timeout (e.g. "Monitor timed out —
  re-arm if needed") and you want extra confidence before launching a whole tab for it, it's fine
  to spot-check whether the underlying thing being watched (a PR, a deployment) is already resolved
  - but default to including it.

  Launch everything else — short replies, drainer seeds, mid-sentence messages, one-word answers,
  all of it. Do not guess whether the user considered a session finished.

  ## Step 3 — Launch each session in a new WT tab

  Merge Step 1 (registry-confirmed) and Step 2 (fallback, after exclusions) into one list,
  de-duplicated by session ID. For each session in that list, run:

  ```bash
  "$HOME/AppData/Local/Microsoft/WindowsApps/wt.exe" -w 0 new-tab \
    -d "<cwd_with_forward_slashes>" \
    --title "<short title (≤30 chars)>" \
    powershell -NoExit \
    -File "$HOME/Dev/rrrutledge/rrrutledge-claude-code-plugins/plugins/session-mgr/skills/resume-sessions/scripts/launch-session.ps1" \
    -Resume "<session_id>"
  ```

  Key rules for the wt.exe command:
  - Top-level command is `wt.exe`, NOT `powershell`
  - Use forward-slash drive paths for `-d` and `-File` (e.g. `C:/Users/...`)
  - Backslash paths from `cwd` must be converted to forward slashes
  - `-Resume` accepts only the UUID — no prose needed
  - Keep `--title` short and quote-free (no `"` inside the title string)

  Launch each tab sequentially (the Bash tool runs them one at a time naturally).

  ## Step 4 — Confirm

  Tell the user how many sessions were opened and list the titles, noting how many came from the
  registry (confirmed) versus the fallback scan (heuristic). If any sessions were skipped because
  they were already open, mention that count too.

  ## Notes

  - `launch-session.ps1` lives inside this plugin at
    `~/Dev/rrrutledge/rrrutledge-claude-code-plugins/plugins/session-mgr/skills/resume-sessions/scripts/launch-session.ps1`
    (the command above points there). Its `-Resume` flag runs `claude --resume <session_id>` in the
    correct working directory. The drainer plugin ships a thin resolver of its own that finds the
    newest *installed* copy of this launcher, so drainer workers aren't tied to a working-clone branch.
  - `claude --resume <session_id>` resumes an existing session by its UUID, picking up the full
    conversation history.
  - There are ~1,300 JSONL session files total; the fallback scan reads all of them but only the
    tail of each (last user message), so it completes in a few seconds.
  - The live-session registry (`hooks/session_registry.py`, wired in `hooks/hooks.json`) is what
    makes Step 1's find-orphans.py authoritative instead of another heuristic. It only reflects
    sessions started since the hook was installed — plan on the fallback scan doing more of the
    work until the registry has enough history built up.
  - `scripts/end-session.py` (next to the launcher) is the correct way for a session to close its
    own tab: it fires this plugin's SessionEnd hooks with the same payload the harness would send,
    then taskkills the hosting process tree. Anything that instructs a session to self-close
    should route through it (the drainer forwards via its own thin resolver,
    `close-session.py`) — a raw `taskkill` of the host PID skips SessionEnd and strands a
    registry entry.
  - `/close` runs `scripts/end-session.py` on demand, for an interactive session that's done and
    wants to close its own tab in one shot instead of typing `exit` twice (once for Claude Code,
    once for the PowerShell host). Requires `CLAUDE_HOST_PID` to be set, which the user's
    `$PROFILE` does automatically for any tab launched with a normal `powershell` host.
---
