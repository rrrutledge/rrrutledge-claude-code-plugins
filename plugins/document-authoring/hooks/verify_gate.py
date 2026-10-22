#!/usr/bin/env python3
"""writing-review gate - makes the drafting loop's Verify step contractual.

A piece of writing cannot reach Russell until a fresh writing-review pass has run against
that exact content. The review flow proves it ran by minting a receipt keyed to a hash of
the reviewed file; this hook, at the moment the writing would reach him, recomputes that
hash and blocks unless a fresh receipt exists.

Two surfaces reach Russell, and the hook gates both against one receipt store:

  * An outward message reaches him when a stage command puts it where he'll send it. The
    gate keys on the body FILE that command passes (gmail.js / mail.js --body-file; ms-rest
    outlook-mail.js --json; a browser-chauffeur composer's declared --body-file).
  * Shipped prose (a SKILL.md, README, repo doc) reaches him when it lands in a PR he reads
    on the Files Changed tab. The gate keys on each changed markdown file in the PR diff,
    computed by the hook itself, and fires on `gh pr create` - the moment the PR opens and
    its prose becomes visible on Files Changed (a draft counts; Russell reviews drafts
    there). It does NOT fire on `gh pr ready` or `gh pr merge`, which act on an
    already-open PR whose prose he has already seen - readying and merging are the
    time-to-merge step, not a fresh prose-reaches-Russell moment.

Four modes in one file (single source of truth for the hash + receipt layout, so the
minting side and the gating side can never drift apart):

  * hook mode  - no args, reads a PreToolUse payload on stdin. Vetoes (deny) a stage
                 command whose body file has no fresh receipt, or a PR-open whose changed
                 prose files have no fresh receipt; defers otherwise.
  * mint <f>   - the review flow's final step: hash file <f>, write its receipt. Optional
                 `--rule-coverage <1-5>` and `--uncertain-spots-file <file>` record the final
                 review round's rating and unsettled spots on the receipt, for scoring.
  * check <f>  - exit 0 if <f> has a fresh receipt (or is a pure reaction), else exit 1.
                 For surfaces the hook can't see (Teams/Slack composers), where the flow
                 asserts the receipt itself rather than a stage command triggering the gate.
  * stage <f> --meta <m.json>
               - the hook's stage recording, for a script that sends from inside itself
                 where the hook can't see it: logs the stage and scores it before returning.

The gate is also the one place send-confidence
(`plugins/document-authoring/send-confidence/send_confidence.py`) scores a draft. For every
message stage it lets through, the hook appends a `staged` event (the body hash, the channel
and recipient the command carries, the session id) to the send-confidence log, and when that
hash has no `scored` row yet it launches a detached scorer and returns at once. The scorer
takes the reviewer's rule coverage and uncertain spots off the receipt and the ask and inputs
from the session transcript the payload names. Both steps are fail-open and add no API call to
the hook itself; the score never surfaces per draft, only in the weekly trend.
`SEND_CONFIDENCE_AUTO=off` switches both off.

Why the binding is exact: the reviewer reviews a file, the flow mints on that file, and the
gate rehashes that same file - the message body the stage command consumes, or the
working-tree markdown file the PR diff names. Hashing the file's content matches on both
sides with no per-format normalization. Edit the file and the hash changes, so a stale
review can't satisfy the gate. The receipt attests that a review ran, not that it was clean
- the writer's standing-to-disagree is preserved; this enforces process, not verdict.

The hook is a pure veto: it only ever denies or defers, never approves, so it composes
with safe-compounds without granting anything. On any internal error it defers (fail-open)
- it must never be the mysterious reason a legitimate stage or PR fails; its job is catching
the common case of no review at all, and the deny message always spells out the fix.
"""

import sys
import os
import re
import json
import time
import shlex
import hashlib
import datetime
import subprocess

