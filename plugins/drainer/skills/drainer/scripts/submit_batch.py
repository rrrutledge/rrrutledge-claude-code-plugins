"""Record what a staged browser submission shows and uploads, then check a re-staged one against it.

A worker runs this from engine/browser-gate.md's "A site that times out".

`save` writes the manifest: each --page file's JSON (a flat object of the values one page shows or
holds, in page order) and the name and SHA-256 of each --file staged for upload, in upload order.
`check` builds the same thing from the re-staged pages and files and compares it with the manifest.
It prints MATCH and exits 0 only when every page and every file is identical; otherwise it prints one
line per difference and exits 1.
A capture that could pass without proving anything is refused: a page that is not a flat object with at
least one value, and, on `check`, a page file written no later than the manifest.

CLI:
    python submit_batch.py save  --manifest <path> --page <json> [--page <json> ...] [--file <path> ...]
    python submit_batch.py check --manifest <path> --page <json> [--page <json> ...] [--file <path> ...]
"""
import argparse
import hashlib
import json
import os
import sys

MISSING = "(absent)"


def build(pages, files):
    values = []
    for path in pages:
        with open(path, encoding="utf-8") as f:
            page = json.load(f)
        if (not isinstance(page, dict) or any(isinstance(v, (dict, list)) for v in page.values())
                or all(v in (None, "") for v in page.values())):
            raise ValueError(f"{path} must be a flat object holding at least one value")
        values.append(page)
    hashes = []
    for path in files:
        with open(path, "rb") as f:
            hashes.append([os.path.basename(path), hashlib.sha256(f.read()).hexdigest()])
    return {"pages": values, "files": hashes}


def _shown(page, key):
    return json.dumps(page[key], ensure_ascii=False) if key in page else MISSING


def _listing(files):
    return ", ".join(f"{name} ({digest[:8]})" for name, digest in files) or MISSING


def differences(approved, fresh):
    found = []
    if len(approved["pages"]) != len(fresh["pages"]):
        found.append(f"page count: {len(approved['pages'])} -> {len(fresh['pages'])}")
    for number, (old, new) in enumerate(zip(approved["pages"], fresh["pages"]), 1):
        for key in sorted(set(old) | set(new)):
            if _shown(old, key) != _shown(new, key):
                found.append(f"page {number}, {key}: {_shown(old, key)} -> {_shown(new, key)}")
    if approved["files"] != fresh["files"]:
        found.append(f"files: {_listing(approved['files'])} -> {_listing(fresh['files'])}")
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="Save or check a staged submission's batch.")
    parser.add_argument("command", choices=("save", "check"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--page", action="append", required=True)
    parser.add_argument("--file", action="append", default=[])
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        batch = build(args.page, args.file)
        if args.command == "save":
            with open(args.manifest, "w", encoding="utf-8") as f:
                json.dump(batch, f, indent=2, ensure_ascii=False)
            print(f"saved {len(batch['pages'])} page(s) and {len(batch['files'])} file(s) to {args.manifest}")
            return 0
        saved_at = os.path.getmtime(args.manifest)
        stale = [path for path in args.page if os.path.getmtime(path) <= saved_at]
        if stale:
            raise ValueError(f"captured before the batch was saved: {', '.join(stale)}")
        with open(args.manifest, encoding="utf-8") as f:
            found = differences(json.load(f), batch)
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return 1
    print("\n".join(found) if found else "MATCH")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
