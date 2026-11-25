"""Helpers shared by golden generation and the characterization test."""
import json
import os


def canonical(stdout_text):
    """Reduce a hook's stdout to ALLOW / BLOCK / PROMPT.

    Accepts both the modern hookSpecificOutput schema and the legacy
    decision:approve/block schema so the same function scores both hooks.
    """
    text = (stdout_text or "").strip()
    if not text:
        return "PROMPT"
    try:
        obj = json.loads(text)
    except Exception:
        return "PROMPT"
    hso = obj.get("hookSpecificOutput")
    if isinstance(hso, dict):
        d = hso.get("permissionDecision")
        if d == "allow":
            return "ALLOW"
        if d == "deny":
            return "BLOCK"
        if d == "ask":
            return "PROMPT"
    if obj.get("decision") == "approve":
        return "ALLOW"
    if obj.get("decision") == "block":
        return "BLOCK"
    return "PROMPT"


def ensure_fixtures(root):
    """Create the git/plain/home fixture dirs under `root` and return them."""
    git_dir = os.path.join(root, "gitrepo")
    plain_dir = os.path.join(root, "plain")
    home_dir = os.path.join(root, "home")
    for d in (git_dir, plain_dir, home_dir):
        os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(git_dir, ".git"), exist_ok=True)
    os.makedirs(os.path.join(git_dir, ".tmp"), exist_ok=True)
    os.makedirs(os.path.join(plain_dir, ".tmp"), exist_ok=True)
    return git_dir, plain_dir, home_dir


def setup_case(root, case):
    """Materialize a case under `root`. Returns (cwd_path, payload dict)."""
    git_dir, plain_dir, home_dir = ensure_fixtures(root)
    cwd_path = git_dir if case.get("cwd", "git") == "git" else plain_dir
    systmp_dir = os.path.join(root, "systmp")
    os.makedirs(systmp_dir, exist_ok=True)

    for rel, content in (case.get("systmp_files") or {}).items():
        full = os.path.join(systmp_dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    for rel, content in (case.get("files") or {}).items():
        full = os.path.join(cwd_path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    for rel, content in (case.get("home_files") or {}).items():
        full = os.path.join(home_dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def resolve(value):
        return value.replace("{CWD}", cwd_path).replace("{HOME}", home_dir).replace("{SYSTMP}", systmp_dir)

    payload = {"tool_name": case["tool"], "tool_input": {}}
    if "command" in case:
        payload["tool_input"]["command"] = resolve(case["command"])
    if "file_path" in case:
        payload["tool_input"]["file_path"] = resolve(case["file_path"])
    if "name" in case:
        payload["tool_input"]["name"] = case["name"]
    if "script" in case:
        payload["tool_input"]["script"] = case["script"]
    return cwd_path, payload