RECEIPT_DIR = os.environ.get("WRITING_REVIEW_RECEIPT_DIR") or os.path.expanduser(
    "~/.claude/writing-review/receipts"
)
TTL_SECONDS = 24 * 3600

# Known stage commands, keyed by script basename. A command is a stage command when it
# names one of these scripts, carries one of its compose verbs, and supplies its body flag.
# Send-of-a-staged-draft (gmail --send-draft, outlook-mail send-draft) carries no body and
# is intentionally absent - that draft was already gated at stage time, and the send is
# Russell's explicit act. ms-graph --send-self composes fresh into a body file, so it is
# gated like a stage. slack.js --send carries the body it posts (like --send-self) rather
# than referencing an already-gated draft - so it is gated here, and the receipt binds the
# exact bytes chat.postMessage emits. slack.js --create-draft stages a server-side draft
# from a body file, so it is gated like any other stage.
STAGE_SCRIPTS = {
    "gmail.js": {"verbs": {"--reply", "--draft-new"}, "body_flag": "--body-file"},
    "mail.js": {"verbs": {"--reply", "--draft-new", "--send-self"}, "body_flag": "--body-file"},
    "outlook-mail.js": {"verbs": {"create-draft", "create-reply"}, "body_flag": "--json"},
    "slack.js": {"verbs": {"--send", "--create-draft"}, "body_flag": "--body-file"},
}

# Native Gmail connector compose/send verbs. Staging Russell's mail this way bypasses both
# this gate and the gmail skill's signature/threading handling, so it is denied outright
# with a redirect. Read-only Gmail verbs (search/get/list/label/...) are untouched.
GMAIL_MCP_WRITE = {
    "mcp__claude_ai_Gmail__create_draft",
    "mcp__claude_ai_Gmail__update_draft",
    "mcp__claude_ai_Gmail__reply",
    "mcp__claude_ai_Gmail__forward",
    "mcp__claude_ai_Gmail__send_message",
}


# --- shared hash + receipt layout (used identically by hook, mint, and check) ---

def normalized_text(path):
    """Read a body file as the canonical text both sides hash. Line endings are unified
    and surrounding whitespace trimmed so a stray CRLF or trailing newline can't split a
    genuine match; nothing else is altered, so any prose change moves the hash."""
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def has_prose(text):
    """A body with no alphabetic character is a pure emoji/symbol reaction (a bare 👍),
    which has no prose to review and is exempt from the gate."""
    return re.search(r"[A-Za-z]", text) is not None


def receipt_path(h):
    return os.path.join(RECEIPT_DIR, h + ".json")


def has_fresh_receipt(h):
    path = receipt_path(h)
    if not os.path.isfile(path):
        return False
    return (time.time() - os.path.getmtime(path)) <= TTL_SECONDS


# --- command parsing ---

def find_body_file(command):
    """Return the body-file path if `command` is a recognized stage command, else None."""
    stage = find_stage(command)
    return stage["body_path"] if stage else None


