"""Launch a Claude session in the background (`claude --bg`) - the command-line front end of
bg_session.spawn_bg, for every caller that isn't Python importing it: a handoff from a worker or an
interactive session, a scheduled script's worker, a resume-on-completion.

Every session gets the same launch: Remote Control on (reachable from claude.ai/code and the phone),
manual permission mode, the same disallowed tools, a clean top-level identity, and the current Claude
account. Nothing opens a Windows Terminal tab.

Three ways to seed the session, all keeping the prose on disk so quotes and metacharacters never reach
the command line:
    --brief FILE        a handoff doc; the seed says "Resume from the handoff at FILE"
    --prompt-file FILE  a standing instructions file; the seed says "Your task instructions are in FILE",
                        led by --summary-file's one-line text when given
    --resume GUID       continue an existing session (full guid) with its history, in its own --cwd;
                        add --brief or --prompt-file to hand it a follow-up, or neither to just reopen it

Usage (via the Bash tool):
    python "<session-mgr>/skills/resume-sessions/scripts/spawn-session.py" --title "<short title>" \
        --cwd "<repo dir>" --brief "<repo dir>/.tmp/handoff-<slug>.md" --model sonnet
    python "<session-mgr>/skills/resume-sessions/scripts/spawn-session.py" --resume <session guid> \
        --cwd "<its cwd>"

--worker-of <job folder> (fresh launches only) makes the session a worker of a coordinator: a copy of
the seed file with the worker contract appended is written into the job folder and used as the seed,
and the worker (short id, slug, result path) is registered in `<job folder>/job.json`. See the
coordinate-workers skill.

--resume-me (fresh --brief launches only) is for a launching session whose own next step waits on the
new session's work: it appends the instruction to resume the launching session (the command
schedule-resume.py prints, for $CLAUDE_CODE_SESSION_ID in the current directory) to the end of the
brief, so the launcher's last step is to close paused. See resume-on-completion.md.

What the launching session does next is the launch's third answer, and a fresh launch from inside a
session records it (launch_marker.py) so the Stop hook can hold the session to it:
    (no flag)     the new session takes over: close this session now (the default)
    --resume-me   this session's next step waits on the new work: close paused (above)
    --keep-open   a side quest: this session carries on with its own work and stays open

A fresh launch needs --model and a name (--title, or the --summary-file text). A resume keeps the
session's own model and name unless --model/--title override them.

Prints the new session's short id (the handle `claude attach/logs/stop` take) and full guid, and
writes the guid to `<FILE>.session` beside the brief or prompt file - the receipt peek.py reads.
Exit code 1 means nothing was launched.
"""
import argparse
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bg_session import launch, session_name  # noqa: E402
import launch_marker  # noqa: E402
import worker_job  # noqa: E402

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
WORKER_DONE = os.path.join(SCRIPTS, "worker-done.py")

RESUME_ME_HEADING = "## When this is done, resume the session that launched you"
RESUME_ME = """

---

""" + RESUME_ME_HEADING + """

The session that launched you closed paused; its own next step waits on this work.
Once this work is genuinely done (Russell has submitted, merged, or confirmed whatever "done" means here), run this via the Bash tool without waiting to be asked, then tell Russell in one line which session you resumed:

    {command}
"""


