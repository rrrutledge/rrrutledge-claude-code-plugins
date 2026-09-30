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
        --cwd "<repo dir>" --brief "<repo dir>/.tmp/handoff-<slug>.md" --model claude-sonnet-5
    python "<session-mgr>/skills/resume-sessions/scripts/spawn-session.py" --resume <session guid> \
        --cwd "<its cwd>"

--worker-of <job folder> (fresh launches only) makes the session a worker of a coordinator: a copy of
the seed file with the worker contract appended is written into the job folder and used as the seed,
and the worker (short id, slug, result path) is registered in `<job folder>/job.json`. See the
coordinate-workers skill.

A fresh launch needs --model and a name (--title, or the --summary-file text). A resume keeps the
session's own model and name unless --model/--title override them.

Prints the new session's short id (the handle `claude attach/logs/stop` take) and full guid, and
writes the guid to `<FILE>.session` beside the brief or prompt file - the receipt peek.py reads.
Exit code 1 means nothing was launched.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bg_session import launch, session_name  # noqa: E402
import worker_job  # noqa: E402

WORKER_DONE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker-done.py")

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
    a = ap.parse_args(argv)

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
    print(f"Launched in the background: {name or 'resumed session'} - session {short_id} ({receipt}). "
          f"Reach it from claude.ai/code, or `claude attach {short_id}` / `claude logs {short_id}` here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
