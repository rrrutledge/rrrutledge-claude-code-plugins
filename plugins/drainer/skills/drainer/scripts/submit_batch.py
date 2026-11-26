"""Record what a staged browser submission shows and uploads, then check a re-staged one against it.

A worker runs this from engine/browser-gate.md's "A site that times out".

`save` writes the manifest: each --page file's JSON (the values one page shows or holds, in page order)
and the SHA-256 of each --file staged for upload, keyed by file name.
`check` builds the same thing from the re-staged pages and files and compares it with the manifest.
It prints MATCH and exits 0 only when every page and every file is identical; otherwise it prints one
line per difference and exits 1.
A page file holding no values is refused, so a capture that read nothing never counts as a match.

CLI:
    python submit_batch.py save  --manifest <path> --page <json> [--page <json> ...] [--file <path> ...]
    python submit_batch.py check --manifest <path> --page <json> [--page <json> ...] [--file <path> ...]
"""
import argparse
import hashlib
import json
import os
import sys
from collections import Counter

MISSING = "(absent)"


def build(pages, files):
    values = []
    for path in pages:
        with open(path, encoding="utf-8") as f:
            page = json.load(f)
        if not page:
            raise ValueError(f"{path} holds no values")
        values.append(page)
    hashes = []
    for path in files:
        with open(path, "rb") as f:
            hashes.append([os.path.basename(path), hashlib.sha256(f.read()).hexdigest()])
    return {"pages": values, "files": sorted(hashes)}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def differences(approved, fresh):
    found = []
    if len(approved["pages"]) != len(fresh["pages"]):
        found.append(f"page count: {len(approved['pages'])} -> {len(fresh['pages'])}")
    for number, (old, new) in enumerate(zip(approved["pages"], fresh["pages"]), 1):
        if _canonical(old) == _canonical(new):
            continue
        if not (isinstance(old, dict) and isinstance(new, dict)):
            found.append(f"page {number}: {_canonical(old)} -> {_canonical(new)}")
            continue
        for key in sorted(set(old) | set(new)):
            before = _canonical(old[key]) if key in old else MISSING
            after = _canonical(new[key]) if key in new else MISSING
            if before != after:
                found.append(f"page {number}, {key}: {before} -> {after}")
    old_files = Counter(map(tuple, approved["files"]))
    new_files = Counter(map(tuple, fresh["files"]))
    changed = (old_files - new_files) + (new_files - old_files)
    found.extend(f"file {name}: differs from the batch" for name in sorted({name for name, _ in changed}))
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
        with open(args.manifest, encoding="utf-8") as f:
            found = differences(json.load(f), batch)
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return 1
    print("\n".join(found) if found else "MATCH")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