def _schedule_resume():
    spec = importlib.util.spec_from_file_location("schedule_resume", os.path.join(SCRIPTS, "schedule-resume.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _append_resume_me(brief, session_id, cwd):
    """Append the resume-the-launcher instruction to the brief, once. Returns the command, or None
    when no launcher can be found to resume with."""
    command = _schedule_resume().resume_command(session_id, cwd)
    if not command:
        return None
    with open(brief, encoding="utf-8") as f:
        body = f.read()
    if RESUME_ME_HEADING not in body:
        with open(brief, "w", encoding="utf-8") as f:
            f.write(body.rstrip() + "\n" + RESUME_ME.format(command=command))
    return command

WORKER_CONTRACT = """

---

## Worker contract

You are a worker for the coordinator of job `{job}`; you report to it, not to Russell.
Ask Russell only for what only he can give; anything else goes in your result file.
Stage nothing that reaches another person - write drafts as files and list them under Artifacts.

Write your result to `{result}`, in this shape:

```markdown
---
status: done | failed
worker: <your session short id>
finished: <date and time, Central>
---
## Summary
Three to five lines (never over ~15): what was done, what is staged, what Russell still has to do.

## Artifacts
- PR: <url>
- Draft bodies: <path>
- Data: <path to anything large>

## Open items
- Anything the coordinator must decide or pass to Russell.
```

Put anything large in its own file beside the result and list it under Artifacts.
When the result file is written, run this without waiting for anyone:

    python "{worker_done}" --job "{folder}" --slug {slug}
"""


def _worker_seed_file(folder, slug, anchor, job):
    """A copy of the brief or prompt file with the worker contract appended, written into the job
    folder beside the worker's result so the original stays untouched. Returns the copy's path."""
    with open(anchor, encoding="utf-8") as f:
        body = f.read()
    contract = WORKER_CONTRACT.format(job=job.get("job", os.path.basename(folder)),
                                      result=worker_job.result_path(folder, slug),
                                      worker_done=WORKER_DONE, folder=folder, slug=slug)
    path = os.path.join(folder, f"{slug}.brief.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body.rstrip() + "\n" + contract)
    return path


def _read_summary(path):
    """The one-line lead from --summary-file, or "" when there is none. A missing or unreadable file
    only costs the descriptive lead, never the launch."""
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return " ".join(f.read().split())
    except OSError:
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--title", help="session name, shown in `claude agents` and the Claude app's session list")
    ap.add_argument("--cwd", "--repo", dest="cwd", default=os.getcwd(),
                    help="working directory for the session (a resume's own original cwd)")
    seed = ap.add_mutually_exclusive_group()
    seed.add_argument("--brief", help="handoff doc (.tmp/handoff-<slug>.md) holding the full brief")
    seed.add_argument("--prompt-file", help="instructions file the session opens and follows")
    ap.add_argument("--summary-file", help="one-line summary that leads a --prompt-file seed and names the session")
    ap.add_argument("--resume", help="full guid of an existing session to continue in the background")
    ap.add_argument("--model", help="explicit model id, per handoffs.md's model-choice rule")
    ap.add_argument("--worker-of", metavar="JOB_FOLDER",
                    help="launch this session as a worker of the coordinator whose job folder "
                         "(holding job.json) this is: appends the worker contract to the seed file "
                         "and registers the worker in job.json")
    ap.add_argument("--resume-me", action="store_true",
                    help="this (launching) session's next step waits on the new session's work: append "
                         "the command that resumes this session to the brief, then close this one paused")
    ap.add_argument("--keep-open", action="store_true",
                    help="a side quest: this (launching) session carries on with its own work after "
                         "the launch, so it stays open")
    a = ap.parse_args(argv)
    if a.keep_open and a.resume_me:
        print("spawn-session: --keep-open and --resume-me contradict each other: one stays open, "
              "the other closes paused.")
        return 1

    brief = os.path.abspath(a.brief) if a.brief else None
    prompt_file = os.path.abspath(a.prompt_file) if a.prompt_file else None
    anchor = brief or prompt_file
    if anchor and not os.path.isfile(anchor):
        print(f"spawn-session: file not found: {anchor}")
        return 1
    if not anchor and not a.resume:
        print("spawn-session: give --brief, --prompt-file, or --resume <session guid>.")
        return 1
    if not os.path.isdir(a.cwd):
        print(f"spawn-session: working directory not found: {a.cwd}")
        return 1

    summary = _read_summary(a.summary_file)
    name = session_name(a.title or summary, "") or None
    if not a.resume and not (a.model and name):
        print("spawn-session: a fresh launch needs --model and a name (--title or --summary-file).")
        return 1

    resume_me = None
    if a.resume_me:
        launcher_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if a.resume or not brief or a.worker_of:
            print("spawn-session: --resume-me needs a fresh launch with --brief (a coordinator's "
                  "workers resume it through --worker-of instead).")
            return 1
        if not launcher_id:
            print("spawn-session: --resume-me runs from inside the launching session via the Bash tool, "
                  "so $CLAUDE_CODE_SESSION_ID names the session to resume; it is not set here.")
            return 1
        resume_me = _append_resume_me(brief, launcher_id, os.getcwd())
        if not resume_me:
            print("spawn-session: --resume-me could not locate spawn-session.py to build the resume command.")
            return 1

    slug = folder = None
    if a.worker_of:
        folder = os.path.abspath(a.worker_of)
        if a.resume or not anchor:
            print("spawn-session: --worker-of needs a fresh launch with --brief or --prompt-file.")
            return 1
        try:
            job = worker_job.read_job(folder)
        except (OSError, ValueError) as e:
            print(f"spawn-session: no readable job.json in {folder} ({e}).")
            return 1
        slug = worker_job.unique_slug(job, a.title or summary)
        seed_file = _worker_seed_file(folder, slug, anchor, job)
        if brief:
            brief = seed_file
        else:
            prompt_file = seed_file

    short_id, receipt = launch(cwd=a.cwd, model=a.model, name=name, brief=brief,
                               prompt_file=prompt_file, lead=summary, resume=a.resume)
    if not short_id:
        print("spawn-session: `claude --bg` launch failed or returned no session id; nothing was launched.")
        return 1
    if slug:
        worker_job.register_worker(folder, slug, short_id)
        print(f"Registered worker {slug} ({short_id}); its result goes to {worker_job.result_path(folder, slug)}.")
    print(f"Launched in the background: the '{name or 'resumed session'}' session. "
          f"Reach it from the Claude app or claude.ai/code, or `claude attach {short_id}` / `claude logs {short_id}` here "
          f"(id {short_id}, {receipt}; name the session to Russell by its title).")
    if resume_me:
        print("The brief now ends with the command that resumes this session when that work is done. "
              "Leave the paper trail (a drainer worker: a dated comment on its card), then close this "
              "session paused this turn: session-mgr:close with --pause (a drainer worker: close-session.py --pause).")
    _record_launch(a, short_id, name or summary, bool(resume_me), bool(slug))
    return 0


def _record_launch(a, short_id, title, resume_me, is_worker):
    """Record the launch for the Stop hook and print what this session does next. A resume of an
    existing session, a coordinator's worker, and a launch from outside any session are not a handoff
    from a session that then has to decide, so they record nothing."""
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if a.resume or is_worker or not session_id:
        return
    mode = "resume" if resume_me else "keep-open" if a.keep_open else "handoff"
    if not launch_marker.write(session_id, mode, title or "", short_id):
        print(f"WARNING: session {session_id} has no entry in the live-session registry "
              f"({launch_marker.session_registry.REGISTRY_PATH}), so the Stop hook cannot hold it to this launch. "
              "A session always registers at start, so this is a bug: the SessionStart hook did not run for "
              "this session, or another session's registry write overwrote its entry. Tell Russell, then "
              "follow the close-or-keep-open instruction below by hand.")
    if mode == "handoff":
        print("Next for this session: the new session now holds the work, so close this one in this same "
              "turn with session-mgr:close (a drainer worker: close-session.py). If Russell asked you to "
              "carry on with your own work after this launch, run `python \""
              + os.path.join(SCRIPTS, "launch_marker.py") + "\" keep-open` instead. If your own next "
              "step waits on the launched work, you needed --resume-me.")


if __name__ == "__main__":
    sys.exit(main())