def find_stage(command):
    """Return {"script", "verb", "args", "tokens", "body_path"} if `command` is a recognized
    stage command, else None. `script` is None for a browser-chauffeur composer run.

    Two shapes count as a stage command. A named script (gmail.js / mail.js /
    outlook-mail.js / slack.js) carrying its compose verb and body flag is one. A browser-chauffeur
    composer run is the other: a `--cdp-port` browser session that declares the message it
    will type via `--body-file`. The Slack and Teams composers type into a web page rather
    than into a script argument, so message-draft routes their body through a declared file,
    which is what lets this gate reach them the same way it reaches mail. A browser-chauffeur
    run that carries no `--body-file` (a screenshot, a scrape) is not a stage command and
    passes straight through."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        base = tok.replace("\\", "/").rsplit("/", 1)[-1]
        spec = STAGE_SCRIPTS.get(base)
        if not spec:
            continue
        rest = tokens[i + 1:]
        verb = next((t for t in rest if t in spec["verbs"]), None)
        if not verb:
            continue
        path = extract_flag_value(rest, spec["body_flag"])
        if path:
            return {"script": base, "verb": verb, "args": rest, "tokens": tokens,
                    "body_path": path}
    if any(t == "--cdp-port" or t.startswith("--cdp-port=") for t in tokens):
        path = extract_flag_value(tokens, "--body-file")
        if path:
            return {"script": None, "verb": None, "args": tokens, "tokens": tokens,
                    "body_path": path}
    return None


def extract_flag_value(tokens, flag):
    """Read a flag value in either `--flag=value` or `--flag value` form."""
    prefix = flag + "="
    for j, tok in enumerate(tokens):
        if tok == flag and j + 1 < len(tokens):
            return tokens[j + 1]
        if tok.startswith(prefix):
            return tok[len(prefix):]
    return None


# --- PR prose gate: shipped prose reaches Russell when a PR opens ---

# A control operator ends the previous command, so a `gh` right after one is a fresh
# command rather than an argument. shlex keeps these as their own tokens, so a quoted
# "gh pr create" inside another command (a grep pattern, an echo) stays one token and
# never matches - it is not at a command position.
_COMMAND_BOUNDARIES = {"&&", "||", ";", "|", "(", "{"}


def find_gh_pr_action(command):
    """Return ('create', args_after_the_verb) if `command` opens a PR, else None. Only a
    `gh pr create` at a command position counts. `gh pr ready` and `gh pr merge` act on an
    already-open PR - the prose reached Russell when it was created - so they are not gated
    here; the create is the one prose-reaches-Russell moment."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        if tok != "gh":
            continue
        if i > 0 and tokens[i - 1] not in _COMMAND_BOUNDARIES:
            continue
        rest = tokens[i + 1:]
        if len(rest) >= 2 and rest[0] == "pr" and rest[1] == "create":
            return rest[1], rest[2:]
    return None


def is_gated_prose(rel_path):
    """A changed file counts as shipped prose when it is a markdown file that ships and
    stays. Exclude `.tmp/` (plans, specs, handoffs, scratch) and a top-level `handoffs/`
    directory - those are change-explanations, reviewed against different rules, so gating
    them against the shipped-artifact rubric would false-block. A commit message and a PR
    body are change-explanations too, and never appear as files in a diff, so they need no
    exclusion here."""
    p = rel_path.replace("\\", "/")
    if not p.lower().endswith(".md"):
        return False
    segments = p.split("/")
    if ".tmp" in segments:
        return False
    if segments[0] == "handoffs":
        return False
    return True


