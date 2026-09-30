"""The job folder a coordinator and its workers share: `<main checkout>/.tmp/results/<job>/`.

    job.json        written by the coordinator: {"job": name, "card_id": Trello card or "",
                    "resume_command": the schedule-resume.py line that brings the coordinator back,
                    "sealed": false, "workers": []}; spawn-session.py --worker-of appends each worker
                    to "workers" as {"short_id", "slug", "result"}
    <slug>.md       one result file per worker, led by a `status: done | failed` front-matter line
    .resume.lock    created exclusively by whichever worker resumes the coordinator

Used by spawn-session.py (register a worker) and worker-done.py (check results, resume once).
"""
import json
import os
import re

JOB_FILE = "job.json"
LOCK_FILE = ".resume.lock"
_STATUS_RE = re.compile(r"^status:\s*(done|failed)\s*$", re.MULTILINE)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "worker"


def _job_path(folder):
    return os.path.join(folder, JOB_FILE)


def read_job(folder):
    with open(_job_path(folder), encoding="utf-8") as f:
        return json.load(f)


def write_job(folder, job):
    """Replace job.json atomically, so a reader never sees a half-written file."""
    tmp = _job_path(folder) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f, indent=2)
    os.replace(tmp, _job_path(folder))


def unique_slug(job, title):
    """A slug for this worker that no earlier worker in the job already holds."""
    taken = {w["slug"] for w in job.get("workers", [])}
    base = slugify(title)
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def result_path(folder, slug):
    return os.path.join(folder, f"{slug}.md")


def register_worker(folder, slug, short_id):
    job = read_job(folder)
    job.setdefault("workers", []).append(
        {"short_id": short_id, "slug": slug, "result": result_path(folder, slug)})
    write_job(folder, job)


def result_status(path):
    """'done' or 'failed' from the result file's front matter, or None when the file is missing or
    has no status line."""
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(2000)
    except OSError:
        return None
    m = _STATUS_RE.search(head)
    return m.group(1) if m else None


def missing_results(job):
    """Slugs of the workers that have not written a result with a status yet."""
    return [w["slug"] for w in job.get("workers", []) if result_status(w["result"]) is None]


def take_resume_lock(folder):
    """True for exactly one caller: the lock is an exclusive file create, so two workers finishing
    together resume the coordinator once."""
    try:
        fd = os.open(os.path.join(folder, LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.close(fd)
    return True
