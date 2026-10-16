"""Tests for the coordinator/worker flow: worker_job.py, spawn-session.py --worker-of, worker-done.py.

Everything runs in-process against a temp job folder, with the `claude --bg` launch, the Trello
comment, the coordinator resume and the session close stubbed, so nothing is launched or closed.
Run directly: python plugins/session-mgr/tests/test_worker_flow.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills", "resume-sessions", "scripts"))
sys.path.insert(0, SCRIPTS)
import worker_job  # noqa: E402


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


spawn_session = load("spawn_session", "spawn-session.py")
worker_done = load("worker_done", "worker-done.py")

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def make_job(tmp, **extra):
    folder = os.path.join(tmp, "results", "demo")
    os.makedirs(folder)
    job = {"job": "demo", "card_id": "card1", "resume_command": "echo resume", "sealed": False,
           "workers": []}
    job.update(extra)
    worker_job.write_job(folder, job)
    return folder


def write_result(folder, slug, status="done"):
    with open(worker_job.result_path(folder, slug), "w", encoding="utf-8") as f:
        f.write(f"---\nstatus: {status}\nworker: abc\n---\n## Summary\nok\n")


def run_done(folder, argv, comment_err=None):
    """worker_done.main with the comment, the resume and the close stubbed. Returns
    (rc, comments, subprocess_calls)."""
    comments, calls = [], []
    real_comment, real_run = worker_done.comment_on_card, worker_done.subprocess.run
    worker_done.comment_on_card = lambda cid, text: comments.append((cid, text)) or comment_err
    worker_done.subprocess.run = lambda cmd, **kw: calls.append(cmd) or types.SimpleNamespace(returncode=0)
    try:
        rc = worker_done.main(["--job", folder] + argv)
    finally:
        worker_done.comment_on_card, worker_done.subprocess.run = real_comment, real_run
    return rc, comments, calls


def test_result_status():
    print("test: result_status reads the front-matter status line")
    with tempfile.TemporaryDirectory() as tmp:
        folder = make_job(tmp)
        check("missing file -> None", worker_job.result_status(worker_job.result_path(folder, "a")) is None)
        with open(worker_job.result_path(folder, "a"), "w", encoding="utf-8") as f:
            f.write("## Summary\nno front matter\n")
        check("no status line -> None", worker_job.result_status(worker_job.result_path(folder, "a")) is None)
        write_result(folder, "a", "failed")
        check("failed read", worker_job.result_status(worker_job.result_path(folder, "a")) == "failed")


def test_spawn_worker_registers_and_appends_contract():
    print("test: spawn-session --worker-of registers the worker and seeds a contract copy")
    with tempfile.TemporaryDirectory() as tmp:
        folder = make_job(tmp)
        brief = os.path.join(tmp, "brief.md")
        with open(brief, "w", encoding="utf-8") as f:
            f.write("# Do the thing\n")
        seen = {}

        def fake_launch(**kw):
            seen.setdefault("brief", kw["brief"])
            return "deadbeef", "receipt"

        real = spawn_session.launch
        spawn_session.launch = fake_launch
        try:
            rc = spawn_session.main(["--title", "Invite members", "--cwd", tmp, "--brief", brief,
                                     "--model", "m", "--worker-of", folder])
            rc2 = spawn_session.main(["--title", "Invite members", "--cwd", tmp, "--brief", brief,
                                      "--model", "m", "--worker-of", folder])
        finally:
            spawn_session.launch = real
        job = worker_job.read_job(folder)
        check("exit 0", rc == 0 and rc2 == 0, f"{rc} {rc2}")
        check("registered both workers with distinct slugs",
              [w["slug"] for w in job["workers"]] == ["invite-members", "invite-members-2"], job["workers"])
        check("registered the short id", job["workers"][0]["short_id"] == "deadbeef")
        check("result path in the job folder",
              job["workers"][0]["result"] == os.path.join(folder, "invite-members.md"))
        seed = seen["brief"]
        check("launched from the copy in the job folder", os.path.dirname(seed) == folder, seed)
        text = open(seed, encoding="utf-8").read()
        check("copy keeps the brief", text.startswith("# Do the thing"))
        check("copy carries the contract", "You are a worker for the coordinator of job `demo`" in text
              and "worker-done.py" in text and "invite-members.md" in text)
        check("original brief untouched", open(brief, encoding="utf-8").read() == "# Do the thing\n")


def test_spawn_worker_needs_job_and_fresh_launch():
    print("test: --worker-of refuses a missing job.json and a --resume launch")
    with tempfile.TemporaryDirectory() as tmp:
        brief = os.path.join(tmp, "b.md")
        open(brief, "w").close()
        empty = os.path.join(tmp, "nojob")
        os.makedirs(empty)
        rc = spawn_session.main(["--title", "t", "--cwd", tmp, "--brief", brief, "--model", "m",
                                 "--worker-of", empty])
        check("no job.json -> 1", rc == 1, rc)
        folder = make_job(tmp)
        rc = spawn_session.main(["--cwd", tmp, "--resume", "guid", "--worker-of", folder])
        check("resume -> 1", rc == 1, rc)


def test_done_without_result_refuses():
    print("test: worker-done with no result file -> exit 1, no comment, no close")
    with tempfile.TemporaryDirectory() as tmp:
        folder = make_job(tmp, workers=[{"short_id": "a", "slug": "a", "result": worker_job.result_path(folder_of(tmp), "a")}])
        rc, comments, calls = run_done(folder, ["--slug", "a"])
        check("exit 1", rc == 1, rc)
        check("no comment", comments == [])
        check("no close", calls == [])


def folder_of(tmp):
    return os.path.join(tmp, "results", "demo")


def two_workers(tmp, sealed):
    folder = folder_of(tmp)
    workers = [{"short_id": s, "slug": s, "result": worker_job.result_path(folder, s)} for s in ("a", "b")]
    return make_job(tmp, workers=workers, sealed=sealed)


def test_last_worker_resumes_once():
    print("test: the last worker comments, resumes the coordinator once, then closes")
    with tempfile.TemporaryDirectory() as tmp:
        folder = two_workers(tmp, sealed=True)
        write_result(folder, "a")
        rc, comments, calls = run_done(folder, ["--slug", "a"])
        check("first worker: no resume (b still missing)", "echo resume" not in calls, calls)
        check("first worker: closes", calls[-1][-1].endswith("end-session.py"), calls)
        check("first worker: commented", comments == [("card1", f"Worker a done - results at {worker_job.result_path(folder, 'a')}")], comments)
        write_result(folder, "b")
        rc, comments, calls = run_done(folder, ["--slug", "b"])
        check("last worker: resumed the coordinator", "echo resume" in calls, calls)
        rc, comments, calls = run_done(folder, ["--slug", "b"])
        check("a second finisher does not resume again", "echo resume" not in calls, calls)


def test_unsealed_job_never_resumes():
    print("test: an unsealed job is never resumed, even when every registered worker is done")
    with tempfile.TemporaryDirectory() as tmp:
        folder = two_workers(tmp, sealed=False)
        write_result(folder, "a")
        write_result(folder, "b")
        rc, comments, calls = run_done(folder, ["--slug", "a"])
        check("no resume", "echo resume" not in calls, calls)
        check("still closes", calls[-1][-1].endswith("end-session.py"), calls)


def test_trello_failure_not_fatal():
    print("test: a failed Trello comment does not stop the resume or the close")
    with tempfile.TemporaryDirectory() as tmp:
        folder = two_workers(tmp, sealed=True)
        write_result(folder, "a")
        write_result(folder, "b")
        rc, comments, calls = run_done(folder, ["--slug", "b"], comment_err="401")
        check("resumed and closed anyway", "echo resume" in calls and calls[-1][-1].endswith("end-session.py"), calls)


def test_seal():
    print("test: --seal marks the job and reports whether anyone is still running")
    with tempfile.TemporaryDirectory() as tmp:
        folder = two_workers(tmp, sealed=False)
        write_result(folder, "a")
        rc, _, _ = run_done(folder, ["--seal"])
        check("sealed in job.json", worker_job.read_job(folder)["sealed"] is True)
        check("missing lists b", worker_job.missing_results(worker_job.read_job(folder)) == ["b"])
        write_result(folder, "b")
        check("none missing once b reports", worker_job.missing_results(worker_job.read_job(folder)) == [])


def test_lock_is_exclusive():
    print("test: the resume lock goes to exactly one caller")
    with tempfile.TemporaryDirectory() as tmp:
        folder = make_job(tmp)
        check("first takes it", worker_job.take_resume_lock(folder))
        check("second does not", not worker_job.take_resume_lock(folder))


if __name__ == "__main__":
    test_result_status()
    test_spawn_worker_registers_and_appends_contract()
    test_spawn_worker_needs_job_and_fresh_launch()
    test_done_without_result_refuses()
    test_last_worker_resumes_once()
    test_unsealed_job_never_resumes()
    test_trello_failure_not_fatal()
    test_seal()
    test_lock_is_exclusive()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