def git_output(args):
    """Run a git command in the current working directory and return its stdout, raising on
    any non-zero exit so the caller can fail open."""
    result = subprocess.run(
        ["git", *args], cwd=os.getcwd(), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def _ref_exists(ref):
    try:
        git_output(["rev-parse", "--verify", "--quiet", ref])
        return True
    except Exception:
        return False


def normalize_repo(value):
    """Reduce a repo reference to a lowercase `owner/repo` slug, or None if it doesn't look
    like one. Handles every shape `gh --repo` accepts and every shape a git remote URL takes:
    bare `owner/repo` shorthand, gh's `[HOST/]owner/repo`, an https URL, and an scp-style ssh
    URL (`git@github.com:owner/repo.git`). The host is dropped so an https flag value matches
    an ssh remote for the same repo; the trailing `.git` and any surrounding slashes are
    stripped, and the last two path segments are taken as owner and repo."""
    if not value:
        return None
    v = value.strip()
    scp = re.match(r"^[^@/]+@[^:/]+:(.+)$", v)
    if scp:
        v = scp.group(1)
    else:
        v = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", v)
    if v.endswith(".git"):
        v = v[:-4]
    parts = [p for p in v.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[-2], parts[-1]
    if not owner or not repo:
        return None
    return f"{owner.lower()}/{repo.lower()}"


def cwd_repo_slugs():
    """Normalized `owner/repo` slugs for every git remote of the cwd's repo, empty on any
    failure (not a repo, no remotes, git error) so the caller fails open."""
    try:
        out = git_output(["remote", "-v"])
    except Exception:
        return set()
    slugs = set()
    for line in out.splitlines():
        cols = line.split()
        if len(cols) >= 2:
            slug = normalize_repo(cols[1])
            if slug:
                slugs.add(slug)
    return slugs


def repo_flag_targets_other_repo(args):
    """True when a `--repo`/`-R` flag names a repo other than the cwd's own - the only case
    where the cwd diff would gate the wrong repo's prose, so the PR gate must defer. Absent
    the flag, or when it names this same repo (matching any of the cwd's remotes), returns
    False so the gate runs on the cwd diff. Fails toward deferring (True) when the flag is
    present but the flag value can't be parsed or the cwd's own repo can't be resolved, so an
    unverifiable target never gates possibly-wrong prose."""
    value = extract_flag_value(args, "--repo") or extract_flag_value(args, "-R")
    if not value:
        return False
    target = normalize_repo(value)
    if not target:
        return True
    own = cwd_repo_slugs()
    if not own:
        return True
    return target not in own


def resolve_pr_base(args):
    """The ref the PR diffs against, matching what GitHub shows. `gh pr create --base <b>`
    diffs against the branch <b> on the remote, so a named base resolves to its
    remote-tracking ref (`origin/<b>`) when that exists, falling back to a local branch of
    that name only when there is no remote - this is what keeps the gate honest when the
    local `main` lags `origin/main`. With no --base, use the remote's default branch. Returns
    None when nothing resolves (fail-open)."""
    name = extract_flag_value(args, "--base") or extract_flag_value(args, "-B")
    if name:
        for candidate in (f"origin/{name}", name):
            if _ref_exists(candidate):
                return candidate
        return None
    try:
        ref = git_output(["rev-parse", "--abbrev-ref", "origin/HEAD"]).strip()
        if ref and _ref_exists(ref):
            return ref
    except Exception:
        pass
    for candidate in ("origin/main", "main"):
        if _ref_exists(candidate):
            return candidate
    return None


def changed_prose_files(base):
    """Repo-root-relative paths of the shipped-prose markdown files the PR adds or changes,
    against `base` at the merge base (three-dot, matching the diff GitHub shows). Deletions
    are excluded - a removed file has no content to review."""
    out = git_output(
        ["diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"]
    )
    return [line.strip() for line in out.splitlines()
            if line.strip() and is_gated_prose(line.strip())]


# --- hook decision output (the PreToolUse contract) ---

def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def defer():
    sys.exit(0)


def stage_block_reason(body_path, h):
    return (
        "Blocked: staging this draft needs a fresh writing-review pass first.\n"
        f"The body file {body_path} has no review receipt (content {h[:12]}...).\n"
        "Do this, then re-run the stage command unchanged:\n"
        "  1. Dispatch the writing-review skill on this exact body file - a fresh "
        "subagent that did not write the draft, per the drafting loop's Verify step.\n"
        "  2. When the review loop converges, mint the receipt:\n"
        f'     python "{os.path.abspath(__file__)}" mint "{body_path}"\n'
        "The receipt binds to the file's content, so any later edit needs a fresh review "
        "and mint. A pure emoji/reaction body (no letters) never reaches this gate."
    )


def pr_block_reason(files):
    script = os.path.abspath(__file__)
    lines = [
        "Blocked: opening this PR needs a fresh writing-review pass on its changed prose.",
        "Russell reviews a PR on the Files Changed tab, so shipped prose reaches him the "
        "moment the PR opens - the same guarantee the message gate gives outward mail.",
        "These markdown files in the PR diff have no review receipt for their current content:",
    ]
    for f in files:
        lines.append(f"  - {f}")
    lines.append("For each, do this, then re-run the gh command unchanged:")
    lines.append(
        "  1. Dispatch the writing-review skill on the file as a fresh subagent that did "
        "not write it, per writing-flow's Review step."
    )
    lines.append("  2. When the review converges, mint its receipt:")
    for f in files:
        lines.append(f'     python "{script}" mint "{f}"')
    lines.append(
        "The receipt binds to the file's content, so any later edit needs a fresh review "
        "and mint. `.tmp/` files, plans, and handoffs are change-explanations and never "
        "reach this gate."
    )
    return "\n".join(lines)


MCP_REDIRECT_REASON = (
    "Blocked: staging or sending Russell's mail through the native Gmail connector bypasses "
    "the writing-review gate and the gmail skill's signature/threading handling. Stage "
    "through the gmail skill's gmail.js instead: write the body to a file, dispatch "
    "writing-review on it, mint a receipt, then "
    "`node <gmail.js> --reply --message-id=<id> --body-file=<file>` (or --draft-new). "
    "Read-only Gmail lookups (search/get/list) stay fine through the connector."
)


def resolve_stage(command):
    """The stage command's parsed shape plus its body's text and hash, or None when the
    command is not a stage, its body file is missing, or the body is a pure reaction."""
    stage = find_stage(command)
    if not stage:
        return None
    body_path = stage["body_path"]
    if not os.path.isabs(body_path):
        body_path = os.path.join(os.getcwd(), body_path)
    if not os.path.isfile(body_path):
        return None
    text = normalized_text(body_path)
    if not has_prose(text):
        return None
    return {**stage, "body_path": body_path, "text": text, "sha256": content_hash(text)}


def message_stage_deny_reason(stage):
    """Deny-reason for a resolved stage lacking a fresh receipt, else None (defer)."""
    if not stage or has_fresh_receipt(stage["sha256"]):
        return None
    return stage_block_reason(stage["body_path"], stage["sha256"])


# --- send-confidence: every stage that passes is logged and scored ---

# The channel each stage script reaches, in send-confidence's channel vocabulary.
STAGE_CHANNELS = {
    "gmail.js": "gmail",
    "mail.js": "outlook-personal",
    "outlook-mail.js": "outlook-work",
    "slack.js": "slack",
}

# Verbs that post the body directly rather than leaving a draft for Russell to send.
DIRECT_SEND_VERBS = {("slack.js", "--send"), ("mail.js", "--send-self")}


def stage_meta(stage, payload):
    """What the stage command itself says about the draft: channel, account, recipient, and
    thread, read off its arguments (and, for work Outlook, its JSON payload). Anything the
    command doesn't carry comes back None rather than being guessed."""
    script, verb, args = stage["script"], stage["verb"], stage["args"]
    meta = {
        "script": script, "verb": verb,
        "channel": STAGE_CHANNELS.get(script, "other"),
        "account": None, "recipient": None, "thread_ref": None,
        "stage_kind": "send" if (script, verb) in DIRECT_SEND_VERBS else "draft",
        "body_format": "text",
        "session_id": payload.get("session_id"),
        "session_kind": "background" if os.environ.get("CLAUDE_JOB_DIR") else "live",
        "transcript_path": payload.get("transcript_path"),
    }
    receipt = read_receipt(stage["sha256"])
    meta["rule_coverage"] = receipt.get("rule_coverage")
    meta["uncertain_spots"] = receipt.get("uncertain_spots")
    if script in ("gmail.js", "mail.js"):
        meta["account"] = extract_flag_value(args, "--account")
        meta["recipient"] = extract_flag_value(args, "--to")
        meta["thread_ref"] = extract_flag_value(args, "--message-id")
        if verb == "--send-self":
            meta["recipient"] = "self"
    elif script == "slack.js":
        meta["recipient"] = extract_flag_value(args, "--channel")
        meta["thread_ref"] = extract_flag_value(args, "--thread-ts")
    elif script == "outlook-mail.js":
        meta["body_format"] = "outlook-json"
        i = args.index(verb)
        if verb == "create-reply" and i + 1 < len(args) and not args[i + 1].startswith("-"):
            meta["thread_ref"] = args[i + 1]
        try:
            to = json.loads(stage["text"]).get("to")
            if isinstance(to, list) and to:
                meta["recipient"] = ",".join(str(t) for t in to)
            elif isinstance(to, str) and to:
                meta["recipient"] = to
        except Exception:
            pass
    else:
        joined = " ".join(stage["tokens"]).lower()
        meta["channel"] = "teams" if "teams" in joined else "slack" if "slack" in joined else "other"
    return meta


def record_stage(stage, payload):
    """Log the stage to send-confidence and queue a background score when the draft has no
    scored row yet. Fail-open: any error here leaves the stage untouched."""
    if os.environ.get("SEND_CONFIDENCE_AUTO") == "off":
        return
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "send-confidence"))
        import send_confidence
        send_confidence.record_stage(stage["text"], stage["sha256"], stage_meta(stage, payload))
    except Exception:
        pass


