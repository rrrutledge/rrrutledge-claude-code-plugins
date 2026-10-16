"""Finish a worker session in one step, and bring its coordinator back when it was the last.

A worker runs this as its final action, once its result file is written:

    python "<this file>" --job "<job folder>" --slug <worker slug>

1. Checks `<job folder>/<slug>.md` exists and carries a `status: done | failed` line; if not, it
   says so and exits 1, leaving the worker open to fix the file.
2. Posts a one-line comment on the job's Trello card (`card_id` in job.json; skipped when empty),
   so the record survives even if `.tmp/` is cleaned. A Trello failure is reported, never fatal.
3. When every registered worker has a result and the coordinator has sealed the job, takes the
   exclusive resume lock and runs job.json's `resume_command`, so two workers finishing together
   resume the coordinator once.
4. Closes this worker with end-session.py (`claude rm`, falling back to stop with the reason).

The coordinator runs it once, after launching every worker and before pausing itself:

    python "<this file>" --job "<job folder>" --seal

Sealing marks the job fully launched (until then no worker resumes the coordinator, since workers
still being spawned would look like "everyone is done") and prints whether every worker has already
reported, in which case the coordinator carries on instead of pausing.

Exit codes: 0 done, 1 result file missing or malformed, 2 job folder unreadable.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import worker_job  # noqa: E402

END_SESSION = os.path.join(HERE, "end-session.py")


def trello_utils_dir():
    """The newest installed trello plugin's scripts dir, else the working clone's."""
    base = os.path.expanduser(os.path.join(
        "~", ".claude", "plugins", "cache", "rrrutledge-claude-code-plugins", "trello"))
    rel = os.path.join("skills", "trello", "scripts")
    if os.path.isdir(base):
        def version(name):
            try:
                return tuple(int(p) for p in name.split("."))
            except ValueError:
                return (0,)
        for v in sorted(os.listdir(base), key=version, reverse=True):
            if os.path.isfile(os.path.join(base, v, rel, "trello_utils.py")):
                return os.path.join(base, v, rel)
    clone = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "trello", rel))
    return clone if os.path.isfile(os.path.join(clone, "trello_utils.py")) else None


def comment_on_card(card_id, text):
    """Post `text` on the Trello card; returns an error string, or None on success."""
    scripts = trello_utils_dir()
    if not scripts:
        return "the trello plugin's trello_utils.py was not found"
    sys.path.insert(0, scripts)
    try:
        import trello_utils
        trello_utils.add_comment(card_id, text, trello_utils.get_trello_session())
    except Exception as e:  # any Trello/network/credential failure must not strand the worker
        return str(e)
    return None


def seal(folder):
    job = worker_job.read_job(folder)
    job["sealed"] = True
    worker_job.write_job(folder, job)
    missing = worker_job.missing_results(job)
    if missing:
        print(f"worker-done: job sealed; waiting on {len(missing)} worker(s): {', '.join(missing)}. "
              "Pause now; the last worker to finish resumes you.")
    else:
        print("worker-done: job sealed and every worker has already reported - do not pause; "
              "read the results and carry on.")
    return 0


def resume_coordinator(folder, job):
    if not job.get("sealed"):
        print("worker-done: the coordinator has not sealed the job yet; it will check results itself.")
        return
    if not job.get("resume_command"):
        print("worker-done: job.json has no resume_command; nothing to resume.")
        return
    if not worker_job.take_resume_lock(folder):
        print("worker-done: another worker already resumed the coordinator.")
        return
    print("worker-done: every worker has reported - resuming the coordinator.")
    subprocess.run(job["resume_command"], shell=True, check=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--job", required=True, help="the job folder (holds job.json and the result files)")
    ap.add_argument("--slug", help="this worker's slug, from its contract")
    ap.add_argument("--seal", action="store_true", help="coordinator only: mark every worker launched")
    a = ap.parse_args(argv)
    folder = os.path.abspath(a.job)
    try:
        job = worker_job.read_job(folder)
    except (OSError, ValueError) as e:
        print(f"worker-done: no readable job.json in {folder} ({e}).")
        return 2
    if a.seal:
        return seal(folder)
    if not a.slug:
        print("worker-done: give --slug (or --seal, for the coordinator).")
        return 1

    result = worker_job.result_path(folder, a.slug)
    status = worker_job.result_status(result)
    if status is None:
        print(f"worker-done: {result} is missing or has no `status: done | failed` line. "
              "Write the result file first, then run this again.")
        return 1

    card_id = job.get("card_id")
    if card_id:
        err = comment_on_card(card_id, f"Worker {a.slug} {status} - results at {result}")
        print("worker-done: commented on the Trello card." if err is None
              else f"worker-done: Trello comment failed ({err}); continuing.")

    if not worker_job.missing_results(job):
        resume_coordinator(folder, job)
    else:
        print(f"worker-done: still waiting on {', '.join(worker_job.missing_results(job))}.")

    return subprocess.run([sys.executable, END_SESSION], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