def pr_prose_deny_reason(command):
    """Deny-reason for a `gh pr create` whose changed prose files lack fresh receipts, else
    None (defer): not a PR-open, no prose changed, all reviewed, or any git step failed
    (fail-open). Reads each changed file from the working tree, which equals the committed
    content the PR diff names when the tree is clean at PR time.

    The diff runs in the current directory's repo. A `-R`/`--repo` flag lets the PR target a
    repo named on the command line, which need only differ from the cwd's repo when it names
    a genuinely other one (e.g. `gh pr create -R owner/other` run from an unrelated checkout) -
    there the cwd diff would gate the wrong repo's prose, so defer. When the flag names this
    same repo (the common defensive-habit case of passing the cwd's own repo redundantly), the
    cwd diff is exactly the PR's diff, so gate it normally."""
    action = find_gh_pr_action(command)
    if not action:
        return None
    _verb, args = action
    if repo_flag_targets_other_repo(args):
        return None
    try:
        base = resolve_pr_base(args)
        if not base:
            return None
        root = git_output(["rev-parse", "--show-toplevel"]).strip()
        files = changed_prose_files(base)
    except Exception:
        return None
    unreviewed = []
    for rel in files:
        abspath = os.path.join(root, rel)
        try:
            text = normalized_text(abspath)
        except Exception:
            continue
        if not has_prose(text):
            continue
        if has_fresh_receipt(content_hash(text)):
            continue
        unreviewed.append(rel)
    if unreviewed:
        return pr_block_reason(unreviewed)
    return None


def run_hook():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = data.get("tool_name", "") or ""
    tool_input = data.get("tool_input", {}) or {}
    try:
        if tool == "Bash":
            command = tool_input.get("command", "") or ""
            stage = resolve_stage(command)
            reason = message_stage_deny_reason(stage)
            if reason is None:
                reason = pr_prose_deny_reason(command)
            if reason:
                deny(reason)
            if stage:
                record_stage(stage, data)
            defer()
        elif tool.startswith("mcp__"):
            if tool in GMAIL_MCP_WRITE:
                deny(MCP_REDIRECT_REASON)
            defer()
        else:
            defer()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)


# --- mint / check subcommands ---

def cmd_mint(path, verdict, rule_coverage=None, uncertain_spots_file=None):
    if not path or not os.path.isfile(path):
        print(f"mint: body file not found: {path}", file=sys.stderr)
        return 2
    if rule_coverage is not None and rule_coverage not in {"1", "2", "3", "4", "5"}:
        print("mint: --rule-coverage must be 1-5", file=sys.stderr)
        return 2
    uncertain_spots = None
    if uncertain_spots_file:
        try:
            with open(uncertain_spots_file, "r", encoding="utf-8") as fh:
                uncertain_spots = fh.read().strip() or None
        except OSError as e:
            print(f"mint: could not read --uncertain-spots-file ({e})", file=sys.stderr)
            return 2
    text = normalized_text(path)
    h = content_hash(text)
    os.makedirs(RECEIPT_DIR, exist_ok=True)
    receipt = {
        "sha256": h,
        "source_file": os.path.abspath(path),
        "created_at": datetime.datetime.now().astimezone().isoformat(),
        "verdict": verdict or "reviewed",
        "chars": len(text),
        "rule_coverage": int(rule_coverage) if rule_coverage is not None else None,
        "uncertain_spots": uncertain_spots,
    }
    with open(receipt_path(h), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2)
    print(f"writing-review receipt minted: {h[:12]}... for {path}")
    return 0


def cmd_stage(path, meta_path):
    """Record a stage for a script that sends from inside itself, out of the hook's sight, and
    score it before returning so the script can record the outcome next. `meta_path` is a JSON
    file of what the script knows: channel, recipient, thread_ref, stage_kind, session_id, and
    optionally the ask and inputs. Exit 0 always - scoring never blocks the send."""
    try:
        if os.environ.get("SEND_CONFIDENCE_AUTO") == "off" or not path or not os.path.isfile(path):
            return 0
        text = normalized_text(path)
        if not has_prose(text):
            return 0
        meta = {}
        if meta_path:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        h = content_hash(text)
        receipt = read_receipt(h)
        meta.setdefault("body_format", "text")
        meta["rule_coverage"] = receipt.get("rule_coverage")
        meta["uncertain_spots"] = receipt.get("uncertain_spots")
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "send-confidence"))
        import send_confidence
        send_confidence.record_stage(text, h, meta, wait=True)
        print(f"send-confidence: staged {h[:12]}...")
    except Exception as e:
        print(f"send-confidence: not staged ({e})", file=sys.stderr)
    return 0


def read_receipt(h):
    """The receipt's JSON, or {} when it is missing or unreadable."""
    try:
        with open(receipt_path(h), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def cmd_check(path):
    if not path or not os.path.isfile(path):
        print(f"check: body file not found: {path}", file=sys.stderr)
        return 2
    text = normalized_text(path)
    if not has_prose(text):
        print("check: no prose (pure reaction) - no receipt required")
        return 0
    h = content_hash(text)
    if has_fresh_receipt(h):
        print(f"check: OK - fresh receipt for {h[:12]}...")
        return 0
    print(
        f"check: NO fresh receipt for {path} (content {h[:12]}...). "
        "Dispatch writing-review on this file, then mint.",
        file=sys.stderr,
    )
    return 1


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "mint":
        rest = argv[1:]
        values = {}
        for flag in ("--verdict", "--rule-coverage", "--uncertain-spots-file", "--score-context"):
            if flag in rest:
                i = rest.index(flag)
                values[flag] = rest[i + 1] if i + 1 < len(rest) else None
                rest = rest[:i] + rest[i + 2:]
        if "--score-context" in values:
            print("mint: --score-context is ignored - the stage gate scores every staged draft",
                  file=sys.stderr)
        sys.exit(cmd_mint(rest[0] if rest else None, values.get("--verdict"),
                          values.get("--rule-coverage"), values.get("--uncertain-spots-file")))
    if argv and argv[0] == "stage":
        rest = argv[1:]
        meta_path = None
        if "--meta" in rest:
            i = rest.index("--meta")
            meta_path = rest[i + 1] if i + 1 < len(rest) else None
            rest = rest[:i] + rest[i + 2:]
        sys.exit(cmd_stage(rest[0] if rest else None, meta_path))
    if argv and argv[0] == "check":
        sys.exit(cmd_check(argv[1] if len(argv) > 1 else None))
    run_hook()


if __name__ == "__main__":
    main()
